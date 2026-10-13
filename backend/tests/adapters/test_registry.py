"""Tests for AdapterRegistry, routing, concurrency, and configuration filtering."""

from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar

import pytest

from ripple.adapters.contract import NetworkAdapter, NormalizationResult, PayloadFormat, RawEvent
from ripple.adapters.errors import (
    DuplicateAdapterError,
    UnknownNetworkError,
)
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.adapters.registry import (
    AdapterRegistry,
    load_enabled_adapters,
    register_adapter,
)
from ripple.config import Settings
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel


def make_test_profile(network_id: str) -> NetworkProfile:
    """Helper creating a minimal valid NetworkProfile for test adapters."""
    return NetworkProfile(
        network_id=network_id,
        display_name=f"Profile for {network_id}",
        description=f"Test description for {network_id}",
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


def make_dummy_adapter(network_id: str, version: str = "1.0.0") -> type[NetworkAdapter]:
    """Dynamically construct a unique dummy NetworkAdapter subclass for testing."""
    target_profile = make_test_profile(network_id)

    class DummyAdapter(NetworkAdapter):
        profile: ClassVar[NetworkProfile] = target_profile
        adapter_version: ClassVar[str] = version

        def can_handle(self, event: RawEvent) -> bool:
            return event.network_id == network_id

        def normalize(self, event: RawEvent) -> NormalizationResult:
            raise NotImplementedError

    DummyAdapter.__name__ = f"Dummy_{network_id.capitalize()}"
    return DummyAdapter



def test_registry_register_get_unregister() -> None:
    """Assert registering, retrieving, and unregistering adapters work as expected."""
    registry = AdapterRegistry()
    adapter_cls = make_dummy_adapter("test_rail_1")

    # Registration
    instance = registry.register(adapter_cls)
    assert registry.has("test_rail_1")
    assert registry.get("test_rail_1") is instance
    assert "test_rail_1" in registry.network_ids()

    profiles = registry.list_profiles()
    assert len(profiles) == 1
    assert profiles[0].network_id == "test_rail_1"

    # Unregistration
    removed = registry.unregister("test_rail_1")
    assert removed is instance
    assert not registry.has("test_rail_1")
    assert registry.network_ids() == []


def test_duplicate_adapter_rejection_and_replace() -> None:
    """Assert registering duplicate network_id raises DuplicateAdapterError unless replace=True."""
    registry = AdapterRegistry()
    adapter_v1 = make_dummy_adapter("dup_rail", version="1.0.0")
    adapter_v2 = make_dummy_adapter("dup_rail", version="2.0.0")

    registry.register(adapter_v1)

    with pytest.raises(DuplicateAdapterError) as exc_info:
        registry.register(adapter_v2)
    assert "dup_rail" in str(exc_info.value)
    assert exc_info.value.network_id == "dup_rail"

    # With replace=True, registration succeeds and overwrites
    new_instance = registry.register(adapter_v2, replace=True)
    assert registry.get("dup_rail").adapter_version == "2.0.0"
    assert registry.get("dup_rail") is new_instance


def test_unknown_network_error_lists_available_ids() -> None:
    """Assert get() on an unregistered network_id raises UnknownNetworkError listing available IDs."""
    registry = AdapterRegistry()
    registry.register(make_dummy_adapter("alpha"))
    registry.register(make_dummy_adapter("beta"))

    with pytest.raises(UnknownNetworkError) as exc_info:
        registry.get("gamma")

    err = exc_info.value
    assert err.network_id == "gamma"
    assert "gamma" in str(err)
    assert "alpha" in str(err)
    assert "beta" in str(err)
    assert err.available_ids == ["alpha", "beta"]


def test_route_uses_network_id_strictly() -> None:
    """Assert route() looks up strictly by event.network_id and raises on unknown networks."""
    from datetime import UTC, datetime

    registry = AdapterRegistry()
    alpha_adapter = registry.register(make_dummy_adapter("alpha"))

    event_alpha = RawEvent(
        network_id="alpha",
        payload={"foo": "bar"},
        payload_format=PayloadFormat.JSON,
        received_at=datetime.now(UTC),
    )
    routed = registry.route(event_alpha)
    assert routed is alpha_adapter

    event_unknown = RawEvent(
        network_id="unknown_rail",
        payload={"foo": "bar"},
        payload_format=PayloadFormat.JSON,
        received_at=datetime.now(UTC),
    )
    with pytest.raises(UnknownNetworkError):
        registry.route(event_unknown)


def test_thread_safety_concurrent_registrations() -> None:
    """Assert AdapterRegistry handles concurrent registrations from multiple threads without race conditions."""
    registry = AdapterRegistry()
    adapter_classes = [make_dummy_adapter(f"thread_rail_{i}") for i in range(25)]

    def register_worker(cls: type[NetworkAdapter]) -> None:
        registry.register(cls)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(register_worker, adapter_classes))

    assert len(registry.network_ids()) == 25
    assert len(registry.list_profiles()) == 25
    for i in range(25):
        assert registry.has(f"thread_rail_{i}")


def test_enabled_networks_filtering_all(caplog: pytest.LogCaptureFixture) -> None:
    """Assert ENABLED_NETWORKS='all' preserves all registered adapters."""
    registry = AdapterRegistry()
    registry.register(make_dummy_adapter("rail_a"))
    registry.register(make_dummy_adapter("rail_b"))

    settings = Settings(ENABLED_NETWORKS="all")
    load_enabled_adapters(settings=settings, registry=registry)

    assert "rail_a" in registry.network_ids()
    assert "rail_b" in registry.network_ids()
    assert "universal" in registry.network_ids()


def test_enabled_networks_filtering_subset_and_unknown_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Assert ENABLED_NETWORKS filters to specified IDs and logs a warning for unknown IDs without crashing."""
    registry = AdapterRegistry()
    registry.register(make_dummy_adapter("rail_a"))
    registry.register(make_dummy_adapter("rail_b"))

    # Enabled list requests rail_a, non_existent_rail
    settings = Settings(ENABLED_NETWORKS="rail_a, non_existent_rail")

    with caplog.at_level("WARNING"):
        load_enabled_adapters(settings=settings, registry=registry)

    # rail_a is kept, rail_b was filtered out
    assert registry.has("rail_a")
    assert not registry.has("rail_b")

    # non_existent_rail triggered a warning log, not a crash
    assert any("non_existent_rail" in record.message for record in caplog.records)


def test_decorator_registration() -> None:
    """Assert @register_adapter registers an adapter into the target registry."""
    custom_registry = AdapterRegistry()

    @register_adapter(registry=custom_registry)
    class DecoratedAdapter(NetworkAdapter):
        profile: ClassVar[NetworkProfile] = make_test_profile("decorated_rail")
        adapter_version: ClassVar[str] = "1.0.0"

        def can_handle(self, event: RawEvent) -> bool:
            return True

        def normalize(self, event: RawEvent) -> NormalizationResult:
            raise NotImplementedError

    assert custom_registry.has("decorated_rail")
    assert isinstance(custom_registry.get("decorated_rail"), DecoratedAdapter)
