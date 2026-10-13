"""Tests for NetworkAdapter contract enforcement, batch normalization, and deterministic UUIDs."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

import pytest

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    PayloadFormat,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.errors import (
    AdapterRegistrationError,
    NormalizationError,
)
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import AssetClass, Money
from ripple.domain.party import Party

from ripple.domain.transaction import SettlementModel, Transaction, TransactionStatus

_SAMPLE_PROFILE = NetworkProfile(
    network_id="test_rail",
    display_name="Test Rail",
    description="Test rail for contract tests.",
    category=NetworkCategory.INSTANT_PAYMENT,
    settlement_model=SettlementModel.INSTANT,
    supported_identifier_types=[IdentifierType.VPA],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=["USD"],
    typical_settlement_seconds_min=0.1,
    typical_settlement_seconds_max=1.0,
    is_reversible=False,
    typical_reversal_window_seconds=None,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone="UTC",
    typical_fee_bps_range=None,
    regions=["US"],
    schema_version=1,
)


def test_subclass_missing_profile_fails_at_class_definition_time() -> None:
    """Assert that defining a NetworkAdapter subclass without 'profile' raises AdapterRegistrationError."""
    with pytest.raises(AdapterRegistrationError) as exc_info:

        class MissingProfileAdapter(NetworkAdapter):
            adapter_version: ClassVar[str] = "1.0.0"

            def can_handle(self, event: RawEvent) -> bool:
                return True

            def normalize(self, event: RawEvent) -> NormalizationResult:
                raise NotImplementedError

    assert "profile" in str(exc_info.value)
    assert "MissingProfileAdapter" in str(exc_info.value)


def test_subclass_missing_adapter_version_fails_at_class_definition_time() -> None:
    """Assert that defining a NetworkAdapter subclass without 'adapter_version' fails."""
    with pytest.raises(AdapterRegistrationError) as exc_info:

        class MissingVersionAdapter(NetworkAdapter):
            profile: ClassVar[NetworkProfile] = _SAMPLE_PROFILE

            def can_handle(self, event: RawEvent) -> bool:
                return True

            def normalize(self, event: RawEvent) -> NormalizationResult:
                raise NotImplementedError

    assert "adapter_version" in str(exc_info.value)


def test_subclass_valid_definition() -> None:
    """Assert that a valid NetworkAdapter subclass instantiates without error."""

    class ValidAdapter(NetworkAdapter):
        profile: ClassVar[NetworkProfile] = _SAMPLE_PROFILE
        adapter_version: ClassVar[str] = "1.2.3"

        def can_handle(self, event: RawEvent) -> bool:
            return event.network_id == "test_rail"

        def normalize(self, event: RawEvent) -> NormalizationResult:
            raise NotImplementedError

    adapter = ValidAdapter()
    assert adapter.profile.network_id == "test_rail"
    assert adapter.adapter_version == "1.2.3"

    desc = adapter.describe()
    assert desc["adapter_version"] == "1.2.3"
    assert desc["profile"]["network_id"] == "test_rail"


def test_deterministic_uuid_stability_and_network_differentiation() -> None:
    """Assert deterministic_uuid is consistent across repeated calls and differs across networks."""
    # Stability for identical network and parts
    id1 = deterministic_uuid("upi", "sender@bank", "tx_12345")
    id2 = deterministic_uuid("upi", "sender@bank", "tx_12345")
    assert isinstance(id1, UUID)
    assert id1 == id2

    # Different network ID produces distinct UUID even with identical parts
    id_card = deterministic_uuid("card", "sender@bank", "tx_12345")
    assert id1 != id_card

    # Different parts on same network produce distinct UUIDs
    id_different_part = deterministic_uuid("upi", "receiver@bank", "tx_12345")
    assert id1 != id_different_part


def test_normalize_batch_fault_isolation() -> None:
    """Assert normalize_batch processes all events and isolates failures without aborting."""
    sender_id = deterministic_uuid("test_rail", "sender_1")
    receiver_id = deterministic_uuid("test_rail", "receiver_1")

    sender_party = Party(

        party_id=sender_id,
        home_network="test_rail",
        identifiers=[
            PartyIdentifier(
                type=IdentifierType.VPA,
                value="alice@bank",
                network="test_rail",
            )
        ],
    )
    receiver_party = Party(
        party_id=receiver_id,
        home_network="test_rail",
        identifiers=[
            PartyIdentifier(
                type=IdentifierType.VPA,
                value="bob@bank",
                network="test_rail",
            )
        ],
    )

    class DummyBatchAdapter(NetworkAdapter):
        profile: ClassVar[NetworkProfile] = _SAMPLE_PROFILE
        adapter_version: ClassVar[str] = "1.0.0"

        def can_handle(self, event: RawEvent) -> bool:
            return True

        def normalize(self, event: RawEvent) -> NormalizationResult:
            if not isinstance(event.payload, dict) or event.payload.get("status") == "corrupt":
                raise NormalizationError(
                    "Corrupt event payload cannot be normalized",
                    field="payload",
                    reason="Flagged as corrupt",
                )

            tx_ref = str(event.payload.get("ref", "tx_0"))
            tx = Transaction(
                transaction_id=deterministic_uuid("test_rail", tx_ref),
                network_transaction_ref=tx_ref,
                network="test_rail",
                sender_id=sender_id,
                receiver_id=receiver_id,
                sender_identifier=sender_party.identifiers[0],
                receiver_identifier=receiver_party.identifiers[0],
                amount=Money(amount=Decimal("10.00"), currency="USD", scale=2),
                initiated_at=event.received_at,

                settled_at=event.received_at,
                observed_at=event.received_at,
                status=TransactionStatus.SETTLED,
                settlement_model=SettlementModel.INSTANT,
            )
            return NormalizationResult(
                transaction=tx,
                parties=[sender_party, receiver_party],
                correlation_key=f"test_rail:{tx_ref}",
                lifecycle_stage="settled",
                warnings=[],
                adapter_version=self.adapter_version,
            )

    adapter = DummyBatchAdapter()

    now = datetime.now(UTC)
    ev_good_1 = RawEvent(
        network_id="test_rail",
        payload={"ref": "tx_101", "status": "ok"},
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )
    ev_bad = RawEvent(
        network_id="test_rail",
        payload={"ref": "tx_bad", "status": "corrupt"},
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )
    ev_good_2 = RawEvent(
        network_id="test_rail",
        payload={"ref": "tx_102", "status": "ok"},
        payload_format=PayloadFormat.JSON,
        received_at=now,
    )

    batch_res = adapter.normalize_batch([ev_good_1, ev_bad, ev_good_2])

    assert batch_res.total_count == 3
    assert batch_res.success_count == 2
    assert batch_res.failure_count == 1
    assert batch_res.has_failures is True

    assert batch_res.successes[0].correlation_key == "test_rail:tx_101"
    assert batch_res.successes[1].correlation_key == "test_rail:tx_102"

    failed_event, exc = batch_res.failures[0]
    assert failed_event == ev_bad
    assert isinstance(exc, NormalizationError)
    assert exc.field == "payload"
