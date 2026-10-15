"""Payment card network adapter implementation for Ripple."""

import copy
import hashlib
import hmac
import logging
import re
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from ripple.adapters.card.profile import CARD_PROFILE
from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.currencies import resolve_currency
from ripple.adapters.errors import (
    AdapterRegistrationError,
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.helpers import (
    parse_amount,
    parse_datetime,
    payload_as_dict,
    require,
)
from ripple.adapters.profile import NetworkProfile
from ripple.config import Settings
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import Money
from ripple.domain.party import Party, PartyType
from ripple.domain.transaction import (
    IntermediaryHop,
    IntermediaryRole,
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

logger = logging.getLogger(__name__)

_ALLOWED_EVENT_TYPES = {
    "authorization",
    "clearing",
    "settlement",
    "reversal",
    "chargeback",
}

_ALLOWED_PROCESSING_CODES = {"00", "01", "20"}

_ALLOWED_CHANNELS = {"pos", "ecommerce", "atm", "recurring", "moto"}

_PROHIBITED_SENSITIVE_FIELDS = {
    "cvv",
    "cvc",
    "cvv2",
    "cvc2",
    "cid",
    "track1",
    "track2",
    "track_1",
    "track_2",
    "pin_block",
    "pin",
    "expiry",
    "exp_date",
    "expiration_date",
}

_DEV_INSECURE_PAN_TOKEN_KEY = "dev_insecure_card_pan_token_key_min_32_chars_long_placeholder"


def is_luhn_valid(pan: str) -> bool:
    """Validate card Primary Account Number (PAN) using the Luhn checksum algorithm."""
    digits_only = [int(ch) for ch in pan if ch.isdigit()]
    if len(digits_only) < 12 or len(digits_only) > 19:
        return False
    checksum = 0
    reverse_digits = digits_only[::-1]
    for i, d in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = d * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += d
    return checksum % 10 == 0


class CardAdapter(NetworkAdapter):
    """Adapter translating Ripple card spec v1 (ISO 8583-style) into the universal model.

    Adheres strictly to the 4 adapter rules and strict sensitive-data protection:
    - Pure: zero I/O, zero clock reads (observed_at sourced from event.received_at).
    - Deterministic: entity and transaction IDs derived via deterministic_uuid().
    - Never silently drop data: all non-universal fields preserved in network_metadata.
    - Typed errors: raises NormalizationError or UnsupportedPayloadError on invalid inputs.
    - Sensitive data protection: Raw PAN is never retained or logged. Converted via HMAC-SHA256
      to a pseudonymous token. CVV/track/PIN data discarded with field-only warnings.
    """

    profile: ClassVar[NetworkProfile] = CARD_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"

    discarded_fields: ClassVar[list[str]] = [
        "pan",
        "cvv",
        "cvc",
        "cvv2",
        "track1",
        "track2",
        "pin_block",
        "expiry",
    ]

    def __init__(self, pan_token_key: str) -> None:
        """Initialize CardAdapter with HMAC key for card PAN tokenization."""
        if not pan_token_key or len(pan_token_key) < 32:
            raise AdapterRegistrationError(
                "CardAdapter pan_token_key must be at least 32 characters long."
            )
        self.pan_token_key: str = pan_token_key

    @classmethod
    def from_settings(cls, settings: Any = None) -> "CardAdapter":
        """Instantiate CardAdapter extracting CARD_PAN_TOKEN_KEY from settings.

        Enforces strict key configuration in staging and production environments.
        Falls back to an insecure development key with loud warnings in development.
        """
        key: str | None = None
        env_mode = "development"

        if isinstance(settings, Settings):
            key = settings.CARD_PAN_TOKEN_KEY
            env_mode = settings.APP_ENV
        elif settings is not None:
            key = getattr(settings, "CARD_PAN_TOKEN_KEY", None)
            env_mode = getattr(settings, "APP_ENV", "development")

        if not key or len(key) < 32:
            if env_mode in {"production", "staging"}:
                raise AdapterRegistrationError(
                    f"CARD_PAN_TOKEN_KEY is required and must be >= 32 characters in {env_mode}."
                )
            logger.warning(
                "************************************************************************\n"
                "WARNING: CARD_PAN_TOKEN_KEY is unset or < 32 chars in development mode!\n"
                "Falling back to an insecure hardcoded development token key.\n"
                "Never deploy to staging or production without a secure 32+ char key.\n"
                "************************************************************************"
            )
            key = _DEV_INSECURE_PAN_TOKEN_KEY

        return cls(pan_token_key=key)

    def can_handle(self, event: RawEvent) -> bool:
        """Fast structural verification whether this adapter can process the raw event."""
        if event.network_id.lower() != "card":
            return False
        try:
            payload = payload_as_dict(event)
            return "event_type" in payload or "network_reference" in payload
        except Exception:
            return False

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Transform a raw card payment event into Ripple's universal domain model."""
        payload = copy.deepcopy(payload_as_dict(event))
        warnings: list[str] = []

        # 1. Prohibited sensitive fields detection and discard
        for prohibited_key in list(payload.keys()):
            if prohibited_key.lower() in _PROHIBITED_SENSITIVE_FIELDS:
                warnings.append(
                    f"Sensitive authentication field '{prohibited_key}' was detected "
                    "in payload and discarded."
                )
                payload.pop(prohibited_key, None)

        # 2. Mandatory event_type
        raw_event_type = str(require(payload, "event_type", "event_type")).strip().lower()
        if raw_event_type not in _ALLOWED_EVENT_TYPES:
            raise UnsupportedPayloadError(
                f"Unsupported card event_type '{raw_event_type}'. "
                f"Allowed: {sorted(_ALLOWED_EVENT_TYPES)}"
            )

        # 3. Mandatory network_reference
        net_ref = str(require(payload, "network_reference", "network_reference")).strip()
        if not net_ref:
            raise NormalizationError(
                "Field 'network_reference' cannot be blank.",
                field="network_reference",
                reason="Blank network reference",
            )

        # 4. Mandatory processing_code
        raw_processing_code = str(require(payload, "processing_code", "processing_code")).strip()
        if raw_processing_code not in _ALLOWED_PROCESSING_CODES:
            raise UnsupportedPayloadError(
                f"Unsupported card processing_code '{raw_processing_code}'. "
                f"Allowed: {sorted(_ALLOWED_PROCESSING_CODES)}"
            )

        # 5. Currency resolution and decimal scale
        raw_curr = require(payload, "currency", "currency")
        currency_code, scale = self._resolve_currency(raw_curr)

        # 6. Amount validation and zero-amount check (account verification check)
        raw_amount = require(payload, "amount", "amount")
        parsed_amount = parse_amount(raw_amount, field="amount", scale=scale)

        if parsed_amount == Decimal(0):
            raise UnsupportedPayloadError(
                "Zero-amount card authorizations (account verification checks) cannot be "
                "represented because Module 2 requires strictly positive amounts. "
                "This is a documented Ripple limitation."
            )

        # 7. Card PAN and Token handling (Strict PAN Security)
        card_token, bin_val, last4_val = self._resolve_card_token(payload)

        # 8. Transmission Time resolution (ISO-8601 or DE7-style MMDDhhmmss)
        transmission_dt = self._resolve_transmission_time(
            payload=payload,
            received_at=event.received_at,
            warnings=warnings,
        )

        # 9. Acquirer (mandatory) and other hop participants
        acquirer_dict = require(payload, "acquirer", "acquirer")
        if not isinstance(acquirer_dict, dict) or "id" not in acquirer_dict:
            raise NormalizationError(
                "Field 'acquirer' must be an object with an 'id'.",
                field="acquirer.id",
                reason="Missing acquirer id",
            )
        acquirer_id = str(acquirer_dict["id"]).strip()
        if not acquirer_id:
            raise NormalizationError(
                "Field 'acquirer.id' cannot be blank.",
                field="acquirer.id",
                reason="Blank acquirer id",
            )

        scheme_name = str(payload["scheme"]).strip() if payload.get("scheme") else None

        issuer_dict = payload.get("issuer")
        issuer_id: str | None = None
        issuer_country: str | None = None
        if isinstance(issuer_dict, dict):
            if issuer_dict.get("id"):
                issuer_id = str(issuer_dict["id"]).strip()
            if issuer_dict.get("country"):
                issuer_country = str(issuer_dict["country"]).strip().upper()

        # 10. Merchant data
        merchant_dict = payload.get("merchant")
        if not isinstance(merchant_dict, dict) or not merchant_dict.get("id"):
            raise NormalizationError(
                "Field 'merchant' must be an object containing 'id'.",
                field="merchant.id",
                reason="Missing merchant id",
            )
        merchant_id = str(merchant_dict["id"]).strip()
        merchant_name = str(merchant_dict.get("name") or "")
        merchant_mcc = str(merchant_dict.get("mcc") or "")
        merchant_country = (
            str(merchant_dict.get("country")).strip().upper()
            if merchant_dict.get("country")
            else None
        )
        merchant_city = str(merchant_dict.get("city") or "")

        # 11. Channel and terminal
        raw_channel = payload.get("channel")
        if raw_channel and str(raw_channel).strip().lower() in _ALLOWED_CHANNELS:
            channel = str(raw_channel).strip().lower()
        else:
            channel = "other"
            warnings.append(
                f"Unrecognized or missing card channel '{raw_channel}'; defaulted to 'other'."
            )

        # 12. Status & Lifecycle Mapping
        response_code = (
            str(payload.get("response_code")).strip()
            if payload.get("response_code") is not None
            else None
        )
        decline_code: str | None = None
        settled_at: datetime | None = None

        if raw_event_type == "authorization":
            if response_code == "00":
                status = TransactionStatus.AUTHORIZED
            else:
                status = TransactionStatus.REJECTED
                decline_code = response_code or "declined"
        elif raw_event_type == "clearing":
            status = TransactionStatus.PENDING_SETTLEMENT
        elif raw_event_type == "settlement":
            status = TransactionStatus.SETTLED
            raw_settlement_date = require(payload, "settlement_date", "settlement_date")
            try:
                s_date = date.fromisoformat(str(raw_settlement_date).strip())
                settled_at = datetime.combine(s_date, time(0, 0, 0), tzinfo=UTC)
                warnings.append(
                    "Settlement timestamp inferred from settlement_date at 00:00:00 UTC; "
                    "exact clearing cut-off time unknown."
                )
            except ValueError as exc:
                raise NormalizationError(
                    f"Field 'settlement_date' must be YYYY-MM-DD, got '{raw_settlement_date}'.",
                    field="settlement_date",
                    reason="Invalid settlement_date format",
                ) from exc
        elif raw_event_type == "reversal":
            status = TransactionStatus.REVERSED
        elif raw_event_type == "chargeback":
            status = TransactionStatus.DISPUTED
        else:
            raise UnsupportedPayloadError(f"Unhandled card event_type '{raw_event_type}'.")

        # 13. Reversal Window
        reversal_window_ends_at: datetime | None = None
        if settled_at is not None and self.profile.typical_reversal_window_seconds is not None:
            reversal_window_ends_at = settled_at + timedelta(
                seconds=self.profile.typical_reversal_window_seconds
            )

        # 14. Parties and Direction
        # Sender/Receiver and hop order flip on refunds (processing_code 20)
        is_refund = raw_processing_code == "20"
        direction = TransactionDirection.CREDIT if is_refund else TransactionDirection.DEBIT

        cardholder_party = self._build_cardholder_party(
            card_token=card_token,
            bin_val=bin_val,
            last4_val=last4_val,
            country_code=issuer_country,
            received_at=event.received_at,
        )

        merchant_party = self._build_merchant_party(
            acquirer_id=acquirer_id,
            merchant_id=merchant_id,
            name=merchant_name,
            mcc=merchant_mcc,
            country=merchant_country,
            city=merchant_city,
            channel=channel,
            received_at=event.received_at,
        )

        if is_refund:
            sender_party = merchant_party
            receiver_party = cardholder_party
            sender_ident = merchant_party.identifiers[0]
            receiver_ident = cardholder_party.identifiers[0]
        else:
            sender_party = cardholder_party
            receiver_party = merchant_party
            sender_ident = cardholder_party.identifiers[0]
            receiver_ident = merchant_party.identifiers[0]

        # 15. Intermediary Hops
        # Hops follow fund flow:
        # Purchase: issuer -> scheme -> acquirer
        # Refund: acquirer -> scheme -> issuer
        hops = self._build_hops(
            acquirer_id=acquirer_id,
            scheme_name=scheme_name,
            issuer_id=issuer_id,
            is_refund=is_refund,
        )

        # 16. Fee (MDR fee)
        fee_money: Money | None = None
        if payload.get("mdr_fee") is not None:
            parsed_fee = parse_amount(payload["mdr_fee"], field="mdr_fee", scale=scale)
            fee_money = Money(amount=parsed_fee, currency=currency_code, scale=scale)

        # 17. Network Metadata
        network_metadata: dict[str, Any] = {
            "amount_stage": raw_event_type,
            "processing_code": raw_processing_code,
        }
        if payload.get("mti"):
            network_metadata["mti"] = str(payload["mti"])
        if payload.get("rrn"):
            network_metadata["rrn"] = str(payload["rrn"])
        if payload.get("stan"):
            network_metadata["stan"] = str(payload["stan"])
        if payload.get("auth_code"):
            network_metadata["auth_code"] = str(payload["auth_code"])
        if response_code is not None:
            network_metadata["response_code"] = response_code
        if decline_code is not None:
            network_metadata["decline_code"] = decline_code
        if payload.get("terminal_id"):
            network_metadata["terminal_id"] = str(payload["terminal_id"])
        if payload.get("reason_code"):
            network_metadata["reason_code"] = str(payload["reason_code"])
        if payload.get("card_present") is not None:
            network_metadata["card_present"] = bool(payload["card_present"])
        if payload.get("three_ds") is not None:
            network_metadata["three_ds"] = bool(payload["three_ds"])

        correlation_key = f"card:{net_ref}"
        transaction_id = deterministic_uuid("card", net_ref)

        device_id = str(payload["device_id"]) if payload.get("device_id") else None
        ip_address = str(payload["ip_address"]) if payload.get("ip_address") else None

        transaction = Transaction(
            transaction_id=transaction_id,
            network_transaction_ref=net_ref,
            network="CARD",
            sender_id=sender_party.party_id,
            receiver_id=receiver_party.party_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=Money(amount=parsed_amount, currency=currency_code, scale=scale),
            fee=fee_money,
            initiated_at=transmission_dt,
            settled_at=settled_at,
            observed_at=event.received_at,
            status=status,
            settlement_model=SettlementModel.AUTH_CAPTURE,
            direction=direction,
            is_reversible=True,
            reversal_window_ends_at=reversal_window_ends_at,
            intermediary_hops=hops,
            channel=channel,
            device_id=device_id,
            ip_address=ip_address,
            geo_country=merchant_country,
            network_metadata=network_metadata,
        )

        parties = [sender_party, receiver_party]

        return NormalizationResult(
            transaction=transaction,
            parties=parties,
            correlation_key=correlation_key,
            lifecycle_stage=raw_event_type,
            warnings=warnings,
            adapter_version=self.adapter_version,
        )

    def _resolve_currency(self, raw_curr: Any) -> tuple[str, int]:
        """Resolve alpha-3 or numeric currency code and determine decimal scale."""
        return resolve_currency(raw_curr)

    def _resolve_card_token(
        self,
        payload: dict[str, Any],
    ) -> tuple[str, str | None, str | None]:
        """Validate PAN with Luhn, tokenize via HMAC-SHA256, or extract pre-tokenized token."""
        raw_pan = payload.get("pan")
        card_token = payload.get("card_token")

        bin_val: str | None = None
        last4_val: str | None = None

        if raw_pan:
            clean_pan = re.sub(r"\D", "", str(raw_pan))
            if not is_luhn_valid(clean_pan):
                raise NormalizationError(
                    "Card PAN failed Luhn checksum validation.",
                    field="pan",
                    reason="Card PAN fails Luhn check",
                )

            token_digest = hmac.new(
                self.pan_token_key.encode("utf-8"),
                clean_pan.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()[:32]
            token = f"tok_{token_digest}"
            bin_val = clean_pan[:6]
            last4_val = clean_pan[-4:]
            # Discard raw PAN from payload dict immediately
            payload.pop("pan", None)
            return token, bin_val, last4_val

        if card_token and str(card_token).strip():
            token = str(card_token).strip()
            bin_val = str(payload.get("bin")).strip() if payload.get("bin") else None
            last4_val = str(payload.get("last4")).strip() if payload.get("last4") else None
            return token, bin_val, last4_val

        raise NormalizationError(
            "Card event must contain either 'pan' or 'card_token'.",
            field="pan",
            reason="Missing card account identifier (pan or card_token)",
        )

    def _resolve_transmission_time(
        self,
        payload: dict[str, Any],
        received_at: datetime,
        warnings: list[str],
    ) -> datetime:
        """Parse transmission_time from ISO-8601 or DE7 MMDDhhmmss GMT format."""
        raw_time = require(payload, "transmission_time", "transmission_time")
        clean_str = str(raw_time).strip()

        # Check for DE7 MMDDhhmmss format (exactly 10 digits)
        if len(clean_str) == 10 and clean_str.isdigit():
            mm = int(clean_str[:2])
            dd = int(clean_str[2:4])
            hh = int(clean_str[4:6])
            minute = int(clean_str[6:8])
            ss = int(clean_str[8:10])

            candidate_years = [
                received_at.year - 1,
                received_at.year,
                received_at.year + 1,
            ]
            best_dt: datetime | None = None
            min_delta_seconds = float("inf")

            for y in candidate_years:
                try:
                    c_dt = datetime(y, mm, dd, hh, minute, ss, tzinfo=UTC)
                    delta = abs((c_dt - received_at).total_seconds())
                    if delta < min_delta_seconds:
                        min_delta_seconds = delta
                        best_dt = c_dt
                except ValueError:
                    continue

            if best_dt is None:
                raise NormalizationError(
                    f"DE7 transmission_time '{clean_str}' could not be parsed into a valid date.",
                    field="transmission_time",
                    reason="Invalid DE7 transmission timestamp",
                )
            return best_dt

        # Standard ISO-8601 timestamp
        dt, warn = parse_datetime(clean_str, field="transmission_time", assume_tz="UTC")
        if warn:
            warnings.append(warn)
        return dt

    def _build_cardholder_party(
        self,
        card_token: str,
        bin_val: str | None,
        last4_val: str | None,
        country_code: str | None,
        received_at: datetime,
    ) -> Party:
        """Construct cardholder Party aggregate from tokenized credentials."""
        identifier = PartyIdentifier(
            type=IdentifierType.CARD_PAN_TOKEN,
            value=card_token,
            network="CARD",
            is_pseudonymous=True,
        )
        party_id = deterministic_uuid("card", card_token)
        attributes: dict[str, Any] = {"entity_kind": "card"}
        if bin_val:
            attributes["bin"] = bin_val
        if last4_val:
            attributes["last4"] = last4_val

        return Party(
            party_id=party_id,
            identifiers=[identifier],
            party_type=PartyType.INDIVIDUAL,
            home_network="CARD",
            country_code=country_code if (country_code and len(country_code) == 2) else None,
            created_at=received_at,
            attributes=attributes,
        )

    def _build_merchant_party(
        self,
        acquirer_id: str,
        merchant_id: str,
        name: str,
        mcc: str,
        country: str | None,
        city: str,
        channel: str,
        received_at: datetime,
    ) -> Party:
        """Construct merchant or financial institution Party aggregate."""
        party_id = deterministic_uuid("card", "merchant", acquirer_id, merchant_id)
        identifier = PartyIdentifier(
            type=IdentifierType.MERCHANT_ID,
            value=f"{acquirer_id}:{merchant_id}",
            network="CARD",
            is_pseudonymous=False,
        )
        party_type = PartyType.FINANCIAL_INSTITUTION if channel == "atm" else PartyType.MERCHANT

        attributes: dict[str, Any] = {}
        if mcc:
            attributes["mcc"] = mcc
        if name:
            attributes["name"] = name
        if country:
            attributes["country"] = country
        if city:
            attributes["city"] = city

        return Party(
            party_id=party_id,
            identifiers=[identifier],
            party_type=party_type,
            home_network="CARD",
            country_code=country if (country and len(country) == 2) else None,
            created_at=received_at,
            attributes=attributes,
        )

    def _build_hops(
        self,
        acquirer_id: str,
        scheme_name: str | None,
        issuer_id: str | None,
        is_refund: bool,
    ) -> list[IntermediaryHop]:
        """Construct intermediary institutional routing hops along fund flow direction."""
        raw_hops: list[tuple[IntermediaryRole, str]] = []

        if is_refund:
            # Funds flow: acquirer -> scheme -> issuer
            raw_hops.append((IntermediaryRole.ACQUIRER, f"acq:{acquirer_id}"))
            if scheme_name:
                raw_hops.append((IntermediaryRole.SCHEME, f"scheme:{scheme_name}"))
            if issuer_id:
                raw_hops.append((IntermediaryRole.ISSUER, f"iss:{issuer_id}"))
        else:
            # Funds flow: issuer -> scheme -> acquirer
            if issuer_id:
                raw_hops.append((IntermediaryRole.ISSUER, f"iss:{issuer_id}"))
            if scheme_name:
                raw_hops.append((IntermediaryRole.SCHEME, f"scheme:{scheme_name}"))
            raw_hops.append((IntermediaryRole.ACQUIRER, f"acq:{acquirer_id}"))

        hops: list[IntermediaryHop] = []
        for idx, (role, ident_val) in enumerate(raw_hops, start=1):
            hop_ident = PartyIdentifier(
                type=IdentifierType.OTHER,
                value=ident_val,
                network="CARD",
            )
            hops.append(
                IntermediaryHop(
                    sequence=idx,
                    institution_identifier=hop_ident,
                    role=role,
                )
            )

        return hops

    def describe(self) -> dict[str, Any]:
        """Return serializable dictionary describing profile, version, and discarded fields."""
        data = super().describe()
        data["discarded_fields"] = list(self.discarded_fields)
        return data
