"""Closed-Loop Payment Wallet network adapter implementation for Ripple.

Translates proprietary closed-loop digital wallet payment and snapshot events
into Ripple's universal domain model. Adheres strictly to the four adapter rules:
- Pure: zero I/O, zero clock reads (observed_at sourced exclusively from event.received_at).
- Deterministic: transaction_id and party_id derived via deterministic_uuid().
- Never silently drop data: non-universal attributes preserved in metadata/attributes.
- Typed errors: raises NormalizationError or UnsupportedPayloadError on invalid inputs.
"""

import copy
import logging
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.currencies import lookup_currency_by_alpha3
from ripple.adapters.errors import (
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.helpers import (
    parse_amount,
    parse_datetime,
    payload_as_dict,
    require,
    wrap_domain_errors,
)
from ripple.adapters.profile import NetworkProfile
from ripple.adapters.registry import register_adapter
from ripple.adapters.wallet.profile import WALLET_PROFILE
from ripple.domain.identity import (
    _EMAIL_PATTERN,
    _PHONE_PATTERN,
    IdentifierType,
    PartyIdentifier,
)
from ripple.domain.money import AssetClass, Money
from ripple.domain.party import KycLevel, Party, PartyType, RiskFlags
from ripple.domain.transaction import (
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

logger = logging.getLogger(__name__)

_SUPPORTED_EVENT_TYPES = {
    "p2p",
    "merchant_payment",
    "funding",
    "withdrawal",
    "refund",
    "dispute",
    "reversal",
}

_SUPPORTED_ORIGINAL_EVENT_TYPES = {
    "p2p",
    "merchant_payment",
    "funding",
    "withdrawal",
}

_SUPPORTED_STATUSES = {
    "PENDING",
    "COMPLETED",
    "DENIED",
    "FAILED",
    "HELD",
    "CANCELLED",
    "EXPIRED",
}

_SUPPORTED_DISPUTE_STATUSES = {
    "opened",
    "escalated",
    "resolved_buyer",
    "resolved_seller",
    "withdrawn",
}

_SUPPORTED_DISPUTE_REASONS = {
    "item_not_received",
    "unauthorized",
    "not_as_described",
    "duplicate",
    "other",
}

_SUPPORTED_REVERSAL_REASONS = {
    "funding_failed",
    "fraud",
    "operator_error",
    "other",
}

_CREDENTIAL_FIELDS = {
    "pan",
    "card_number",
    "cvv",
    "cvc",
    "expiry",
    "iban",
    "account_number",
    "routing_number",
    "sort_code",
    "ssn",
    "tax_id",
    "national_id",
    "passport_number",
    "password",
    "otp",
    "pin",
    "security_answer",
}

_ORDINARY_PII_FIELDS = {
    "name",
    "first_name",
    "last_name",
    "address",
    "date_of_birth",
    "note",
    "memo",
}

_KNOWN_TOP_LEVEL_KEYS = {
    "event_type",
    "txn_id",
    "parent_txn_id",
    "original_event_type",
    "status",
    "flow",
    "requested_at",
    "approved_at",
    "sender",
    "receiver",
    "account",
    "instrument",
    "amount",
    "currency",
    "converted",
    "fee",
    "fee_payer",
    "created_at",
    "completed_at",
    "original_completed_at",
    "payment_type",
    "buyer_protection",
    "reversal_window_days",
    "hold_reason",
    "denial_reason",
    "failure_reason",
    "funding_source",
    "funding_instrument_id",
    "provisional_credit",
    "case_id",
    "dispute_status",
    "dispute_reason",
    "opened_at",
    "resolved_at",
    "reversal_reason",
    "reversed_at",
    "channel",
    "device_id",
    "ip_address",
    "ip_country",
    "source",
    "note",
}

_INSTRUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{6,64}$")


def _sanitize_credentials(obj: Any, path: str = "") -> tuple[Any, list[str]]:
    """Recursively strip credential-class fields from payload and generate warnings."""
    warnings: list[str] = []
    if isinstance(obj, dict):
        clean_dict: dict[str, Any] = {}
        for k, v in obj.items():
            k_str = str(k)
            k_lower = k_str.lower()
            field_path = f"{path}.{k_str}" if path else k_str
            if k_lower in _CREDENTIAL_FIELDS:
                warnings.append(
                    f"Sensitive credential field '{field_path}' was detected and discarded."
                )
            else:
                sub_val, sub_warns = _sanitize_credentials(v, field_path)
                warnings.extend(sub_warns)
                clean_dict[k_str] = sub_val
        return clean_dict, warnings
    elif isinstance(obj, list):
        clean_list: list[Any] = []
        for idx, item in enumerate(obj):
            sub_val, sub_warns = _sanitize_credentials(item, f"{path}[{idx}]")
            warnings.extend(sub_warns)
            clean_list.append(sub_val)
        return clean_list, warnings
    return obj, warnings


@register_adapter
class WalletAdapter(NetworkAdapter):
    """Adapter translating Ripple Closed-Loop Payment Wallet events into the universal model."""

    profile: ClassVar[NetworkProfile] = WALLET_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"
    discarded_fields: ClassVar[list[str]] = sorted(_ORDINARY_PII_FIELDS)

    def can_handle(self, event: RawEvent) -> bool:
        """Determine if this adapter handles the incoming event."""
        return event.network_id.lower() == "wallet"

    def describe(self) -> dict[str, Any]:
        """Describe adapter capabilities and discarded fields."""
        data = super().describe()
        data["discarded_fields"] = list(self.discarded_fields)
        return data

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Normalize a closed-loop wallet event into Ripple's universal domain model."""
        raw_payload = payload_as_dict(event)
        # Deep copy to guarantee input immutability
        payload_copy = copy.deepcopy(raw_payload)

        # 1. Recursive credential scan & discard
        clean_payload, warnings = _sanitize_credentials(payload_copy)

        # 2. Extract and validate mandatory event_type
        raw_event_type = require(clean_payload, "event_type", "event_type")
        event_type = str(raw_event_type).strip().lower()
        if event_type not in _SUPPORTED_EVENT_TYPES:
            raise UnsupportedPayloadError(
                f"Unsupported wallet event_type '{event_type}'. "
                f"Supported types: {', '.join(sorted(_SUPPORTED_EVENT_TYPES))}."
            )

        # 3. Extract txn_id
        txn_id = str(require(clean_payload, "txn_id", "txn_id")).strip()
        if not txn_id:
            raise NormalizationError(
                "Field 'txn_id' cannot be blank.",
                field="txn_id",
                reason="Blank txn_id",
            )

        parent_txn_id: str | None = None
        if event_type == "refund":
            parent_txn_id = str(require(clean_payload, "parent_txn_id", "parent_txn_id")).strip()
            if not parent_txn_id:
                raise NormalizationError(
                    "Field 'parent_txn_id' is required for refund events.",
                    field="parent_txn_id",
                    reason="Missing or blank parent_txn_id",
                )

        # 4. original_event_type for dispute and reversal
        original_event_type: str | None = None
        if event_type in {"dispute", "reversal"}:
            raw_orig = require(clean_payload, "original_event_type", "original_event_type")
            original_event_type = str(raw_orig).strip().lower()
            if original_event_type not in _SUPPORTED_ORIGINAL_EVENT_TYPES:
                raise UnsupportedPayloadError(
                    f"Unsupported original_event_type '{original_event_type}'. "
                    f"Supported: {', '.join(sorted(_SUPPORTED_ORIGINAL_EVENT_TYPES))}."
                )

        # 5. Extract and validate status
        status_str: str | None = None
        if event_type in {"p2p", "merchant_payment", "funding", "withdrawal", "refund"}:
            raw_status = require(clean_payload, "status", "status")
            status_str = str(raw_status).strip().upper()
            if status_str not in _SUPPORTED_STATUSES:
                raise NormalizationError(
                    f"Unsupported wallet status '{status_str}'. "
                    f"Supported statuses: {', '.join(sorted(_SUPPORTED_STATUSES))}.",
                    field="status",
                    reason=f"Unsupported status '{status_str}'",
                )

        # 6. Flow for p2p
        flow = str(clean_payload.get("flow", "send")).strip().lower()
        requested_at_dt: datetime | None = None
        approved_at_dt: datetime | None = None

        if event_type == "p2p" and flow == "request":
            raw_req = require(clean_payload, "requested_at", "requested_at")
            requested_at_dt, warn_req = parse_datetime(
                raw_req, field="requested_at", assume_tz="UTC"
            )
            if warn_req:
                warnings.append(warn_req)
            if clean_payload.get("approved_at") is not None:
                approved_at_dt, warn_app = parse_datetime(
                    clean_payload["approved_at"], field="approved_at", assume_tz="UTC"
                )
                if warn_app:
                    warnings.append(warn_app)

        # 7. Timestamps
        raw_created_at = require(clean_payload, "created_at", "created_at")
        created_at_dt, warn_ca = parse_datetime(raw_created_at, field="created_at", assume_tz="UTC")
        if warn_ca:
            warnings.append(warn_ca)

        completed_at_dt: datetime | None = None
        if clean_payload.get("completed_at") is not None:
            completed_at_dt, warn_comp = parse_datetime(
                clean_payload["completed_at"], field="completed_at", assume_tz="UTC"
            )
            if warn_comp:
                warnings.append(warn_comp)

        original_completed_at_dt: datetime | None = None
        if event_type in {"dispute", "reversal"}:
            raw_orig_comp = require(clean_payload, "original_completed_at", "original_completed_at")
            original_completed_at_dt, warn_oc = parse_datetime(
                raw_orig_comp, field="original_completed_at", assume_tz="UTC"
            )
            if warn_oc:
                warnings.append(warn_oc)

        # 8. Currency and Amount
        raw_currency = require(clean_payload, "currency", "currency")
        clean_curr = str(raw_currency).strip().upper()
        # Fiat only validation via shared currency lookup
        curr_code, curr_scale = lookup_currency_by_alpha3(clean_curr)

        # Prohibit floats everywhere in payload
        for amt_key in ["amount"]:
            if isinstance(clean_payload.get(amt_key), float):
                raise NormalizationError(
                    f"Field '{amt_key}' must not be a float. Please supply amounts as strings.",
                    field=amt_key,
                    reason=f"Float prohibited in '{amt_key}'",
                )

        raw_amount = require(clean_payload, "amount", "amount")
        parsed_amount = parse_amount(raw_amount, field="amount", scale=curr_scale)
        if parsed_amount == Decimal(0):
            raise UnsupportedPayloadError(
                "Zero-value transfers cannot be represented because Module 2 requires "
                "strictly positive amounts. This is a documented Ripple limitation."
            )

        transfer_money = Money(
            amount=parsed_amount,
            currency=curr_code,
            scale=curr_scale,
            asset_class=AssetClass.FIAT,
        )

        # Fee
        fee_money: Money | None = None
        fee_val = clean_payload.get("fee")
        if isinstance(fee_val, dict):
            if isinstance(fee_val.get("amount"), float):
                raise NormalizationError(
                    "Field 'fee.amount' must not be a float.",
                    field="fee.amount",
                    reason="Float prohibited in fee.amount",
                )
            fee_raw_amt = fee_val.get("amount")
            fee_raw_curr = fee_val.get("currency", curr_code)
            if fee_raw_amt is not None:
                fee_curr_code, fee_curr_scale = lookup_currency_by_alpha3(fee_raw_curr)
                parsed_fee_amt = parse_amount(fee_raw_amt, field="fee.amount", scale=fee_curr_scale)
                if parsed_fee_amt > Decimal(0):
                    fee_money = Money(
                        amount=parsed_fee_amt,
                        currency=fee_curr_code,
                        scale=fee_curr_scale,
                        asset_class=AssetClass.FIAT,
                    )

        # 9. Parties Resolution
        sender_party: Party
        receiver_party: Party

        effective_original_type = (
            original_event_type if event_type in {"dispute", "reversal"} else event_type
        )

        if effective_original_type in {"p2p", "merchant_payment", "refund"}:
            raw_sender = require(clean_payload, "sender", "sender")
            raw_receiver = require(clean_payload, "receiver", "receiver")
            if not isinstance(raw_sender, dict):
                raise NormalizationError(
                    "Field 'sender' must be a dictionary.",
                    field="sender",
                    reason="Invalid sender type",
                )
            if not isinstance(raw_receiver, dict):
                raise NormalizationError(
                    "Field 'receiver' must be a dictionary.",
                    field="receiver",
                    reason="Invalid receiver type",
                )
            sender_party = self._build_wallet_party(
                raw_sender, "sender", event.received_at, warnings
            )
            receiver_party = self._build_wallet_party(
                raw_receiver, "receiver", event.received_at, warnings
            )

        elif effective_original_type == "funding":
            raw_account = require(clean_payload, "account", "account")
            raw_instrument = require(clean_payload, "instrument", "instrument")
            if not isinstance(raw_account, dict):
                raise NormalizationError(
                    "Field 'account' must be a dictionary.",
                    field="account",
                    reason="Invalid account type",
                )
            if not isinstance(raw_instrument, dict):
                raise NormalizationError(
                    "Field 'instrument' must be a dictionary.",
                    field="instrument",
                    reason="Invalid instrument type",
                )
            # Funding: money flows from external instrument (sender) to wallet account (receiver)
            sender_party = self._build_instrument_party(
                raw_instrument, "instrument", event.received_at
            )
            receiver_party = self._build_wallet_party(
                raw_account, "account", event.received_at, warnings
            )

        elif effective_original_type == "withdrawal":
            raw_account = require(clean_payload, "account", "account")
            raw_instrument = require(clean_payload, "instrument", "instrument")
            if not isinstance(raw_account, dict):
                raise NormalizationError(
                    "Field 'account' must be a dictionary.",
                    field="account",
                    reason="Invalid account type",
                )
            if not isinstance(raw_instrument, dict):
                raise NormalizationError(
                    "Field 'instrument' must be a dictionary.",
                    field="instrument",
                    reason="Invalid instrument type",
                )
            # Withdrawal: money flows from wallet account (sender) to external instrument (receiver)
            sender_party = self._build_wallet_party(
                raw_account, "account", event.received_at, warnings
            )
            receiver_party = self._build_instrument_party(
                raw_instrument, "instrument", event.received_at
            )

        else:
            raise UnsupportedPayloadError(
                f"Unsupported event category '{effective_original_type}'."
            )

        # Disallow self-transfers per Module 2
        if sender_party.party_id == receiver_party.party_id:
            raise UnsupportedPayloadError(
                "Self-transfers where sender equals receiver cannot be represented "
                "because Module 2 requires sender and receiver to be distinct entities."
            )

        # 10. Status and Settled_at Resolution
        settled_at_dt: datetime | None = None
        universal_status: TransactionStatus
        lifecycle_stage: str

        if event_type in {"p2p", "merchant_payment", "funding", "withdrawal", "refund"}:
            match status_str:
                case "PENDING":
                    if event_type == "p2p" and flow == "request" and approved_at_dt is None:
                        universal_status = TransactionStatus.PENDING_AUTHORIZATION
                        lifecycle_stage = "requested"
                    else:
                        universal_status = TransactionStatus.PENDING_SETTLEMENT
                        lifecycle_stage = "pending"
                case "HELD":
                    universal_status = TransactionStatus.PENDING_SETTLEMENT
                    lifecycle_stage = "held"
                case "COMPLETED":
                    if completed_at_dt is None:
                        raise NormalizationError(
                            "Field 'completed_at' is required when status is COMPLETED.",
                            field="completed_at",
                            reason="Missing completed_at for completed event",
                        )
                    universal_status = TransactionStatus.SETTLED
                    settled_at_dt = completed_at_dt
                    lifecycle_stage = "completed"
                case "DENIED":
                    universal_status = TransactionStatus.REJECTED
                    lifecycle_stage = "denied"
                case "FAILED":
                    universal_status = TransactionStatus.FAILED
                    lifecycle_stage = "failed"
                case "CANCELLED":
                    universal_status = TransactionStatus.CANCELLED
                    lifecycle_stage = "cancelled"
                case "EXPIRED":
                    universal_status = TransactionStatus.CANCELLED
                    lifecycle_stage = "expired"
                case _:
                    raise NormalizationError(
                        f"Unsupported status '{status_str}'.",
                        field="status",
                        reason="Unsupported status",
                    )

        elif event_type == "dispute":
            case_id = str(require(clean_payload, "case_id", "case_id")).strip()
            raw_disp_status = require(clean_payload, "dispute_status", "dispute_status")
            disp_status = str(raw_disp_status).strip().lower()
            if disp_status not in _SUPPORTED_DISPUTE_STATUSES:
                raise NormalizationError(
                    f"Unsupported dispute_status '{disp_status}'.",
                    field="dispute_status",
                    reason=f"Unsupported dispute_status '{disp_status}'",
                )

            raw_disp_reason = require(clean_payload, "dispute_reason", "dispute_reason")
            disp_reason = str(raw_disp_reason).strip().lower()
            if disp_reason not in _SUPPORTED_DISPUTE_REASONS:
                raise NormalizationError(
                    f"Unsupported dispute_reason '{disp_reason}'.",
                    field="dispute_reason",
                    reason=f"Unsupported dispute_reason '{disp_reason}'",
                )

            raw_opened_at = require(clean_payload, "opened_at", "opened_at")
            opened_at_dt, warn_op = parse_datetime(
                raw_opened_at, field="opened_at", assume_tz="UTC"
            )
            if warn_op:
                warnings.append(warn_op)

            resolved_at_dt: datetime | None = None
            if disp_status in {"resolved_buyer", "resolved_seller", "withdrawn"}:
                raw_res_at = require(clean_payload, "resolved_at", "resolved_at")
                resolved_at_dt, warn_res = parse_datetime(
                    raw_res_at, field="resolved_at", assume_tz="UTC"
                )
                if warn_res:
                    warnings.append(warn_res)

            match disp_status:
                case "opened":
                    universal_status = TransactionStatus.DISPUTED
                    settled_at_dt = original_completed_at_dt
                    lifecycle_stage = "dispute_opened"
                case "escalated":
                    universal_status = TransactionStatus.DISPUTED
                    settled_at_dt = original_completed_at_dt
                    lifecycle_stage = "dispute_escalated"
                case "resolved_buyer":
                    universal_status = TransactionStatus.REVERSED
                    settled_at_dt = resolved_at_dt
                    lifecycle_stage = "dispute_resolved_buyer"
                case "resolved_seller":
                    universal_status = TransactionStatus.SETTLED
                    settled_at_dt = original_completed_at_dt
                    lifecycle_stage = "dispute_resolved_seller"
                case "withdrawn":
                    universal_status = TransactionStatus.SETTLED
                    settled_at_dt = original_completed_at_dt
                    lifecycle_stage = "dispute_withdrawn"
                case _:
                    raise NormalizationError(
                        f"Unsupported dispute_status '{disp_status}'.",
                        field="dispute_status",
                        reason=f"Unsupported dispute_status '{disp_status}'",
                    )

        elif event_type == "reversal":
            raw_rev_reason = require(clean_payload, "reversal_reason", "reversal_reason")
            rev_reason = str(raw_rev_reason).strip().lower()
            if rev_reason not in _SUPPORTED_REVERSAL_REASONS:
                raise NormalizationError(
                    f"Unsupported reversal_reason '{rev_reason}'.",
                    field="reversal_reason",
                    reason=f"Unsupported reversal_reason '{rev_reason}'",
                )

            raw_reversed_at = require(clean_payload, "reversed_at", "reversed_at")
            reversed_at_dt, warn_rev = parse_datetime(
                raw_reversed_at, field="reversed_at", assume_tz="UTC"
            )
            if warn_rev:
                warnings.append(warn_rev)

            universal_status = TransactionStatus.REVERSED
            settled_at_dt = reversed_at_dt
            lifecycle_stage = "reversed"

        # 11. Initiated_at Resolution
        if event_type == "p2p" and flow == "request":
            if approved_at_dt is not None:
                initiated_at_dt = approved_at_dt
            elif requested_at_dt is not None:
                initiated_at_dt = requested_at_dt
            else:
                initiated_at_dt = created_at_dt
        else:
            initiated_at_dt = created_at_dt

        # 12. Direction
        match effective_original_type:
            case "p2p":
                direction = TransactionDirection.TRANSFER
            case "merchant_payment":
                direction = TransactionDirection.DEBIT
            case "funding":
                direction = TransactionDirection.CREDIT
            case "withdrawal":
                direction = TransactionDirection.DEBIT
            case "refund":
                direction = TransactionDirection.CREDIT
            case _:
                direction = TransactionDirection.TRANSFER

        # 13. Reversibility & Reversal Window
        buyer_protection = clean_payload.get("buyer_protection")
        if buyer_protection is not None:
            is_reversible = bool(buyer_protection)
        else:
            is_reversible = bool(effective_original_type == "merchant_payment")

        reversal_window_ends_at: datetime | None = None
        if is_reversible and settled_at_dt is not None:
            rev_days = clean_payload.get("reversal_window_days")
            if rev_days is not None:
                try:
                    rev_days_int = int(rev_days)
                    if rev_days_int < 1:
                        raise NormalizationError(
                            "Field 'reversal_window_days' must be >= 1.",
                            field="reversal_window_days",
                            reason="reversal_window_days must be >= 1",
                        )
                    reversal_window_ends_at = settled_at_dt + timedelta(days=rev_days_int)
                except ValueError as exc:
                    raise NormalizationError(
                        "Field 'reversal_window_days' must be an integer.",
                        field="reversal_window_days",
                        reason="Invalid reversal_window_days integer",
                    ) from exc
            else:
                reversal_window_ends_at = settled_at_dt + timedelta(
                    seconds=self.profile.typical_reversal_window_seconds or 15552000
                )

        # 14. Correlation key and Transaction ID
        correlation_key = f"wallet:{txn_id}"
        transaction_id = deterministic_uuid("wallet", txn_id)

        # 15. Metadata assembly
        metadata: dict[str, Any] = {
            "event_type": event_type,
            "flow": flow,
        }
        if parent_txn_id is not None:
            metadata["parent_txn_id"] = parent_txn_id
        if original_event_type is not None:
            metadata["original_event_type"] = original_event_type

        if requested_at_dt is not None:
            metadata["requested_at"] = requested_at_dt.isoformat()
        if approved_at_dt is not None:
            metadata["approved_at"] = approved_at_dt.isoformat()

        if status_str == "HELD" or clean_payload.get("hold_reason") is not None:
            metadata["hold"] = True
            if clean_payload.get("hold_reason"):
                metadata["hold_reason"] = str(clean_payload["hold_reason"])

        for reason_field in ["denial_reason", "failure_reason"]:
            if clean_payload.get(reason_field) is not None:
                metadata[reason_field] = str(clean_payload[reason_field])

        if clean_payload.get("funding_source") is not None:
            metadata["funding_source"] = str(clean_payload["funding_source"])
        if clean_payload.get("funding_instrument_id") is not None:
            metadata["funding_instrument_id"] = str(clean_payload["funding_instrument_id"])
        if clean_payload.get("provisional_credit") is not None:
            metadata["provisional_credit"] = bool(clean_payload["provisional_credit"])

        if clean_payload.get("payment_type") is not None:
            metadata["payment_type"] = str(clean_payload["payment_type"])
        if clean_payload.get("buyer_protection") is not None:
            metadata["buyer_protection"] = bool(clean_payload["buyer_protection"])
        if clean_payload.get("fee_payer") is not None:
            metadata["fee_payer"] = str(clean_payload["fee_payer"])

        # Converted currency info
        converted_obj = clean_payload.get("converted")
        if isinstance(converted_obj, dict):
            metadata["converted_amount"] = str(converted_obj.get("amount", ""))
            metadata["converted_currency"] = str(converted_obj.get("currency", ""))
            metadata["converted_fx_rate"] = str(converted_obj.get("fx_rate", ""))

        # Dispute metadata
        if event_type == "dispute":
            metadata["dispute_case_id"] = case_id
            metadata["dispute_status"] = disp_status
            metadata["dispute_reason"] = disp_reason
            match disp_status:
                case "resolved_buyer":
                    metadata["dispute_outcome"] = "buyer"
                case "resolved_seller":
                    metadata["dispute_outcome"] = "seller"
                case "withdrawn":
                    metadata["dispute_outcome"] = "withdrawn"
                case _:
                    metadata["dispute_outcome"] = "pending"

        # Reversal metadata
        if event_type == "reversal":
            metadata["reversal_reason"] = rev_reason

        # Note tracking (note itself is discarded)
        if "note" in clean_payload and clean_payload["note"] is not None:
            note_str = str(clean_payload["note"])
            metadata["note_present"] = True
            metadata["note_length"] = len(note_str)
        else:
            metadata["note_present"] = False
            metadata["note_length"] = 0

        # Unknown top-level keys
        unmapped_keys = sorted([k for k in clean_payload.keys() if k not in _KNOWN_TOP_LEVEL_KEYS])
        if unmapped_keys:
            metadata["unmapped_field_names"] = unmapped_keys
            warnings.append(
                "Unknown top-level payload keys detected and recorded by name only: "
                f"{', '.join(unmapped_keys)}"
            )

        # 16. Transaction construction
        channel = str(clean_payload.get("channel", "wallet_app")).strip() or "wallet_app"
        device_id = clean_payload.get("device_id")
        ip_address = clean_payload.get("ip_address")
        geo_country = clean_payload.get("ip_country")

        with wrap_domain_errors(default_field="transaction"):
            tx = Transaction(
                transaction_id=transaction_id,
                network_transaction_ref=txn_id,
                network="wallet",
                sender_id=sender_party.party_id,
                receiver_id=receiver_party.party_id,
                sender_identifier=sender_party.identifiers[0],
                receiver_identifier=receiver_party.identifiers[0],
                amount=transfer_money,
                fee=fee_money,
                initiated_at=initiated_at_dt,
                settled_at=settled_at_dt,
                observed_at=event.received_at,
                status=universal_status,
                settlement_model=SettlementModel.INTERNAL_LEDGER,
                direction=direction,
                is_reversible=is_reversible,
                reversal_window_ends_at=reversal_window_ends_at,
                intermediary_hops=[],
                channel=channel,
                device_id=str(device_id) if device_id is not None else None,
                ip_address=str(ip_address) if ip_address is not None else None,
                geo_country=str(geo_country).strip().upper() if geo_country else None,
                network_metadata=metadata,
            )

        return NormalizationResult(
            transaction=tx,
            parties=[sender_party, receiver_party],
            correlation_key=correlation_key,
            lifecycle_stage=lifecycle_stage,
            warnings=warnings,
            adapter_version=self.adapter_version,
        )

    def _build_wallet_party(
        self,
        data: dict[str, Any],
        prefix: str,
        received_at: datetime,
        warnings: list[str],
    ) -> Party:
        """Construct a Party domain aggregate for a wallet account."""
        account_id_raw = require(data, "account_id", f"{prefix}.account_id")
        account_id = str(account_id_raw).strip()
        if not account_id:
            raise NormalizationError(
                f"Field '{prefix}.account_id' cannot be blank.",
                field=f"{prefix}.account_id",
                reason="Blank account_id",
            )

        party_id = deterministic_uuid("wallet", account_id)
        primary_ident = PartyIdentifier(
            type=IdentifierType.WALLET_USER_ID,
            value=account_id,
            network="wallet",
            is_pseudonymous=False,
        )
        identifiers: list[PartyIdentifier] = [primary_ident]

        # Email
        if data.get("email") is not None:
            email_val = str(data["email"]).strip()
            if not _EMAIL_PATTERN.match(email_val):
                raise NormalizationError(
                    f"Field '{prefix}.email' has invalid email format.",
                    field=f"{prefix}.email",
                    reason=f"Malformed email format in '{prefix}.email'",
                )
            identifiers.append(
                PartyIdentifier(
                    type=IdentifierType.EMAIL,
                    value=email_val,
                    network="wallet",
                    is_pseudonymous=False,
                )
            )

        # Phone
        if data.get("phone") is not None:
            phone_val = str(data["phone"]).strip()
            cleaned_phone = re.sub(r"[\s\-\(\)]", "", phone_val)
            if not _PHONE_PATTERN.match(cleaned_phone):
                raise NormalizationError(
                    f"Field '{prefix}.phone' has invalid phone number format.",
                    field=f"{prefix}.phone",
                    reason=f"Malformed phone format in '{prefix}.phone'",
                )
            identifiers.append(
                PartyIdentifier(
                    type=IdentifierType.PHONE,
                    value=phone_val,
                    network="wallet",
                    is_pseudonymous=False,
                )
            )

        # Merchant ID
        merchant_id = data.get("merchant_id")
        if merchant_id is not None:
            merchant_id_str = str(merchant_id).strip()
            if merchant_id_str:
                identifiers.append(
                    PartyIdentifier(
                        type=IdentifierType.MERCHANT_ID,
                        value=merchant_id_str,
                        network="wallet",
                        is_pseudonymous=False,
                    )
                )

        # Party type determination
        account_type = str(data.get("account_type", "")).strip().lower()
        mcc = data.get("mcc")
        if account_type == "merchant" or mcc is not None or merchant_id is not None:
            party_type = PartyType.MERCHANT
        elif account_type == "business":
            party_type = PartyType.BUSINESS
        elif account_type == "personal":
            party_type = PartyType.INDIVIDUAL
        else:
            party_type = PartyType.UNKNOWN

        # KYC Level
        raw_kyc = data.get("kyc_level")
        kyc_level = KycLevel.NONE
        if raw_kyc is not None:
            kyc_str = str(raw_kyc).strip().lower()
            match kyc_str:
                case "none":
                    kyc_level = KycLevel.NONE
                case "basic":
                    kyc_level = KycLevel.BASIC
                case "full":
                    kyc_level = KycLevel.FULL
                case _:
                    raise NormalizationError(
                        f"Unknown kyc_level in field '{prefix}.kyc_level'.",
                        field=f"{prefix}.kyc_level",
                        reason=f"Unknown kyc_level '{kyc_str}'",
                    )

        # Country code
        raw_country = data.get("country")
        country_code = str(raw_country).strip().upper() if raw_country else None

        # Party creation time
        if data.get("account_created_at") is not None:
            created_at_dt, warn_acc = parse_datetime(
                data["account_created_at"],
                field=f"{prefix}.account_created_at",
                assume_tz="UTC",
            )
            if warn_acc:
                warnings.append(warn_acc)
        else:
            created_at_dt = received_at

        # Attributes
        attributes: dict[str, Any] = {}
        if account_type:
            attributes["account_type"] = account_type
        if mcc is not None:
            attributes["mcc"] = str(mcc)

        with wrap_domain_errors(default_field=f"{prefix}.country"):
            return Party(
                party_id=party_id,
                identifiers=identifiers,
                party_type=party_type,
                home_network="wallet",
                country_code=country_code,
                created_at=created_at_dt,
                risk_flags=RiskFlags(kyc_level=kyc_level),
                attributes=attributes,
            )

    def _build_instrument_party(
        self,
        data: dict[str, Any],
        prefix: str,
        received_at: datetime,
    ) -> Party:
        """Construct a Party domain aggregate for an external payment instrument."""
        raw_instr_id = require(data, "instrument_id", f"{prefix}.instrument_id")
        instr_id = str(raw_instr_id).strip()

        # Validation: [A-Za-z0-9_.:-]{6,64}
        if not _INSTRUMENT_ID_PATTERN.match(instr_id):
            raise NormalizationError(
                f"Field '{prefix}.instrument_id' does not match format [A-Za-z0-9_.:-]{{6,64}}.",
                field=f"{prefix}.instrument_id",
                reason="Invalid instrument_id format",
            )

        # Remove delimiters and verify it is not all digits
        delims_removed = re.sub(r"[\-_\:\.]", "", instr_id)
        if delims_removed.isdigit():
            raise NormalizationError(
                f"Field '{prefix}.instrument_id' must contain at least one non-digit character.",
                field=f"{prefix}.instrument_id",
                reason="Instrument ID cannot be all digits",
            )

        party_id = deterministic_uuid("wallet", "instrument", instr_id)
        ident = PartyIdentifier(
            type=IdentifierType.OTHER,
            value=f"instr:{instr_id}",
            network="wallet",
            is_pseudonymous=True,
        )

        attributes: dict[str, Any] = {
            "entity_kind": "external_instrument",
        }
        if data.get("instrument_type") is not None:
            attributes["instrument_type"] = str(data["instrument_type"]).strip().lower()
        raw_country = data.get("country")
        country_code = str(raw_country).strip().upper() if raw_country else None
        if country_code is not None:
            attributes["country"] = country_code

        with wrap_domain_errors(default_field=f"{prefix}.country"):
            return Party(
                party_id=party_id,
                identifiers=[ident],
                party_type=PartyType.UNKNOWN,
                home_network="wallet",
                country_code=country_code,
                created_at=received_at,
                attributes=attributes,
            )
