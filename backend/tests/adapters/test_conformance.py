"""Adapter conformance suite integration tests (Part B2).

Executes the 21 conformance checks against each registered adapter using its
data-driven conformance manifest and foreign cross-network cases.
"""

from pathlib import Path

import pytest

from ripple.adapters.registry import AdapterRegistry, load_enabled_adapters
from ripple.adapters.testing import (
    ConformanceCase,
    ConformanceOptions,
    load_manifest,
    run_conformance,
)
from ripple.config import Settings

FIXTURES_ROOT = Path(__file__).parent / "fixtures"
TEST_KEY = "test_pan_token_key_for_unit_tests_32chars_min"

# Load manifests for all 6 networks once at test module load
MANIFEST_DATA: dict[str, tuple[ConformanceOptions, list[ConformanceCase]]] = {}
ALL_NETWORKS = ["universal", "upi", "card", "crypto", "swift", "wallet"]

for nid in ALL_NETWORKS:
    manifest_path = FIXTURES_ROOT / nid / "conformance.json"
    if manifest_path.exists():
        opts, cases = load_manifest(manifest_path)
        MANIFEST_DATA[nid] = (opts, cases)


def get_fresh_registry() -> AdapterRegistry:
    """Create a fresh registry loaded with all adapters under dev test settings."""
    reg = AdapterRegistry()
    dev_settings = Settings(
        ENABLED_NETWORKS="all",
        CARD_PAN_TOKEN_KEY=TEST_KEY,
        APP_ENV="development",
    )
    load_enabled_adapters(settings=dev_settings, registry=reg)
    return reg


@pytest.fixture(scope="module")
def shared_registry() -> AdapterRegistry:
    """Module-scoped fresh registry."""
    return get_fresh_registry()


@pytest.mark.conformance
@pytest.mark.parametrize("network_id", ALL_NETWORKS)
def test_adapter_conformance(network_id: str, shared_registry: AdapterRegistry) -> None:
    """Assert each registered adapter passes all 21 conformance checks."""
    assert shared_registry.has(network_id), f"Adapter for network '{network_id}' not registered."
    adapter = shared_registry.get(network_id)

    assert network_id in MANIFEST_DATA, f"Manifest not found for network '{network_id}'."
    options, cases = MANIFEST_DATA[network_id]

    # Collect foreign ok cases from all other adapter manifests
    foreign_cases: list[ConformanceCase] = []
    for other_id, (_, other_cases) in MANIFEST_DATA.items():
        if other_id != network_id:
            foreign_cases.extend([c for c in other_cases if c.expect == "ok"])

    report = run_conformance(
        adapter=adapter,
        cases=cases,
        options=options,
        foreign_cases=foreign_cases,
    )

    # Output detailed markdown report on failure via assert_conformant
    report.assert_conformant()
