"""Tests for generator configuration, presets, and validation rules."""

import pytest

from ripple.generator.config import (
    DriftConfig,
    ForcedStart,
    FraudConfig,
    GeneratorConfig,
)


def test_config_defaults_and_presets() -> None:
    """Assert presets construct valid configs with expected parameters."""
    cfg_tiny = GeneratorConfig.from_preset("tiny")
    assert cfg_tiny.actors == 200
    assert cfg_tiny.days == 2

    cfg_small = GeneratorConfig.from_preset("small")
    assert cfg_small.actors == 2000
    assert cfg_small.days == 7

    cfg_demo = GeneratorConfig.from_preset("demo")
    assert cfg_demo.actors == 5000
    assert cfg_demo.days == 30

    cfg_large = GeneratorConfig.from_preset("large")
    assert cfg_large.actors == 50000
    assert cfg_large.days == 30

    # Overrides work on presets
    cfg_custom = GeneratorConfig.from_preset("tiny", actors=350, seed=123)
    assert cfg_custom.actors == 350
    assert cfg_custom.seed == 123
    assert cfg_custom.days == 2


def test_canonical_json_and_config_hash() -> None:
    """Assert canonical JSON and SHA-256 hash are deterministic across instances."""
    cfg1 = GeneratorConfig(seed=42, actors=500, days=5)
    cfg2 = GeneratorConfig(seed=42, actors=500, days=5)

    assert cfg1.canonical_json() == cfg2.canonical_json()
    assert cfg1.config_hash() == cfg2.config_hash()
    assert len(cfg1.config_hash()) == 64

    # Changing a parameter modifies hash
    cfg3 = GeneratorConfig(seed=43, actors=500, days=5)
    assert cfg1.config_hash() != cfg3.config_hash()


def test_network_validation() -> None:
    """Assert universal adapter is rejected and unknown networks raise error."""
    with pytest.raises(ValueError, match="universal"):
        GeneratorConfig(networks=["universal", "upi"])

    with pytest.raises(ValueError, match="Unsupported network"):
        GeneratorConfig(networks=["bitcoin_lightning"])

    # Valid networks pass
    cfg = GeneratorConfig(networks=["upi", "card"])
    assert cfg.networks == ["card", "upi"]  # Sorted


def test_drift_shift_day_validation() -> None:
    """Assert drift.shift_day must fall strictly between 0 and total days."""
    with pytest.raises(ValueError, match="drift.shift_day"):
        GeneratorConfig(days=10, drift=DriftConfig(enabled=True, shift_day=12))

    with pytest.raises(ValueError, match="drift.shift_day"):
        GeneratorConfig(days=10, drift=DriftConfig(enabled=True, shift_day=0))

    # Valid shift day
    cfg = GeneratorConfig(days=10, drift=DriftConfig(enabled=True, shift_day=5))
    assert cfg.drift.shift_day == 5


def test_scenario_validation_and_warnings() -> None:
    """Assert unknown scenarios rejected and incompatible scenarios dropped with warnings."""
    # Unknown scenario ID rejected in mix
    with pytest.raises(ValueError, match="Unknown scenario ID"):
        GeneratorConfig(fraud=FraudConfig(scenario_mix={"unknown_typology": 1.0}))

    # Unknown scenario in forced starts rejected
    with pytest.raises(ValueError, match="Unknown scenario ID"):
        GeneratorConfig(
            fraud=FraudConfig(forced_starts=[ForcedStart(scenario="fake_sc", at_hour=5)])
        )

    # Incompatible scenario dropped when network disabled
    # cross_network_laundering requires wallet, swift, crypto
    cfg = GeneratorConfig(
        networks=["upi", "card"],
        fraud=FraudConfig(scenario_mix={"cross_network_laundering": 1.0}),
    )
    assert len(cfg.resolved_warnings) > 0
    assert "cross_network_laundering" in cfg.resolved_warnings[0]
