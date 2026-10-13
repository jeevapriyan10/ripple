"""Universal pass-through network adapter.

Normalizes pre-structured domain payloads that already adhere to Ripple's universal
domain schema. Used for direct API ingestion, synthetic generator benchmarking,
replay pipelines, and multi-rail testing without dedicated protocol decoding.
"""

from typing import ClassVar
from uuid import UUID

from pydantic import ValidationError

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    RawEvent,
)
from ripple.adapters.errors import NormalizationError
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.adapters.registry import register_adapter
from ripple.domain.errors import RippleDomainError
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.party import Party
from ripple.domain.transaction import SettlementModel, Transaction


@register_adapter
class UniversalAdapter(NetworkAdapter):
    """Pass-through adapter for universal pre-structured payloads.

    Accepts RawEvents whose payload is a dictionary containing:
        {
            "transaction": { ... Transaction fields ... },
            "parties": [ { ... Party fields ... }, ... ]
        }
    Validates structural invariants using the domain models, overrides the transaction's
    observed_at timestamp with event.received_at to measure ingestion pipeline latency,
    and returns a canonical NormalizationResult.
    """

    profile: ClassVar[NetworkProfile] = NetworkProfile(
        network_id="universal",
        display_name="Universal Pass-Through Network",
        description=(
            "Generic pass-through adapter for pre-normalized transaction and party payloads, "
            "supporting all identifier types and asset classes."
        ),
        category=NetworkCategory.OTHER,
        settlement_model=SettlementModel.INTERNAL_LEDGER,
        supported_identifier_types=list(IdentifierType),
        supported_asset_classes=[AssetClass.FIAT, AssetClass.CRYPTO],
        default_scale=2,
        supported_currencies=[],
        typical_settlement_seconds_min=0.0,
        typical_settlement_seconds_max=60.0,
        is_reversible=False,
        typical_reversal_window_seconds=None,
        intermediaries_visible=False,
        ledger_visibility=LedgerVisibility.PRIVATE,
        is_pseudonymous=False,
        operating_calendar=OperatingCalendar.ALWAYS_ON,
        operating_timezone="UTC",
        typical_fee_bps_range=None,
        regions=[],
        schema_version=1,
    )
    adapter_version: ClassVar[str] = "1.0.0"

    def can_handle(self, event: RawEvent) -> bool:
        """Verify event is targeted to 'universal' and payload contains a transaction dictionary."""
        return (
            event.network_id == "universal"
            and isinstance(event.payload, dict)
            and "transaction" in event.payload
        )

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Validate and normalize pre-structured transaction and party payloads.

        Overwrites transaction.observed_at with event.received_at, derives the correlation_key
        as 'universal:{transaction_id}', and returns a canonical NormalizationResult.
        """
        payload = event.payload
        if not isinstance(payload, dict):
            raise NormalizationError(
                "Payload must be a dictionary containing 'transaction' and 'parties'.",
                field="payload",
                reason="Payload is not a dictionary",
            )

        # 1. Parse and validate Transaction
        if "transaction" not in payload:
            raise NormalizationError(
                "Payload is missing the mandatory 'transaction' object.",
                field="transaction",
                reason="Missing 'transaction' key in payload",
            )

        tx_raw = payload["transaction"]
        if not isinstance(tx_raw, dict):
            raise NormalizationError(
                "Payload field 'transaction' must be a dictionary.",
                field="transaction",
                reason="Field 'transaction' is not a dictionary",
            )

        try:
            transaction = Transaction.model_validate(tx_raw)
        except ValidationError as exc:
            first_err = exc.errors()[0]
            loc_str = ".".join(str(loc) for loc in first_err["loc"])
            failing_field = f"transaction.{loc_str}" if loc_str else "transaction"
            raise NormalizationError(
                f"Transaction validation failed: {first_err['msg']}",
                field=failing_field,
                reason=first_err["msg"],
            ) from exc
        except RippleDomainError as exc:
            raise NormalizationError(
                f"Transaction domain invariant violated: {exc}",
                field="transaction",
                reason=str(exc),
            ) from exc

        # Overwrite observed_at with the exact moment Ripple received the raw event
        transaction.observed_at = event.received_at

        # 2. Parse and validate Parties
        if "parties" not in payload:
            raise NormalizationError(
                "Payload is missing the mandatory 'parties' list.",
                field="parties",
                reason="Missing 'parties' key in payload",
            )

        parties_raw = payload["parties"]
        if not isinstance(parties_raw, list):
            raise NormalizationError(
                "Payload field 'parties' must be a list of Party objects.",
                field="parties",
                reason="Field 'parties' is not a list",
            )

        parties: list[Party] = []
        seen_party_ids: set[UUID] = set()

        for idx, party_data in enumerate(parties_raw):
            if not isinstance(party_data, dict):
                raise NormalizationError(
                    f"Party at index {idx} must be a dictionary.",
                    field=f"parties[{idx}]",
                    reason="Party entry is not a dictionary",
                )
            try:
                party = Party.model_validate(party_data)
            except ValidationError as exc:
                first_err = exc.errors()[0]
                loc_str = ".".join(str(loc) for loc in first_err["loc"])
                failing_field = f"parties[{idx}].{loc_str}" if loc_str else f"parties[{idx}]"
                raise NormalizationError(
                    f"Party validation failed at index {idx}: {first_err['msg']}",
                    field=failing_field,
                    reason=first_err["msg"],
                ) from exc
            except RippleDomainError as exc:
                raise NormalizationError(
                    f"Party domain invariant violated at index {idx}: {exc}",
                    field=f"parties[{idx}]",
                    reason=str(exc),
                ) from exc

            # Deduplicate by party_id preserving insertion order
            if party.party_id not in seen_party_ids:
                seen_party_ids.add(party.party_id)
                parties.append(party)

        # Invariant check: sender and receiver must be in parties
        if transaction.sender_id not in seen_party_ids:
            raise NormalizationError(
                f"Transaction sender_id '{transaction.sender_id}' not found in parties list.",
                field="parties",
                reason=f"Transaction sender_id '{transaction.sender_id}' missing from parties list",
            )

        if transaction.receiver_id not in seen_party_ids:
            raise NormalizationError(
                f"Transaction receiver_id '{transaction.receiver_id}' not found in parties list.",
                field="parties",
                reason=(
                    f"Transaction receiver_id '{transaction.receiver_id}' "
                    "missing from parties list"
                ),
            )

        correlation_key = f"universal:{transaction.transaction_id}"
        lifecycle_stage = transaction.status.value.lower()

        return NormalizationResult(
            transaction=transaction,
            parties=parties,
            correlation_key=correlation_key,
            lifecycle_stage=lifecycle_stage,
            warnings=[],
            adapter_version=self.adapter_version,
        )
