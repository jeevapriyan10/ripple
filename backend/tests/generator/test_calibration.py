"""Tests for calibration profiles, honesty rules, provenance tracking, and overrides."""

import pytest

from ripple.generator.calibration import (
    CalibrationError,
    CalibrationProfile,
    load_calibration_profile,
)


def test_default_profile_honesty_guarantees() -> None:
    """Rule R4 Invariant: assert every parameter is an assumption with citation null."""
    calib = load_calibration_profile("default")
    summary = calib.summary()

    assert summary["assumption"] > 0
    assert summary["published"] == 0

    # Inspect all flattened parameters
    for key, p in calib._flat_params.items():
        assert p["provenance"] == "assumption", f"Parameter {key} is not assumption"
        assert p["citation"] is None, f"Parameter {key} has non-null citation"


def test_published_without_citation_rejected() -> None:
    """Assert loader raises CalibrationError when published has missing citation."""
    bad_data = {
        "rates": {
            "fraud_rate": {
                "value": 0.01,
                "unit": "ratio",
                "provenance": "published",
                "citation": None,  # Prohibited!
                "note": "Missing citation",
            }
        }
    }
    with pytest.raises(CalibrationError, match="missing citation"):
        CalibrationProfile(bad_data, name="invalid")


def test_parameter_access_and_unused_tracking() -> None:
    """Assert access logging tracks queried keys and reports unused keys."""
    calib = load_calibration_profile("default")

    # Before accessing world.individual_ratio
    unused_before = calib.unused_parameters()
    assert "world.individual_ratio" in unused_before

    # Access parameter
    val = calib.get("world.individual_ratio")
    assert val == 0.85

    unused_after = calib.unused_parameters()
    assert "world.individual_ratio" not in unused_after


def test_calibration_overrides() -> None:
    """Assert overrides create a new immutable profile without mutating original."""
    calib = load_calibration_profile("default")
    original_val = calib.get("world.individual_ratio")

    overridden = calib.override({"world.individual_ratio": 0.70})

    assert calib.get("world.individual_ratio") == original_val
    assert overridden.get("world.individual_ratio") == 0.70
    assert overridden.name == "default_override"
