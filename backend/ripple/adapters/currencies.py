"""ISO-4217 Currency table and scale lookup utilities.

Provides an authoritative mapping of ISO-4217 currency alpha-3 codes, 3-digit numeric
codes, and standard minor-unit decimal scales. Used by payment network adapters (Card,
SWIFT, etc.) for decimal scaling, validation, and normalization.
"""

from typing import Any

from ripple.adapters.errors import NormalizationError

# Mapping of alpha-3 code -> (3-digit numeric code | None, minor-unit decimal scale)
ISO_4217_CURRENCIES: dict[str, tuple[str | None, int]] = {
    "USD": ("840", 2),
    "EUR": ("978", 2),
    "GBP": ("826", 2),
    "JPY": ("392", 0),
    "CHF": ("756", 2),
    "CAD": ("124", 2),
    "AUD": ("036", 2),
    "NZD": ("554", 2),
    "CNY": ("156", 2),
    "HKD": ("344", 2),
    "SGD": ("702", 2),
    "INR": ("356", 2),
    "AED": ("784", 2),
    "SAR": ("682", 2),
    "SEK": ("752", 2),
    "NOK": ("578", 2),
    "DKK": ("208", 2),
    "PLN": ("985", 2),
    "CZK": ("203", 2),
    "HUF": ("348", 2),
    "TRY": ("949", 2),
    "ZAR": ("710", 2),
    "BRL": ("986", 2),
    "MXN": ("484", 2),
    "THB": ("764", 2),
    "IDR": ("360", 2),
    "MYR": ("458", 2),
    "PHP": ("608", 2),
    "KRW": ("410", 0),
    "VND": ("704", 0),
    "CLP": ("152", 0),
    "ISK": ("352", 0),
    "KWD": ("414", 3),
    "BHD": ("048", 3),
    "OMR": ("512", 3),
    "JOD": ("400", 3),
    "TND": ("788", 3),
}

# Inverted index for numeric code lookups (supporting both padded "036" and unpadded "36")
_NUMERIC_TO_ALPHA3: dict[str, str] = {}
for _alpha, (_num, _) in ISO_4217_CURRENCIES.items():
    if _num is not None:
        _NUMERIC_TO_ALPHA3[_num] = _alpha
        # Also support unpadded form if it starts with '0'
        if _num.startswith("0"):
            _NUMERIC_TO_ALPHA3[_num.lstrip("0")] = _alpha


def lookup_currency_by_alpha3(code: str) -> tuple[str, int]:
    """Look up an ISO-4217 currency by alpha-3 code.

    Returns:
        tuple[str, int]: Canonical upper-cased alpha-3 code and decimal scale.

    Raises:
        NormalizationError: If the currency code is unknown or invalid.
    """
    clean_code = str(code).strip().upper()
    if clean_code in ISO_4217_CURRENCIES:
        _, scale = ISO_4217_CURRENCIES[clean_code]
        return clean_code, scale

    raise NormalizationError(
        f"Unknown or unsupported currency code '{code}'.",
        field="currency",
        reason=f"Unknown currency '{code}'",
    )


def lookup_currency_by_numeric(numeric_code: str | int) -> tuple[str, int]:
    """Look up an ISO-4217 currency by 3-digit numeric code.

    Accepts string (e.g. "840", "036", "36") or integer (e.g. 840, 36).

    Returns:
        tuple[str, int]: Canonical upper-cased alpha-3 code and decimal scale.

    Raises:
        NormalizationError: If the numeric code is unknown or invalid.
    """
    clean_str = str(numeric_code).strip()
    if clean_str in _NUMERIC_TO_ALPHA3:
        alpha3 = _NUMERIC_TO_ALPHA3[clean_str]
        _, scale = ISO_4217_CURRENCIES[alpha3]
        return alpha3, scale

    # Also check zero-padded 3-digit form if numeric
    if clean_str.isdigit():
        padded = clean_str.zfill(3)
        if padded in _NUMERIC_TO_ALPHA3:
            alpha3 = _NUMERIC_TO_ALPHA3[padded]
            _, scale = ISO_4217_CURRENCIES[alpha3]
            return alpha3, scale

    raise NormalizationError(
        f"Unknown or unsupported numeric currency code '{numeric_code}'.",
        field="currency",
        reason=f"Unknown numeric currency '{numeric_code}'",
    )


def get_currency_scale_by_alpha3(code: str) -> int:
    """Return the minor-unit decimal scale for an ISO-4217 alpha-3 code."""
    _, scale = lookup_currency_by_alpha3(code)
    return scale


def get_currency_scale_by_numeric(numeric_code: str | int) -> int:
    """Return the minor-unit decimal scale for an ISO-4217 numeric code."""
    _, scale = lookup_currency_by_numeric(numeric_code)
    return scale


def resolve_currency(raw_curr: Any) -> tuple[str, int]:
    """Resolve an arbitrary currency representation (alpha-3 or numeric code).

    Returns:
        tuple[str, int]: Canonical upper-cased alpha-3 code and decimal scale.

    Raises:
        NormalizationError: If the currency cannot be resolved.
    """
    if raw_curr is None:
        raise NormalizationError(
            "Currency code cannot be null or empty.",
            field="currency",
            reason="Missing or empty currency",
        )

    clean_str = str(raw_curr).strip()
    if not clean_str:
        raise NormalizationError(
            "Currency code cannot be empty.",
            field="currency",
            reason="Empty currency code",
        )

    # Check numeric lookup
    if clean_str.isdigit() or (isinstance(raw_curr, int) and not isinstance(raw_curr, bool)):
        return lookup_currency_by_numeric(clean_str)

    # Check alpha-3 lookup
    clean_upper = clean_str.upper()
    if len(clean_upper) == 3 and clean_upper.isalpha():
        return lookup_currency_by_alpha3(clean_upper)

    raise NormalizationError(
        f"Unknown or unsupported currency code '{raw_curr}'.",
        field="currency",
        reason=f"Unknown currency '{raw_curr}'",
    )
