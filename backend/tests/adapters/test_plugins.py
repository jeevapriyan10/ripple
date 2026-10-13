"""Tests for external plugin discovery via entry points."""

from typing import Any, ClassVar
from unittest.mock import MagicMock

import pytest

from ripple.adapters.contract import NetworkAdapter, NormalizationResult, RawEvent
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.adapters.registry import AdapterRegistry, discover_plugins
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

_PLUGIN_PROFILE = NetworkProfile(
    network_id="plugin_rail",
    display_name="External Plugin Rail",
    description="Plugin adapter loaded via setuptools entry points.",
    category=NetworkCategory.OTHER,
    settlement_model=SettlementModel.INTERNAL_LEDGER,
    supported_identifier_types=[IdentifierType.WALLET_USER_ID],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=["EUR"],
    typical_settlement_seconds_min=0.1,
    typical_settlement_seconds_max=2.0,
    is_reversible=False,
    typical_reversal_window_seconds=None,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone="UTC",
    typical_fee_bps_range=None,
    regions=["FR"],
    schema_version=1,
)


class PluginValidAdapter(NetworkAdapter):
    """Fake valid external adapter for plugin discovery test."""

    profile: ClassVar[NetworkProfile] = _PLUGIN_PROFILE
    adapter_version: ClassVar[str] = "3.2.1"

    def can_handle(self, event: RawEvent) -> bool:
        return event.network_id == "plugin_rail"

    def normalize(self, event: RawEvent) -> NormalizationResult:
        raise NotImplementedError


class NotAnAdapter:
    """Class that does not inherit from NetworkAdapter."""

    pass


def test_plugin_discovery_resilience_and_reporting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Assert discovery successfully registers valid plugins, isolates errors, and never raises."""
    # 1. Valid entry point that loads cleanly
    ep_valid = MagicMock()
    ep_valid.name = "valid_plugin"
    ep_valid.load.return_value = PluginValidAdapter

    # 2. Failing entry point that raises an Exception on load
    ep_failing = MagicMock()
    ep_failing.name = "broken_plugin"
    ep_failing.load.side_effect = RuntimeError("SyntaxError in external plugin package")

    # 3. Invalid entry point that returns a class not subclassing NetworkAdapter
    ep_wrong_type = MagicMock()
    ep_wrong_type.name = "invalid_type_plugin"
    ep_wrong_type.load.return_value = NotAnAdapter

    mock_entry_points: list[Any] = [ep_valid, ep_failing, ep_wrong_type]

    def mock_eps(group: str) -> list[Any]:
        if group == "ripple.adapters":
            return mock_entry_points
        return []

    monkeypatch.setattr("importlib.metadata.entry_points", mock_eps)

    registry = AdapterRegistry()
    report = discover_plugins(registry=registry)

    # Assert no unhandled crash occurred
    assert "valid_plugin" in report.loaded
    assert len(report.loaded) == 1

    # Both failing and invalid-type plugins appear in report.failed
    failed_names = [f[0] for f in report.failed]
    assert "broken_plugin" in failed_names
    assert "invalid_type_plugin" in failed_names

    # The valid plugin is registered in the registry
    assert registry.has("plugin_rail")
    adapter = registry.get("plugin_rail")
    assert adapter.adapter_version == "3.2.1"
