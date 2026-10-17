"""UPI instant-payment network adapter implementation for Ripple."""

from datetime import datetime
from typing import Any, ClassVar

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    RawEvent,
    deterministic_uuid,
)
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
from ripple.adapters.upi.profile import UPI_PROFILE
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import Money
from ripple.domain.party import Party, PartyType
from ripple.domain.transaction import (
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

# Known NPCI UPI response codes
_UPI_RESPONSE_CODE_LABELS: dict[str, str] = {
    "00": "success",
    "U16": "risk threshold exceeded",
    "U30": "debit failed",
    "ZM": "invalid PIN",
}

_ALLOWED_TXN_TYPES = {"PAY", "COLLECT"}

_ALLOWED_STATUSES = {
    "SUCCESS",
    "FAILURE",
    "PENDING",
    "DEEMED",
    "EXPIRED",
    "REVERSED",
}


class UpiAdapter(NetworkAdapter):
    """Adapter translating Ripple UPI-style JSON payload spec v1 into the universal model.

    Adheres strictly to the 4 adapter rules:
    - Pure: zero I/O, zero clock reads (observed_at sourced from event.received_at).
    - Deterministic: entity and transaction IDs derived via deterministic_uuid(RIPPLE_NAMESPACE).
    - Never silently drop data: all non-universal fields preserved in network_metadata.
    - Typed errors: raises NormalizationError or UnsupportedPayloadError on invalid inputs.
    """

    profile: ClassVar[NetworkProfile] = UPI_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"

    # Fields intentionally omitted for data minimization
    discarded_fields: ClassVar[list[str]] = ["payer.name", "payee.name"]

    def can_handle(self, event: RawEvent) -> bool:
        """Fast structural verification whether this adapter can process the raw event."""
        if event.network_id.lower() != "upi":
            return False
        try:
            payload = payload_as_dict(event)
            return "txn_id" in payload or "txn_type" in payload
        except Exception:
            return False

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Transform a raw UPI payment event into Ripple's universal domain model."""
        payload = payload_as_dict(event)
        warnings: list[str] = []

        # 1. Mandatory transaction reference
        txn_id = str(require(payload, "txn_id", "txn_id")).strip()
        if not txn_id:
            raise NormalizationError(
                "Field 'txn_id' cannot be blank.",
                field="txn_id",
                reason="Blank transaction identifier",
            )

        # 2. Transaction Type validation
        raw_txn_type = str(require(payload, "txn_type", "txn_type")).strip().upper()
        if raw_txn_type not in _ALLOWED_TXN_TYPES:
            raise UnsupportedPayloadError(
                f"Unsupported UPI txn_type '{raw_txn_type}'. Only PAY and COLLECT are supported."
            )

        # 3. Currency validation (INR only)
        raw_currency = str(require(payload, "currency", "currency")).strip().upper()
        if raw_currency != "INR":
            raise NormalizationError(
                f"UPI transactions only support currency 'INR', got '{raw_currency}'.",
                field="currency",
                reason="Unsupported currency for UPI",
            )

        # 4. Amount parsing
        raw_amount = require(payload, "amount", "amount")
        parsed_amount = parse_amount(raw_amount, field="amount", scale=2)

        # 5. Status validation and mapping
        raw_status = str(require(payload, "status", "status")).strip().upper()
        if raw_status not in _ALLOWED_STATUSES:
            raise NormalizationError(
                f"Unknown UPI status '{raw_status}'. Allowed: {sorted(_ALLOWED_STATUSES)}",
                field="status",
                reason=f"Unknown status '{raw_status}'",
            )

        # 6. Timestamps
        raw_initiated_at = payload.get("initiated_at")
        raw_collect_requested_at = payload.get("collect_requested_at")
        raw_completed_at = payload.get("completed_at")

        if raw_txn_type == "COLLECT" and not raw_collect_requested_at:
            raise NormalizationError(
                "Field 'collect_requested_at' is required for COLLECT transactions.",
                field="collect_requested_at",
                reason="Missing collect_requested_at",
            )

        initiated_at_dt: datetime | None = None
        if raw_initiated_at:
            initiated_at_dt, warn = parse_datetime(
                raw_initiated_at, field="initiated_at", assume_tz="Asia/Kolkata"
            )
            if warn:
                warnings.append(warn)

        collect_requested_dt: datetime | None = None
        if raw_collect_requested_at:
            collect_requested_dt, warn = parse_datetime(
                raw_collect_requested_at,
                field="collect_requested_at",
                assume_tz="Asia/Kolkata",
            )
            if warn:
                warnings.append(warn)

        completed_at_dt: datetime | None = None
        if raw_completed_at:
            completed_at_dt, warn = parse_datetime(
                raw_completed_at, field="completed_at", assume_tz="Asia/Kolkata"
            )
            if warn:
                warnings.append(warn)

        # Effective transaction initiated_at
        effective_initiated_at = initiated_at_dt or collect_requested_dt
        if effective_initiated_at is None:
            raise NormalizationError(
                "Either 'initiated_at' or 'collect_requested_at' must be provided.",
                field="initiated_at",
                reason="Missing transaction initiation timestamp",
            )

        # Status and lifecycle mapping
        deemed = False
        settled_at: datetime | None = None

        if raw_status == "SUCCESS":
            if completed_at_dt is None:
                raise NormalizationError(
                    "Field 'completed_at' is required for SUCCESS UPI transactions.",
                    field="completed_at",
                    reason="Missing completed_at for SETTLED status",
                )
            status = TransactionStatus.SETTLED
            settled_at = completed_at_dt
            lifecycle_stage = "completed"

        elif raw_status == "FAILURE":
            status = TransactionStatus.FAILED
            lifecycle_stage = "failed"

        elif raw_status == "PENDING":
            if raw_txn_type == "COLLECT" and initiated_at_dt is None:
                status = TransactionStatus.PENDING_AUTHORIZATION
                lifecycle_stage = "collect_requested"
            else:
                status = TransactionStatus.PENDING_SETTLEMENT
                lifecycle_stage = "pending"

        elif raw_status == "DEEMED":
            status = TransactionStatus.PENDING_SETTLEMENT
            deemed = True
            lifecycle_stage = "deemed"

        elif raw_status == "EXPIRED":
            status = TransactionStatus.CANCELLED
            lifecycle_stage = "expired"

        elif raw_status == "REVERSED":
            status = TransactionStatus.REVERSED
            lifecycle_stage = "reversed"

        else:
            raise NormalizationError(
                f"Unhandled status '{raw_status}'.",
                field="status",
                reason="Unhandled status",
            )

        # 7. Counterparties (payer & payee)
        payer_dict = require(payload, "payer", "payer")
        if not isinstance(payer_dict, dict):
            raise NormalizationError(
                "Field 'payer' must be an object.",
                field="payer",
                reason="Invalid payer structure",
            )

        payee_dict = require(payload, "payee", "payee")
        if not isinstance(payee_dict, dict):
            raise NormalizationError(
                "Field 'payee' must be an object.",
                field="payee",
                reason="Invalid payee structure",
            )

        payer_party, payer_primary_ident = self._build_party(
            party_dict=payer_dict,
            role_label="payer",
            received_at=event.received_at,
        )

        payee_party, payee_primary_ident = self._build_party(
            party_dict=payee_dict,
            role_label="payee",
            received_at=event.received_at,
        )

        if payer_party.party_id == payee_party.party_id:
            raise NormalizationError(
                "Payer and payee resolve to the same party identifier (self-transfers disallowed).",
                field="payee",
                reason="Self-transfer detected",
            )

        # 8. Build network metadata
        network_metadata: dict[str, Any] = {
            "txn_type": raw_txn_type,
        }
        if collect_requested_dt is not None:
            network_metadata["collect_requested_at"] = collect_requested_dt.isoformat()
        if deemed:
            network_metadata["deemed"] = True

        rrn = payload.get("rrn")
        if rrn:
            network_metadata["rrn"] = str(rrn)

        raw_remarks = payload.get("remarks")
        if raw_remarks is not None:
            # Truncate remarks to 100 characters per specification
            network_metadata["remarks"] = str(raw_remarks)[:100]

        response_code = payload.get("response_code")
        if response_code is not None:
            resp_str = str(response_code).strip()
            network_metadata["response_code"] = resp_str
            network_metadata["response_code_label"] = _UPI_RESPONSE_CODE_LABELS.get(
                resp_str, "unknown"
            )

        # 9. IDs and correlation
        correlation_key = f"upi:{txn_id}"
        transaction_id = deterministic_uuid("upi", txn_id)

        # 10. Channel and client telemetry
        raw_channel = payload.get("channel")
        channel = str(raw_channel).strip().lower() if raw_channel else "mobile_app"

        device_id = str(payload["device_id"]) if payload.get("device_id") else None
        ip_address = str(payload["ip_address"]) if payload.get("ip_address") else None
        purpose_code = str(payload["purpose_code"]) if payload.get("purpose_code") else None

        with wrap_domain_errors(default_field="transaction"):
            transaction = Transaction(
                transaction_id=transaction_id,
                network_transaction_ref=txn_id,
                network="UPI",
                sender_id=payer_party.party_id,
                receiver_id=payee_party.party_id,
                sender_identifier=payer_primary_ident,
                receiver_identifier=payee_primary_ident,
                amount=Money(amount=parsed_amount, currency="INR", scale=2),
                fee=None,
                initiated_at=effective_initiated_at,
                settled_at=settled_at,
                observed_at=event.received_at,
                status=status,
                settlement_model=SettlementModel.INSTANT,
                direction=TransactionDirection.TRANSFER,
                is_reversible=False,
                reversal_window_ends_at=None,
                intermediary_hops=[],
                channel=channel,
                purpose_code=purpose_code,
                device_id=device_id,
                ip_address=ip_address,
                geo_country="IN",
                network_metadata=network_metadata,
            )

        parties = [payer_party, payee_party]

        return NormalizationResult(
            transaction=transaction,
            parties=parties,
            correlation_key=correlation_key,
            lifecycle_stage=lifecycle_stage,
            warnings=warnings,
            adapter_version=self.adapter_version,
        )

    def _build_party(
        self,
        party_dict: dict[str, Any],
        role_label: str,
        received_at: datetime,
    ) -> tuple[Party, PartyIdentifier]:
        """Construct a multi-identifier Party entity respecting primary identifier preference."""
        vpa = party_dict.get("vpa")
        account_dict = party_dict.get("account")
        phone = party_dict.get("phone")

        identifiers: list[PartyIdentifier] = []
        primary_ident: PartyIdentifier | None = None
        normalized_primary_str: str | None = None

        # 1. VPA Identifier (Preference 1)
        if vpa and isinstance(vpa, str) and vpa.strip():
            clean_vpa = vpa.strip().lower()
            try:
                vpa_ident = PartyIdentifier(
                    type=IdentifierType.VPA,
                    value=clean_vpa,
                    network="UPI",
                    is_pseudonymous=False,
                )
                identifiers.append(vpa_ident)
                primary_ident = vpa_ident
                normalized_primary_str = clean_vpa
            except Exception as exc:
                raise NormalizationError(
                    f"Invalid VPA '{vpa}' for {role_label}: {exc}",
                    field=f"{role_label}.vpa",
                    reason=f"Invalid VPA in {role_label}",
                ) from exc

        # 2. Bank Account Identifier (Preference 2)
        if account_dict and isinstance(account_dict, dict):
            ifsc = account_dict.get("ifsc")
            number = account_dict.get("number")
            if ifsc and number and str(ifsc).strip() and str(number).strip():
                clean_ifsc = str(ifsc).strip().upper()
                clean_num = str(number).strip()
                normalized_bank_acc = f"{clean_ifsc}:{clean_num}"
                try:
                    bank_ident = PartyIdentifier(
                        type=IdentifierType.BANK_ACCOUNT,
                        value=normalized_bank_acc,
                        network="UPI",
                        is_pseudonymous=False,
                    )
                    identifiers.append(bank_ident)
                    if primary_ident is None:
                        primary_ident = bank_ident
                        normalized_primary_str = normalized_bank_acc
                except Exception as exc:
                    raise NormalizationError(
                        f"Invalid bank account for {role_label}: {exc}",
                        field=f"{role_label}.account",
                        reason=f"Invalid bank account in {role_label}",
                    ) from exc

        # 3. Phone Identifier (Preference 3)
        if phone and isinstance(phone, str) and phone.strip():
            clean_phone = phone.strip()
            try:
                phone_ident = PartyIdentifier(
                    type=IdentifierType.PHONE,
                    value=clean_phone,
                    network="UPI",
                    is_pseudonymous=False,
                )
                identifiers.append(phone_ident)
                if primary_ident is None:
                    primary_ident = phone_ident
                    normalized_primary_str = phone_ident.normalize()
            except Exception as exc:
                raise NormalizationError(
                    f"Invalid phone number '{phone}' for {role_label}: {exc}",
                    field=f"{role_label}.phone",
                    reason=f"Invalid phone number in {role_label}",
                ) from exc

        if not identifiers or primary_ident is None or normalized_primary_str is None:
            raise NormalizationError(
                f"Party '{role_label}' must carry at least one valid identifier "
                "(vpa, account, phone).",
                field=role_label,
                reason=f"Missing counterparty identifier for {role_label}",
            )

        # Derive stable deterministic UUID
        party_id = deterministic_uuid("upi", normalized_primary_str)

        # Attributes and Party Type
        attributes: dict[str, Any] = {}
        if vpa and "@" in str(vpa):
            attributes["psp_handle"] = str(vpa).split("@")[1].strip().lower()

        party_type = PartyType.INDIVIDUAL
        merchant_dict = party_dict.get("merchant")
        if merchant_dict and isinstance(merchant_dict, dict):
            party_type = PartyType.MERCHANT
            if "mcc" in merchant_dict:
                attributes["mcc"] = str(merchant_dict["mcc"])
            if "merchant_id" in merchant_dict:
                attributes["merchant_id"] = str(merchant_dict["merchant_id"])

        party = Party(
            party_id=party_id,
            identifiers=identifiers,
            party_type=party_type,
            home_network="UPI",
            country_code="IN",
            created_at=received_at,
            attributes=attributes,
        )

        return party, primary_ident

    def describe(self) -> dict[str, Any]:
        """Return serializable dictionary describing profile, version, and discarded fields."""
        data = super().describe()
        data["discarded_fields"] = list(self.discarded_fields)
        return data
