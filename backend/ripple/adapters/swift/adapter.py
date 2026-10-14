"""SWIFT Correspondent Banking payment network adapter.

Translates rail-native cross-border wire transfer events (modelled on pacs.008 / MT103
concepts and gpi tracking) into Ripple's universal domain model.

Adheres strictly to the four adapter rules:
- Pure: zero I/O, zero clock reads (observed_at sourced from event.received_at).
- Deterministic: transaction_id and party_id derived via deterministic_uuid().
- Never silently drop data: wire metadata (instructed amount, FX, charge bearer) preserved.
- Typed errors: raises NormalizationError or UnsupportedPayloadError on invalid inputs.
- Data hygiene: customer names, addresses, and remittance text are discarded; IBANs are
  never echoed in error messages or warnings.
"""

import re
from datetime import datetime
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
from ripple.adapters.swift.profile import SWIFT_PROFILE
from ripple.domain.identity import IdentifierType, PartyIdentifier, _is_valid_iban
from ripple.domain.money import AssetClass, Money
from ripple.domain.party import Party, PartyType
from ripple.domain.transaction import (
    IntermediaryHop,
    IntermediaryRole,
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

_BIC_PATTERN = re.compile(r"^[A-Za-z]{4}[A-Za-z]{2}[A-Za-z0-9]{2}([A-Za-z0-9]{3})?$")
_UETR_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_RECALL_REASONS: dict[str, str] = {
    "FRAD": "fraudulent_origin",
    "DUPL": "duplicate_payment",
    "TECH": "technical_problem",
    "CUST": "customer_request",
}

_UNSUPPORTED_MESSAGE_TYPES = frozenset(
    {"pacs.009", "pacs009", "mt202", "mt202cov", "mt 202", "mt 202 cov"}
)


def _validate_bic(bic_str: str, field_name: str) -> str:
    """Validate ISO 9362 BIC structure without echoing the value on error."""
    cleaned = bic_str.strip().upper()
    if not _BIC_PATTERN.match(cleaned):
        raise NormalizationError(
            f"Field '{field_name}' has invalid BIC structure "
            "(must be 8 or 11 alphanumeric characters).",
            field=field_name,
            reason="Invalid BIC structure",
        )
    return cleaned


def _validate_iban_hygiene(iban_str: str, field_name: str) -> str:
    """Validate IBAN checksum and structure without echoing the IBAN in error messages."""
    cleaned = iban_str.replace(" ", "").upper()
    if not (15 <= len(cleaned) <= 34 and cleaned[:2].isalpha() and cleaned[2:4].isdigit()):
        raise NormalizationError(
            f"Field '{field_name}' has invalid IBAN format.",
            field=field_name,
            reason="Invalid IBAN structure",
        )
    if not _is_valid_iban(cleaned):
        raise NormalizationError(
            f"Field '{field_name}' failed ISO 13616 structure or MOD-97 checksum.",
            field=field_name,
            reason="Invalid IBAN checksum or structure",
        )
    return cleaned


@register_adapter
class SwiftAdapter(NetworkAdapter):
    """Adapter translating SWIFT customer credit wire transfer events into universal models."""

    profile: ClassVar[NetworkProfile] = SWIFT_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"
    discarded_fields: ClassVar[list[str]] = [
        "debtor.name",
        "debtor.address",
        "creditor.name",
        "creditor.address",
        "remittance_info",
    ]

    def can_handle(self, event: RawEvent) -> bool:
        """Determine if this adapter handles the incoming event."""
        return event.network_id.lower() == "swift"

    def describe(self) -> dict[str, Any]:
        """Describe adapter capabilities and discarded fields for data minimization."""
        info = super().describe()
        info["discarded_fields"] = self.discarded_fields
        info["supported_recall_reasons"] = _RECALL_REASONS
        return info

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Normalize a SWIFT customer credit transfer event into the universal domain model."""
        # 1. Reject raw XML or MT text payloads
        raw_input = event.payload
        if isinstance(raw_input, str):
            trimmed_raw = raw_input.strip()
            if (
                trimmed_raw.startswith("<?xml")
                or trimmed_raw.startswith("<")
                or trimmed_raw.startswith("{1:")
            ):
                raise UnsupportedPayloadError(
                    "Raw XML (ISO 20022) or MT text payloads are unsupported in v1; "
                    "only simplified JSON is accepted."
                )

        payload = payload_as_dict(event)
        warnings: list[str] = []

        # 2. Check message_type for FI-to-FI rejections
        msg_type_raw = payload.get("message_type")
        if msg_type_raw:
            cleaned_msg_type = str(msg_type_raw).strip().lower()
            if cleaned_msg_type in _UNSUPPORTED_MESSAGE_TYPES:
                raise UnsupportedPayloadError(
                    f"FI-to-FI transfers ({msg_type_raw}) are not supported in v1."
                )

        # 3. Mandatory event_type
        event_type = str(require(payload, "event_type", "event_type")).strip().lower()
        if event_type not in {"payment", "status", "recall_request", "return"}:
            raise UnsupportedPayloadError(
                f"Unsupported SWIFT event_type '{event_type}'. "
                "Allowed: payment, status, recall_request, return."
            )

        # 4. Mandatory settlement_amount on EVERY event
        settlement_dict = require(payload, "settlement_amount", "settlement_amount")
        if not isinstance(settlement_dict, dict):
            raise NormalizationError(
                "Field 'settlement_amount' must be an object with amount and currency.",
                field="settlement_amount",
                reason="Invalid settlement_amount structure",
            )

        raw_settle_curr = require(settlement_dict, "currency", "settlement_amount.currency")
        settle_currency, settle_scale = lookup_currency_by_alpha3(str(raw_settle_curr))

        raw_settle_amt = require(settlement_dict, "amount", "settlement_amount.amount")
        if isinstance(raw_settle_amt, float):
            raise NormalizationError(
                "Field 'settlement_amount.amount' must not be a float.",
                field="settlement_amount.amount",
                reason="Float prohibited in settlement_amount",
            )
        parsed_settle_amt = parse_amount(
            raw_settle_amt, field="settlement_amount.amount", scale=settle_scale
        )
        if parsed_settle_amt == Decimal(0):
            raise UnsupportedPayloadError(
                "Zero-amount transfers cannot be represented in Module 2."
            )

        settlement_money = Money(
            amount=parsed_settle_amt,
            currency=settle_currency,
            scale=settle_scale,
            asset_class=AssetClass.FIAT,
        )

        # 5. UETR and sender_reference validation
        raw_uetr = payload.get("uetr")
        clean_uetr: str | None = None
        if raw_uetr is not None and str(raw_uetr).strip():
            uetr_str = str(raw_uetr).strip()
            if not _UETR_PATTERN.match(uetr_str):
                raise NormalizationError(
                    "Field 'uetr' must be a valid UUID format (8-4-4-4-12 hex characters).",
                    field="uetr",
                    reason="Invalid UETR format",
                )
            clean_uetr = uetr_str.lower()

        sender_ref_raw = payload.get("sender_reference")
        sender_reference = str(sender_ref_raw).strip() if sender_ref_raw is not None else None

        if not clean_uetr and not sender_reference:
            raise NormalizationError(
                "Field 'sender_reference' is required when 'uetr' is absent.",
                field="sender_reference",
                reason="Missing sender_reference when UETR is absent",
            )

        # 6. Payment event requirements
        creation_datetime_raw = payload.get("creation_datetime")
        value_date_raw = payload.get("value_date")

        if event_type == "payment":
            if creation_datetime_raw is None:
                raise NormalizationError(
                    "Field 'creation_datetime' is required for payment events.",
                    field="creation_datetime",
                    reason="Missing creation_datetime for payment event",
                )
            if value_date_raw is None:
                raise NormalizationError(
                    "Field 'value_date' is required for payment events.",
                    field="value_date",
                    reason="Missing value_date for payment event",
                )

        value_date: str | None = None
        if value_date_raw is not None:
            vdate_clean = str(value_date_raw).strip()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", vdate_clean):
                raise NormalizationError(
                    "Field 'value_date' must be in YYYY-MM-DD format.",
                    field="value_date",
                    reason="Invalid value_date format",
                )
            value_date = vdate_clean

        # 7. Agent BICs validation
        debtor_agent_dict = require(payload, "debtor_agent", "debtor_agent")
        if not isinstance(debtor_agent_dict, dict):
            raise NormalizationError(
                "Field 'debtor_agent' must be an object with bic.",
                field="debtor_agent",
                reason="Invalid debtor_agent structure",
            )
        debtor_agent_bic = _validate_bic(
            str(require(debtor_agent_dict, "bic", "debtor_agent.bic")),
            "debtor_agent.bic",
        )

        creditor_agent_dict = require(payload, "creditor_agent", "creditor_agent")
        if not isinstance(creditor_agent_dict, dict):
            raise NormalizationError(
                "Field 'creditor_agent' must be an object with bic.",
                field="creditor_agent",
                reason="Invalid creditor_agent structure",
            )
        creditor_agent_bic = _validate_bic(
            str(require(creditor_agent_dict, "bic", "creditor_agent.bic")),
            "creditor_agent.bic",
        )

        # 8. Counterparties resolution (Debtor & Creditor)
        debtor_dict = require(payload, "debtor", "debtor")
        creditor_dict = require(payload, "creditor", "creditor")

        def _resolve_party(
            party_dict: dict[str, Any],
            agent_bic: str,
            role_name: str,
        ) -> tuple[PartyIdentifier, Party]:
            acc_dict = require(party_dict, "account", f"{role_name}.account")
            if not isinstance(acc_dict, dict):
                raise NormalizationError(
                    f"Field '{role_name}.account' must be an object with iban or number.",
                    field=f"{role_name}.account",
                    reason="Invalid account structure",
                )

            raw_iban = acc_dict.get("iban")
            raw_number = acc_dict.get("number")

            if raw_iban is not None and str(raw_iban).strip():
                clean_iban = _validate_iban_hygiene(str(raw_iban), f"{role_name}.account.iban")
                with wrap_domain_errors(default_field=f"{role_name}.account.iban"):
                    identifier = PartyIdentifier(
                        type=IdentifierType.IBAN,
                        value=clean_iban,
                        network="swift",
                        is_pseudonymous=False,
                    )
                country_code = clean_iban[:2]
            elif raw_number is not None and str(raw_number).strip():
                acc_num = str(raw_number).strip()
                combined_val = f"{agent_bic}:{acc_num}"
                with wrap_domain_errors(default_field=f"{role_name}.account.number"):
                    identifier = PartyIdentifier(
                        type=IdentifierType.BANK_ACCOUNT,
                        value=combined_val,
                        network="swift",
                        is_pseudonymous=False,
                    )
                # Country code derivation
                supp_country = party_dict.get("country")
                if supp_country and len(str(supp_country).strip()) == 2:
                    country_code = str(supp_country).strip().upper()
                else:
                    country_code = agent_bic[4:6]
            else:
                raise NormalizationError(
                    f"Field '{role_name}.account' must contain either 'iban' or 'number'.",
                    field=f"{role_name}.account",
                    reason="Missing iban or account number",
                )

            norm_val = identifier.normalize()
            party_id = deterministic_uuid("swift", norm_val)

            party_type_str = str(party_dict.get("type", "")).strip().lower()
            if party_type_str == "individual":
                party_type = PartyType.INDIVIDUAL
            elif party_type_str == "business":
                party_type = PartyType.BUSINESS
            else:
                party_type = PartyType.UNKNOWN

            party_attrs = {
                "agent_bic": agent_bic,
            }

            party = Party(
                party_id=party_id,
                identifiers=[identifier],
                party_type=party_type,
                country_code=country_code,
                attributes=party_attrs,
                home_network="swift",
            )
            return identifier, party

        debtor_ident, debtor_party = _resolve_party(debtor_dict, debtor_agent_bic, "debtor")
        creditor_ident, creditor_party = _resolve_party(
            creditor_dict, creditor_agent_bic, "creditor"
        )

        if debtor_ident.normalize() == creditor_ident.normalize():
            raise UnsupportedPayloadError(
                "Debtor and creditor cannot share the same primary account identifier."
            )

        # 9. Intermediary hops in funds-flow order
        # Funds flow: DEBTOR_AGENT -> CORRESPONDENT_BANK (intermediaries) -> CREDITOR_AGENT
        hops: list[IntermediaryHop] = []
        hop_charges: list[Money] = []

        def _parse_charge(charge_dict: Any, hop_field: str) -> tuple[Money | None, str | None]:
            if not isinstance(charge_dict, dict):
                return None, None
            c_amt = charge_dict.get("amount")
            c_curr = charge_dict.get("currency")
            if c_amt is None or c_curr is None:
                return None, None
            if isinstance(c_amt, float):
                raise NormalizationError(
                    f"Field '{hop_field}.charge.amount' must not be a float.",
                    field=f"{hop_field}.charge.amount",
                    reason="Float prohibited in hop charge",
                )
            c_currency, c_scale = lookup_currency_by_alpha3(str(c_curr))
            parsed_c_amt = parse_amount(c_amt, field=f"{hop_field}.charge.amount", scale=c_scale)
            return (
                Money(
                    amount=parsed_c_amt,
                    currency=c_currency,
                    scale=c_scale,
                    asset_class=AssetClass.FIAT,
                ),
                c_currency,
            )

        # Hop 1: Debtor Agent
        debtor_hop_time = None
        if debtor_agent_dict.get("received_at") is not None:
            debtor_hop_time, w_dah = parse_datetime(
                debtor_agent_dict["received_at"], field="debtor_agent.received_at"
            )
            if w_dah:
                warnings.append(w_dah)

        debtor_charge, _ = _parse_charge(debtor_agent_dict.get("charge"), "debtor_agent")
        if debtor_charge is not None:
            hop_charges.append(debtor_charge)

        hops.append(
            IntermediaryHop(
                sequence=1,
                institution_identifier=PartyIdentifier(
                    type=IdentifierType.BIC,
                    value=debtor_agent_bic,
                    network="swift",
                ),
                role=IntermediaryRole.DEBTOR_AGENT,
                received_at=debtor_hop_time,
                fee=debtor_charge,
            )
        )

        # Hops 2..K: Intermediaries
        raw_intermediaries = payload.get("intermediaries", [])
        if not isinstance(raw_intermediaries, list):
            raise NormalizationError(
                "Field 'intermediaries' must be an ordered list.",
                field="intermediaries",
                reason="Invalid intermediaries type",
            )

        current_seq = 2
        for idx, inter_dict in enumerate(raw_intermediaries):
            if not isinstance(inter_dict, dict):
                raise NormalizationError(
                    f"Intermediary at index {idx} must be an object.",
                    field=f"intermediaries[{idx}]",
                    reason="Invalid intermediary element",
                )
            inter_bic = _validate_bic(
                str(require(inter_dict, "bic", f"intermediaries[{idx}].bic")),
                f"intermediaries[{idx}].bic",
            )

            inter_time = None
            if inter_dict.get("received_at") is not None:
                inter_time, w_ith = parse_datetime(
                    inter_dict["received_at"],
                    field=f"intermediaries[{idx}].received_at",
                )
                if w_ith:
                    warnings.append(w_ith)

            inter_charge, _ = _parse_charge(inter_dict.get("charge"), f"intermediaries[{idx}]")
            if inter_charge is not None:
                hop_charges.append(inter_charge)

            hops.append(
                IntermediaryHop(
                    sequence=current_seq,
                    institution_identifier=PartyIdentifier(
                        type=IdentifierType.BIC,
                        value=inter_bic,
                        network="swift",
                    ),
                    role=IntermediaryRole.CORRESPONDENT_BANK,
                    received_at=inter_time,
                    fee=inter_charge,
                )
            )
            current_seq += 1

        # Final Hop: Creditor Agent
        creditor_hop_time = None
        if creditor_agent_dict.get("received_at") is not None:
            creditor_hop_time, w_cah = parse_datetime(
                creditor_agent_dict["received_at"],
                field="creditor_agent.received_at",
            )
            if w_cah:
                warnings.append(w_cah)

        creditor_charge, _ = _parse_charge(creditor_agent_dict.get("charge"), "creditor_agent")
        if creditor_charge is not None:
            hop_charges.append(creditor_charge)

        hops.append(
            IntermediaryHop(
                sequence=current_seq,
                institution_identifier=PartyIdentifier(
                    type=IdentifierType.BIC,
                    value=creditor_agent_bic,
                    network="swift",
                ),
                role=IntermediaryRole.CREDITOR_AGENT,
                received_at=creditor_hop_time,
                fee=creditor_charge,
            )
        )

        # 10. Fee summation across hops
        total_fee_money: Money | None = None
        if hop_charges:
            currencies_in_charges = {c.currency for c in hop_charges}
            if len(currencies_in_charges) == 1:
                common_curr = currencies_in_charges.pop()
                common_scale = hop_charges[0].scale
                sum_fee = sum((c.amount for c in hop_charges), Decimal(0))
                if sum_fee > Decimal(0):
                    total_fee_money = Money(
                        amount=sum_fee,
                        currency=common_curr,
                        scale=common_scale,
                        asset_class=AssetClass.FIAT,
                    )
            else:
                total_fee_money = None
                warnings.append(
                    "Intermediary charges span multiple currencies; Transaction.fee set to None, "
                    "per-hop charges are preserved on the individual hops."
                )

        # 11. Network metadata assembly
        network_metadata: dict[str, Any] = {}
        if msg_type_raw is not None:
            network_metadata["message_type"] = str(msg_type_raw).strip()
        if value_date is not None:
            network_metadata["value_date"] = value_date

        if "instructed_amount" in payload and isinstance(payload["instructed_amount"], dict):
            instr_dict = payload["instructed_amount"]
            if isinstance(instr_dict.get("amount"), float):
                raise NormalizationError(
                    "Field 'instructed_amount.amount' must not be a float.",
                    field="instructed_amount.amount",
                    reason="Float prohibited in instructed_amount",
                )
            network_metadata["instructed_amount"] = instr_dict

        if "exchange_rate" in payload:
            rate_val = payload["exchange_rate"]
            if isinstance(rate_val, float):
                raise NormalizationError(
                    "Field 'exchange_rate' must not be a float.",
                    field="exchange_rate",
                    reason="Float prohibited in exchange_rate",
                )
            network_metadata["exchange_rate"] = str(rate_val).strip()

        if "charge_bearer" in payload:
            cb_val = str(payload["charge_bearer"]).strip().upper()
            if cb_val in {"SHA", "OUR", "BEN"}:
                network_metadata["charge_bearer"] = cb_val
            else:
                warnings.append(f"Unrecognized charge_bearer '{cb_val}'.")
                network_metadata["charge_bearer"] = cb_val

        # Remittance info handling (discard text, keep presence and length)
        if "remittance_info" in payload and payload["remittance_info"] is not None:
            network_metadata["remittance_info_present"] = True
            network_metadata["remittance_info_length"] = len(str(payload["remittance_info"]))

        # 12. Lifecycle status and stage mapping
        raw_status = payload.get("status")
        status_timestamp_raw = payload.get("status_timestamp")
        status_ts_dt = None
        if status_timestamp_raw is not None:
            status_ts_dt, w_sts = parse_datetime(status_timestamp_raw, field="status_timestamp")
            if w_sts:
                warnings.append(w_sts)

        settled_at = None

        if event_type == "payment" and raw_status is None:
            universal_status = TransactionStatus.INITIATED
            lifecycle_stage = "instruction"
        elif raw_status is not None:
            status_code = str(raw_status).strip().upper()
            match status_code:
                case "ACTC":
                    universal_status = TransactionStatus.PENDING_AUTHORIZATION
                    lifecycle_stage = "status_actc"
                case "ACSP":
                    universal_status = TransactionStatus.PENDING_SETTLEMENT
                    lifecycle_stage = "status_acsp"
                case "ACSC":
                    universal_status = TransactionStatus.PENDING_SETTLEMENT
                    lifecycle_stage = "status_acsc"
                    network_metadata["debtor_settled"] = True
                case "PDNG":
                    universal_status = TransactionStatus.PENDING_SETTLEMENT
                    lifecycle_stage = "status_pdng"
                    network_metadata["pending"] = True
                case "ACCC":
                    universal_status = TransactionStatus.SETTLED
                    lifecycle_stage = "status_accc"
                    if status_ts_dt is not None:
                        settled_at = status_ts_dt
                    elif value_date is not None:
                        settled_at = datetime.fromisoformat(f"{value_date}T00:00:00+00:00")
                        warnings.append(
                            "Settlement time approximated using value_date at 00:00 UTC; "
                            "precise status_timestamp was not provided."
                        )
                    else:
                        settled_at = event.received_at
                        warnings.append("Settlement time approximated using event received_at.")
                case "RJCT":
                    universal_status = TransactionStatus.REJECTED
                    lifecycle_stage = "status_rjct"
                    if payload.get("status_reason_code") is not None:
                        network_metadata["status_reason_code"] = str(
                            payload["status_reason_code"]
                        ).strip()
                case _:
                    raise NormalizationError(
                        f"Unsupported SWIFT status code '{status_code}'. "
                        "Allowed: ACTC, ACSP, ACSC, ACCC, PDNG, RJCT.",
                        field="status",
                        reason=f"Unsupported status '{status_code}'",
                    )
        elif event_type == "recall_request":
            universal_status = TransactionStatus.DISPUTED
            lifecycle_stage = "recall_requested"
            raw_recall_reason = payload.get("recall_reason")
            if raw_recall_reason is not None:
                clean_recall_code = str(raw_recall_reason).strip().upper()
                network_metadata["recall_reason"] = clean_recall_code
                network_metadata["recall_reason_label"] = _RECALL_REASONS.get(
                    clean_recall_code, "unknown"
                )
        elif event_type == "return":
            universal_status = TransactionStatus.REVERSED
            lifecycle_stage = "returned"
            if payload.get("returned_at") is not None:
                returned_at_dt, w_ret = parse_datetime(payload["returned_at"], field="returned_at")
                if w_ret:
                    warnings.append(w_ret)
                settled_at = returned_at_dt
            if payload.get("return_reason") is not None:
                network_metadata["return_reason"] = str(payload["return_reason"]).strip()
        else:
            raise NormalizationError(
                f"Unsupported combination of event_type '{event_type}' and status.",
                field="event_type",
                reason="Invalid event_type or status combination",
            )

        # 13. Initiated at resolution
        if creation_datetime_raw is not None:
            creation_dt, w_cd = parse_datetime(creation_datetime_raw, field="creation_datetime")
            if w_cd:
                warnings.append(w_cd)
            initiated_at = creation_dt
        elif status_ts_dt is not None:
            initiated_at = status_ts_dt
        elif payload.get("recall_requested_at") is not None:
            initiated_at, w_rr = parse_datetime(
                payload["recall_requested_at"], field="recall_requested_at"
            )
            if w_rr:
                warnings.append(w_rr)
        elif payload.get("returned_at") is not None:
            initiated_at, w_rt = parse_datetime(payload["returned_at"], field="returned_at")
            if w_rt:
                warnings.append(w_rt)
        else:
            initiated_at = event.received_at
            warnings.append("Initiation time approximated using event received_at.")

        # Guard: If creation_datetime was not explicitly provided and settled_at is known,
        # initiated_at cannot exceed settled_at.
        if creation_datetime_raw is None and settled_at is not None and initiated_at > settled_at:
            initiated_at = settled_at
            warnings.append(
                "Initiation time defaulted to settlement time to preserve temporal invariant."
            )

        # 14. Correlation key and Transaction ID derivation
        if clean_uetr:
            correlation_key = f"swift:{clean_uetr}"
            id_identity = clean_uetr
            network_transaction_ref = clean_uetr
        else:
            assert sender_reference is not None
            v_date_str = value_date or ""
            id_identity = f"ref:{debtor_agent_bic}:{sender_reference}:{v_date_str}"
            correlation_key = f"swift:{id_identity}"
            network_transaction_ref = sender_reference

        transaction_id = deterministic_uuid("swift", id_identity)

        # 15. Purpose code pass-through
        purpose_code_raw = payload.get("purpose_code")
        purpose_code = str(purpose_code_raw).strip().upper() if purpose_code_raw else None

        # 16. Construct universal Transaction inside wrap_domain_errors
        with wrap_domain_errors(default_field="transaction"):
            tx = Transaction(
                transaction_id=transaction_id,
                network_transaction_ref=network_transaction_ref,
                network="SWIFT",
                sender_id=debtor_party.party_id,
                receiver_id=creditor_party.party_id,
                sender_identifier=debtor_ident,
                receiver_identifier=creditor_ident,
                amount=settlement_money,
                fee=total_fee_money,
                initiated_at=initiated_at,
                settled_at=settled_at,
                observed_at=event.received_at,
                status=universal_status,
                settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
                direction=TransactionDirection.TRANSFER,
                is_reversible=False,
                reversal_window_ends_at=None,
                intermediary_hops=hops,
                channel=str(payload.get("channel", "swift")).strip(),
                purpose_code=purpose_code,
                network_metadata=network_metadata,
            )

        return NormalizationResult(
            transaction=tx,
            parties=[debtor_party, creditor_party],
            correlation_key=correlation_key,
            lifecycle_stage=lifecycle_stage,
            warnings=warnings,
            adapter_version=self.adapter_version,
        )
