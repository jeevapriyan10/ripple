"""Shared normalization helpers for payment network adapters.

Pure, reusable utility functions for parsing raw payloads, extracting mandatory
fields, validating exact monetary amounts, and normalizing timestamps to timezone-aware UTC.
"""

import json
import re
from contextlib import ContextDecorator
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError

from ripple.adapters.contract import RawEvent
from ripple.adapters.errors import NormalizationError, UnsupportedPayloadError
from ripple.domain.errors import (
    CurrencyMismatchError,
    InvalidIdentifierError,
    InvalidTransactionError,
    PrecisionError,
    RippleDomainError,
)


def payload_as_dict(event: RawEvent | Any) -> dict[str, Any]:
    """Extract and parse the raw event payload into a dictionary without float materialization.

    If payload is str or bytes, parses JSON using parse_float=Decimal so IEEE 754 floats
    never materialize. If payload is already a dictionary, returns it directly.
    Raises UnsupportedPayloadError for invalid JSON or unsupported payload types.
    """
    raw = event.payload if isinstance(event, RawEvent) else event

    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsupportedPayloadError(
                f"Binary payload cannot be decoded as UTF-8: {exc}"
            ) from exc

    if isinstance(raw, str):
        try:
            parsed = json.loads(raw, parse_float=Decimal)
        except json.JSONDecodeError as exc:
            raise UnsupportedPayloadError(f"Malformed JSON payload: {exc}") from exc
        if not isinstance(parsed, dict):
            raise UnsupportedPayloadError(
                f"JSON payload must be an object (dict), got {type(parsed).__name__}."
            )
        return parsed

    if isinstance(raw, dict):
        return raw

    raise UnsupportedPayloadError(
        f"Unsupported payload type '{type(raw).__name__}'. Expected dict, str, or bytes."
    )


def require(
    payload: dict[str, Any],
    key: str,
    field_path: str | None = None,
) -> Any:
    """Return the value for key in payload, or raise NormalizationError if missing or empty.

    Treats None, whitespace-only strings, and empty sequences/mappings as empty.
    Zero and False are valid non-empty values.
    """
    display_field = field_path or key
    if not isinstance(payload, dict):
        raise NormalizationError(
            f"Expected object for field '{display_field}', got {type(payload).__name__}.",
            field=display_field,
            reason=f"Invalid container structure for '{display_field}'",
        )

    if key not in payload:
        raise NormalizationError(
            f"Field '{display_field}' is required but missing from payload.",
            field=display_field,
            reason=f"Missing required field '{display_field}'",
        )

    val = payload[key]
    if (
        val is None
        or (isinstance(val, str) and not val.strip())
        # pyrefly: ignore [implicit-any-type-argument]
        or (isinstance(val, list | dict | set | tuple) and len(val) == 0)
    ):
        raise NormalizationError(
            f"Field '{display_field}' is required but empty in payload.",
            field=display_field,
            reason=f"Empty required field '{display_field}'",
        )

    return val


def parse_amount(
    value: Any,
    field: str = "amount",
    scale: int = 2,
) -> Decimal:
    """Parse and validate a monetary amount, strictly rejecting binary floats.

    Accepts str, int, Decimal. Rejects float with a NormalizationError instructing
    the caller to supply amounts as strings to preserve decimal precision.
    Rejects non-numeric, NaN, infinite, or negative values.
    Validates that the amount does not exceed the allowed decimal scale.
    """
    if isinstance(value, float):
        raise NormalizationError(
            f"Field '{field}' must not be a float due to precision loss. "
            "Please supply amounts as strings or integers.",
            field=field,
            reason="Float amounts prohibited: supply as string",
        )

    if not isinstance(value, str | int | Decimal):
        raise NormalizationError(
            f"Field '{field}' must be a string, integer, or Decimal, got {type(value).__name__}.",
            field=field,
            reason=f"Non-numeric amount type '{type(value).__name__}'",
        )

    try:
        if isinstance(value, str):
            clean_str = value.strip()
            if not clean_str:
                raise NormalizationError(
                    f"Field '{field}' cannot be an empty string.",
                    field=field,
                    reason=f"Empty amount string in '{field}'",
                )
            dec = Decimal(clean_str)
        elif isinstance(value, int):
            dec = Decimal(value)
        else:
            dec = value
    except InvalidOperation as exc:
        raise NormalizationError(
            f"Field '{field}' contains non-numeric value '{value}'.",
            field=field,
            reason="Non-numeric amount string",
        ) from exc

    if dec.is_nan() or dec.is_infinite():
        raise NormalizationError(
            f"Field '{field}' cannot be NaN or infinite.",
            field=field,
            reason="Invalid amount: NaN or infinite",
        )

    if dec < Decimal(0):
        raise NormalizationError(
            f"Field '{field}' cannot be negative, got {dec}.",
            field=field,
            reason="Negative amount rejected",
        )

    # Validate against configured scale
    if scale >= 0:
        step = Decimal(10) ** -scale
        try:
            quantized = dec.quantize(step)
            if dec != quantized:
                raise NormalizationError(
                    f"Field '{field}' has more decimal places than allowed scale {scale}.",
                    field=field,
                    reason=f"Amount exceeds scale {scale}",
                )
            return quantized
        except (InvalidOperation, NormalizationError):
            raise
        except Exception as exc:
            raise NormalizationError(
                f"Field '{field}' cannot be formatted to scale {scale}: {exc}",
                field=field,
                reason=f"Scale validation failed for '{field}'",
            ) from exc

    return dec


def parse_datetime(
    value: Any,
    field: str = "datetime",
    assume_tz: str | None = None,
) -> tuple[datetime, str | None]:
    """Parse an ISO-8601 string, integer epoch, or datetime into UTC datetime.

    Accepts:
    - datetime instances
    - ISO-8601 formatted strings
    - Integer epoch seconds (e.g. 1728864000)

    Rejects:
    - Booleans (bool is a subclass of int in Python)
    - Floating point numbers
    - Negative epoch values
    - Absurd epoch values (e.g. beyond year 9999)

    If the value is naive and assume_tz is provided, interprets it in that IANA
    timezone, converts to UTC, and returns a warning string.
    If the value is naive and assume_tz is None, raises NormalizationError.
    Returns (utc_datetime, warning_or_none).
    """
    if isinstance(value, bool):
        raise NormalizationError(
            f"Timestamp field '{field}' cannot be a boolean.",
            field=field,
            reason=f"Boolean timestamp in '{field}'",
        )

    if isinstance(value, float):
        raise NormalizationError(
            f"Timestamp field '{field}' must not be a float. "
            "Please supply integer epoch seconds or ISO-8601 string.",
            field=field,
            reason=f"Float timestamp in '{field}'",
        )

    if isinstance(value, int):
        if value < 0:
            raise NormalizationError(
                f"Timestamp field '{field}' cannot be negative, got {value}.",
                field=field,
                reason=f"Negative timestamp in '{field}'",
            )
        if value > 253402300799:  # 9999-12-31 23:59:59 UTC
            raise NormalizationError(
                f"Timestamp field '{field}' contains absurd epoch value {value}.",
                field=field,
                reason=f"Absurd timestamp in '{field}'",
            )
        try:
            return datetime.fromtimestamp(value, tz=UTC), None
        except (OverflowError, OSError, ValueError) as exc:
            raise NormalizationError(
                f"Timestamp field '{field}' contains invalid epoch value: {exc}",
                field=field,
                reason=f"Invalid epoch timestamp in '{field}'",
            ) from exc

    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        clean_str = value.strip()
        if not clean_str:
            raise NormalizationError(
                f"Timestamp field '{field}' cannot be empty.",
                field=field,
                reason=f"Empty timestamp in '{field}'",
            )
        try:
            dt = datetime.fromisoformat(clean_str)
        except ValueError as exc:
            raise NormalizationError(
                f"Field '{field}' has invalid ISO-8601 timestamp '{value}': {exc}",
                field=field,
                reason=f"Invalid ISO-8601 timestamp in '{field}'",
            ) from exc
    else:
        raise NormalizationError(
            f"Timestamp field '{field}' must be an ISO-8601 string, integer epoch seconds, "
            f"or datetime, got {type(value).__name__}.",
            field=field,
            reason=f"Invalid timestamp type in '{field}'",
        )

    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        if assume_tz is None:
            raise NormalizationError(
                f"Timestamp '{value}' for field '{field}' is naive without timezone information.",
                field=field,
                reason=f"Naive timestamp without timezone in '{field}'",
            )
        try:
            tz = ZoneInfo(assume_tz)
        except ZoneInfoNotFoundError as exc:
            raise NormalizationError(
                f"Invalid assume_tz timezone '{assume_tz}' for field '{field}'.",
                field=field,
                reason=f"Unknown timezone '{assume_tz}'",
            ) from exc

        aware_dt = dt.replace(tzinfo=tz).astimezone(UTC)
        warning = (
            f"Timestamp for '{field}' was naive; assumed timezone '{assume_tz}' "
            "and converted to UTC."
        )
        return aware_dt, warning

    return dt.astimezone(UTC), None


class wrap_domain_errors(ContextDecorator):
    """Context manager / decorator catching domain validation errors as NormalizationError.

    Ensures that domain validation errors (InvalidTransactionError, InvalidIdentifierError,
    PrecisionError, CurrencyMismatchError, and Pydantic ValidationError) raised while
    constructing universal domain models are translated into typed NormalizationError(field, reason)
    WITHOUT echoing submitted sensitive values (e.g. IBANs, accounts, card numbers, addresses).
    """

    def __init__(self, default_field: str = "transaction") -> None:
        self.default_field = default_field

    def __enter__(self) -> "wrap_domain_errors":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> Literal[False]:
        if exc_val is None:
            return False

        if isinstance(exc_val, NormalizationError | UnsupportedPayloadError):
            return False

        if isinstance(
            exc_val,
            RippleDomainError
            | InvalidTransactionError
            | InvalidIdentifierError
            | PrecisionError
            | CurrencyMismatchError
            | ValidationError,
        ):
            field = self.default_field
            reason = "Domain invariant validation failed."

            if isinstance(exc_val, ValidationError):
                errors = exc_val.errors()
                if errors:
                    err = errors[0]
                    loc_parts = err.get("loc", ())
                    if loc_parts:
                        field = ".".join(str(p) for p in loc_parts)
                    reason = err.get("msg", "Validation failed")
                else:
                    reason = "Validation failed"
            elif isinstance(exc_val, InvalidIdentifierError):
                field = "identifier"
                raw_msg = str(exc_val)
                sanitized_msg = re.sub(r"'[^']*'", "<redacted>", raw_msg)
                sanitized_msg = re.sub(r'"[^"]*"', "<redacted>", sanitized_msg)
                if "IBAN" in raw_msg:
                    field = "iban"
                    reason = "Invalid IBAN: fails structure or checksum"
                elif "BIC" in raw_msg:
                    field = "bic"
                    reason = "Invalid BIC/SWIFT code structure"
                elif "Crypto" in raw_msg:
                    field = "address"
                    reason = "Invalid crypto address structure"
                else:
                    reason = sanitized_msg
            elif isinstance(exc_val, InvalidTransactionError):
                raw_msg = str(exc_val)
                sanitized_msg = re.sub(r"'[^']*'", "<redacted>", raw_msg)
                sanitized_msg = re.sub(r'"[^"]*"', "<redacted>", sanitized_msg)
                if "settled_at" in raw_msg:
                    field = "settled_at"
                elif "amount" in raw_msg:
                    field = "amount"
                elif "sender" in raw_msg.lower() or "receiver" in raw_msg.lower():
                    field = "counterparty"
                reason = sanitized_msg
            elif isinstance(exc_val, PrecisionError):
                field = "amount"
                reason = "Amount precision exceeds allowed scale or contains float"
            elif isinstance(exc_val, CurrencyMismatchError):
                field = "currency"
                reason = "Currency mismatch between amounts"

            raise NormalizationError(
                f"Domain validation failed for field '{field}': {reason}",
                field=field,
                reason=reason,
            ) from exc_val

        return False
