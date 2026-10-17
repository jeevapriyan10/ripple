"""Tests for NetworkProfile model validation and domain invariants."""

import pytest
from pydantic import ValidationError

from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel


def make_valid_profile_kwargs(**overrides: object) -> dict[str, object]:
    """Helper returning valid default kwargs for constructing a NetworkProfile."""
    defaults: dict[str, object] = {
        "network_id": "test_net",
        "display_name": "Test Network",
        "description": "Test network description for test cases.",
        "category": NetworkCategory.INSTANT_PAYMENT,
        "settlement_model": SettlementModel.INSTANT,
        "supported_identifier_types": [IdentifierType.VPA, IdentifierType.PHONE],
        "supported_asset_classes": [AssetClass.FIAT],
        "default_scale": 2,
        "supported_currencies": ["INR"],
        "typical_settlement_seconds_min": 0.5,
        "typical_settlement_seconds_max": 3.0,
        "is_reversible": False,
        "typical_reversal_window_seconds": None,
        "intermediaries_visible": False,
        "ledger_visibility": LedgerVisibility.PRIVATE,
        "is_pseudonymous": False,
        "operating_calendar": OperatingCalendar.ALWAYS_ON,
        "operating_timezone": "UTC",
        "typical_fee_bps_range": (1.0, 5.0),
        "regions": ["IN"],
        "schema_version": 1,
    }
    defaults.update(overrides)
    return defaults


def test_profile_valid_construction() -> None:
    """Assert that a properly formed NetworkProfile instantiates successfully."""
    kwargs = make_valid_profile_kwargs()
    profile = NetworkProfile(**kwargs)  # type: ignore[arg-type]
    assert profile.network_id == "test_net"
    assert profile.is_reversible is False
    assert profile.typical_reversal_window_seconds is None


@pytest.mark.parametrize(
    "valid_slug",
    [
        "upi",
        "card_visa",
        "swift_wire",
        "net1",
        "a_1_b_2",
        "network_with_exactly_32_chars__",
    ],
)
def test_network_id_slug_valid(valid_slug: str) -> None:
    """Assert valid lowercase slugs pass validation."""
    kwargs = make_valid_profile_kwargs(network_id=valid_slug)
    profile = NetworkProfile(**kwargs)  # type: ignore[arg-type]
    assert profile.network_id == valid_slug


@pytest.mark.parametrize(
    "invalid_slug",
    [
        "UPI",  # Uppercase disallowed
        "1net",  # Must start with lowercase letter
        "_net",  # Cannot start with underscore
        "net-work",  # Hyphens disallowed
        "net work",  # Spaces disallowed
        "a",  # Too short (min 2 chars: ^[a-z][a-z0-9_]{1,31}$)
        "a" * 33,  # Too long (> 32 chars)
        "",  # Empty string
    ],
)
def test_network_id_slug_invalid(invalid_slug: str) -> None:
    """Assert invalid network_id slugs are rejected by validation."""
    kwargs = make_valid_profile_kwargs(network_id=invalid_slug)
    with pytest.raises(ValidationError) as exc_info:
        NetworkProfile(**kwargs)  # type: ignore[arg-type]
    assert "network_id" in str(exc_info.value)


def test_settlement_latency_min_max_validator() -> None:
    """Assert typical_settlement_seconds_min <= typical_settlement_seconds_max is enforced."""
    # min == max is valid
    kwargs_equal = make_valid_profile_kwargs(
        typical_settlement_seconds_min=2.0,
        typical_settlement_seconds_max=2.0,
    )
    profile = NetworkProfile(**kwargs_equal)  # type: ignore[arg-type]
    assert profile.typical_settlement_seconds_min == 2.0
    assert profile.typical_settlement_seconds_max == 2.0

    # min > max must fail
    kwargs_inverted = make_valid_profile_kwargs(
        typical_settlement_seconds_min=5.0,
        typical_settlement_seconds_max=2.0,
    )
    with pytest.raises(ValidationError) as exc_info:
        NetworkProfile(**kwargs_inverted)  # type: ignore[arg-type]
    assert "typical_settlement_seconds_min" in str(exc_info.value)


def test_reversible_reversal_window_validator() -> None:
    """Assert typical_reversal_window_seconds must be None when is_reversible is False."""
    # is_reversible=True with a window is valid
    kwargs_rev = make_valid_profile_kwargs(
        is_reversible=True,
        typical_reversal_window_seconds=86400,
    )
    profile = NetworkProfile(**kwargs_rev)  # type: ignore[arg-type]
    assert profile.is_reversible is True
    assert profile.typical_reversal_window_seconds == 86400

    # is_reversible=False with window must raise ValidationError
    kwargs_invalid = make_valid_profile_kwargs(
        is_reversible=False,
        typical_reversal_window_seconds=3600,
    )
    with pytest.raises(ValidationError) as exc_info:
        NetworkProfile(**kwargs_invalid)  # type: ignore[arg-type]
    assert "typical_reversal_window_seconds must be None" in str(exc_info.value)


@pytest.mark.parametrize(
    "valid_tz",
    ["UTC", "America/New_York", "Asia/Kolkata", "Europe/London", None],
)
def test_operating_timezone_valid(valid_tz: str | None) -> None:
    """Assert valid IANA timezones and None pass validation."""
    kwargs = make_valid_profile_kwargs(operating_timezone=valid_tz)
    profile = NetworkProfile(**kwargs)  # type: ignore[arg-type]
    assert profile.operating_timezone == valid_tz


@pytest.mark.parametrize(
    "invalid_tz",
    ["Mars/Base", "Invalid/Timezone", "GMT+5", "FakeTZ"],
)
def test_operating_timezone_invalid(invalid_tz: str) -> None:
    """Assert invalid timezone strings are rejected."""
    kwargs = make_valid_profile_kwargs(operating_timezone=invalid_tz)
    with pytest.raises(ValidationError) as exc_info:
        NetworkProfile(**kwargs)  # type: ignore[arg-type]
    assert "operating_timezone" in str(exc_info.value)


def test_regions_validation() -> None:
    """Assert region codes must be uppercase 2-letter ISO-3166 alpha-2 strings."""
    # Valid empty (global) and valid country codes
    profile_global = NetworkProfile(**make_valid_profile_kwargs(regions=[]))  # type: ignore[arg-type]
    assert profile_global.regions == []

    profile_multi = NetworkProfile(**make_valid_profile_kwargs(regions=["US", "GB", "IN"]))  # type: ignore[arg-type]
    assert profile_multi.regions == ["US", "GB", "IN"]

    # Invalid region formats
    for bad_region in [["USA"], ["us"], ["12"], ["U"]]:
        with pytest.raises(ValidationError) as exc_info:
            NetworkProfile(**make_valid_profile_kwargs(regions=bad_region))  # type: ignore[arg-type]
        assert "Region code" in str(exc_info.value)


def test_settlement_model_intermediaries_unconstrained() -> None:
    """Assert CORRESPONDENT_CHAIN and AUTH_CAPTURE permit intermediaries_visible True or False."""
    p_chain_true = NetworkProfile(
        **make_valid_profile_kwargs(  # type: ignore[arg-type]
            settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
            intermediaries_visible=True,
        )
    )
    assert p_chain_true.intermediaries_visible is True

    p_chain_false = NetworkProfile(
        **make_valid_profile_kwargs(  # type: ignore[arg-type]
            settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
            intermediaries_visible=False,
        )
    )
    assert p_chain_false.intermediaries_visible is False
