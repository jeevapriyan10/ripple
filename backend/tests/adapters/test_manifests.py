"""Verification tests for adapter conformance manifests.

Guarantees every registered adapter maintains an authoritative, valid
conformance manifest with fixture integrity and minimum test case coverage.
"""

from __future__ import annotations

from pathlib import Path

from ripple.adapters.registry import AdapterRegistry, load_enabled_adapters
from ripple.adapters.testing import ConformanceOptions, load_manifest
from ripple.config import Settings

FIXTURES_ROOT = Path(__file__).parent / "fixtures"


def test_every_registered_adapter_has_valid_manifest() -> None:
    """Assert every adapter in a loaded registry provides a valid conformance.json manifest."""
    settings = Settings(
        ENABLED_NETWORKS="all",
        CARD_PAN_TOKEN_KEY="testing_card_pan_token_key_min_32_characters_long",
    )
    reg = AdapterRegistry()
    load_enabled_adapters(settings=settings, registry=reg)
    registered_ids = reg.network_ids()

    for net_id in registered_ids:
        manifest_path = FIXTURES_ROOT / net_id / "conformance.json"
        assert manifest_path.is_file(), (
            f"Missing required conformance manifest for registered adapter '{net_id}' "
            f"at: {manifest_path}"
        )

        options, cases = load_manifest(manifest_path)
        assert isinstance(options, ConformanceOptions)

        ok_cases = [c for c in cases if c.expect == "ok"]
        error_cases = [c for c in cases if c.expect == "error"]

        assert (
            len(ok_cases) >= 1
        ), f"Manifest for '{net_id}' must define at least one ok case, found {len(ok_cases)}."
        assert len(error_cases) >= 2, (
            f"Manifest for '{net_id}' must define at least two error cases, "
            f"found {len(error_cases)}."
        )

        # Assert every case has a non-empty payload loaded from its fixture
        for case in cases:
            assert isinstance(case.payload, dict), f"Case '{case.name}' payload is not a dict."
            assert len(case.payload) > 0, f"Case '{case.name}' loaded an empty payload."
