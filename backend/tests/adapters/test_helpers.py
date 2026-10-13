"""Unit tests for shared adapter helpers (backend/ripple/adapters/helpers.py)."""

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.errors import (
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.helpers import (
    parse_amount,
    parse_datetime,
    payload_as_dict,
    require,
)


def _make_event(payload: object) -> RawEvent:
    return RawEvent(
        network_id="test",
        payload=payload,  # type: ignore[arg-type]
        payload_format=PayloadFormat.JSON,
        received_at=datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC),
    )


def test_payload_as_dict_success_cases() -> None:
    """Assert payload_as_dict parses dict, JSON str, and JSON bytes without floats."""
    d = {"a": "1", "b": 2}
    assert payload_as_dict(_make_event(d)) == d

    s = '{"amount": "100.50", "raw_num": 42.99}'
    res_s = payload_as_dict(_make_event(s))
    assert res_s["amount"] == "100.50"
    assert isinstance(res_s["raw_num"], Decimal)
    assert res_s["raw_num"] == Decimal("42.99")

    b = json.dumps({"key": "val"}).encode("utf-8")
    assert payload_as_dict(_make_event(b)) == {"key": "val"}


def test_payload_as_dict_failure_cases() -> None:
    """Assert payload_as_dict rejects invalid json, non-mappings, and bad types."""
    with pytest.raises(UnsupportedPayloadError) as exc1:
        payload_as_dict(_make_event("not json {[[}"))
    assert "Malformed JSON payload" in str(exc1.value)

    with pytest.raises(UnsupportedPayloadError):
        payload_as_dict(_make_event(b"\xff\xfe\x00"))

    with pytest.raises(UnsupportedPayloadError) as exc2:
        payload_as_dict(_make_event("[1, 2, 3]"))
    assert "must be an object" in str(exc2.value)

    from unittest.mock import MagicMock

    with pytest.raises(UnsupportedPayloadError) as exc3:
        payload_as_dict(MagicMock(payload=12345))
    assert "Unsupported payload type" in str(exc3.value)


def test_require_helper() -> None:
    """Assert require returns existing non-empty values and rejects missing or empty."""
    payload = {"foo": "bar", "empty_str": "  ", "none_val": None, "empty_lst": []}

    assert require(payload, "foo") == "bar"
    assert require(payload, "foo", "custom.path") == "bar"

    with pytest.raises(NormalizationError) as exc_missing:
        require(payload, "bar")
    assert exc_missing.value.field == "bar"

    with pytest.raises(NormalizationError) as exc_empty:
        require(payload, "empty_str")
    assert exc_empty.value.field == "empty_str"

    with pytest.raises(NormalizationError):
        require(payload, "none_val")

    with pytest.raises(NormalizationError):
        require(payload, "empty_lst")


def test_parse_amount_valid_and_quantization() -> None:
    """Assert parse_amount correctly parses strings, ints, Decimals and quantizes."""
    assert parse_amount("100.50", scale=2) == Decimal("100.50")
    assert parse_amount("100", scale=2) == Decimal("100.00")
    assert parse_amount(100, scale=2) == Decimal("100.00")
    assert parse_amount(Decimal("50.25"), scale=2) == Decimal("50.25")
    assert parse_amount("1000", scale=0) == Decimal("1000")


def test_parse_amount_rejections() -> None:
    """Assert parse_amount strictly rejects floats, negatives, non-numerics, and NaN/inf."""
    with pytest.raises(NormalizationError) as exc_float:
        parse_amount(10.50)  # type: ignore[arg-type]
    assert "must not be a float" in str(exc_float.value)

    with pytest.raises(NormalizationError) as exc_type:
        parse_amount(["100"])  # type: ignore[arg-type]
    assert "must be a string, integer, or Decimal" in str(exc_type.value)

    with pytest.raises(NormalizationError):
        parse_amount("  ")

    with pytest.raises(NormalizationError):
        parse_amount("abc")

    with pytest.raises(NormalizationError) as exc_neg:
        parse_amount("-50.00")
    assert "cannot be negative" in str(exc_neg.value)

    with pytest.raises(NormalizationError):
        parse_amount("NaN")
    with pytest.raises(NormalizationError):
        parse_amount("Infinity")
    with pytest.raises(NormalizationError):
        parse_amount("-Infinity")

    with pytest.raises(NormalizationError) as exc_scale:
        parse_amount("100.123", scale=2)
    assert "more decimal places than allowed" in str(exc_scale.value)


def test_parse_datetime_valid() -> None:
    """Assert parse_datetime handles ISO-8601 strings and datetimes."""
    dt = datetime(2026, 10, 13, 14, 30, 0, tzinfo=UTC)
    parsed_dt, warn = parse_datetime(dt)
    assert parsed_dt == dt
    assert warn is None

    iso_str = "2026-10-13T14:30:00+00:00"
    parsed_iso, warn_iso = parse_datetime(iso_str)
    assert parsed_iso == dt
    assert warn_iso is None

    naive_str = "2026-10-13T20:00:00"
    parsed_ist, warn_ist = parse_datetime(naive_str, assume_tz="Asia/Kolkata")
    assert parsed_ist == datetime(2026, 10, 13, 14, 30, 0, tzinfo=UTC)
    assert warn_ist is not None
    assert "assumed timezone 'Asia/Kolkata'" in warn_ist


def test_parse_datetime_rejections() -> None:
    """Assert parse_datetime rejects invalid strings, naive datetimes without assume_tz."""
    with pytest.raises(NormalizationError) as exc_naive:
        parse_datetime("2026-10-13T20:00:00", assume_tz=None)
    assert "is naive without timezone information" in str(exc_naive.value)

    with pytest.raises(NormalizationError):
        parse_datetime(12345678)  # type: ignore[arg-type]

    with pytest.raises(NormalizationError):
        parse_datetime("   ")

    with pytest.raises(NormalizationError):
        parse_datetime("invalid-date-format")
