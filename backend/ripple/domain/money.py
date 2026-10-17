"""Monetary value object and currency models for the Ripple platform.

Monetary integrity is foundational in financial fraud detection. Binary floating-point
types (IEEE 754) suffer from representation inaccuracies (e.g. 0.1 + 0.2 != 0.3) that
can introduce rounding anomalies, corrupt balances, or compromise audit trails.
This module enforces exact arithmetic using `decimal.Decimal` across all payment rails,
from 2-decimal fiat currencies (USD, EUR, INR) to 18-decimal cryptocurrencies (EVM Wei).
"""

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ripple.domain.errors import CurrencyMismatchError, PrecisionError


class AssetClass(str, Enum):
    """Classification of monetary assets across global payment networks.

    FIAT: Sovereign fiat currencies governed by central banks and standardized by
          ISO 4217 (e.g. USD, EUR, GBP, INR). Typically operate on 2 decimal places
          (minor units like cents/paise), with rare exceptions like JPY (0) or BHD (3).
    CRYPTO: Cryptographically secured blockchain tokens and native assets (e.g. BTC, ETH, SOL).
            Operate on fractional sub-units with high decimal scales (e.g. 8 for Bitcoin Satoshi,
            18 for Ethereum Wei) to support micro-transactions and gas fees.
    """

    FIAT = "FIAT"
    CRYPTO = "CRYPTO"


class Currency(BaseModel):
    """Domain concept representing a currency and its native precision specifications.

    Distinguishes traditional fiat rails (UPI, Card networks, SWIFT) from high-precision
    cryptocurrency networks (Bitcoin, Ethereum, Solana).
    """

    model_config = ConfigDict(frozen=True)

    code: str = Field(
        ...,
        description="Currency symbol or ISO-4217 code in uppercase (e.g. USD, EUR, BTC, ETH).",
    )
    asset_class: AssetClass = Field(
        ...,
        description="Asset class designating sovereign fiat vs decentralized cryptocurrency.",
    )
    default_scale: int = Field(
        default=2,
        ge=0,
        le=18,
        description="Default number of decimal places for minor unit representation.",
    )

    @field_validator("code")
    @classmethod
    def _validate_code(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("Currency code cannot be empty.")
        return cleaned


# Common pre-configured currency definitions for convenient lookup
KNOWN_CURRENCIES: dict[str, Currency] = {
    "USD": Currency(code="USD", asset_class=AssetClass.FIAT, default_scale=2),
    "EUR": Currency(code="EUR", asset_class=AssetClass.FIAT, default_scale=2),
    "GBP": Currency(code="GBP", asset_class=AssetClass.FIAT, default_scale=2),
    "INR": Currency(code="INR", asset_class=AssetClass.FIAT, default_scale=2),
    "JPY": Currency(code="JPY", asset_class=AssetClass.FIAT, default_scale=0),
    "BTC": Currency(code="BTC", asset_class=AssetClass.CRYPTO, default_scale=8),
    "ETH": Currency(code="ETH", asset_class=AssetClass.CRYPTO, default_scale=18),
    "USDT": Currency(code="USDT", asset_class=AssetClass.CRYPTO, default_scale=6),
    "USDC": Currency(code="USDC", asset_class=AssetClass.CRYPTO, default_scale=6),
    "SOL": Currency(code="SOL", asset_class=AssetClass.CRYPTO, default_scale=9),
}


class Money(BaseModel):
    """Immutable monetary value object representing an exact financial amount.

    Motivated by the union of multi-rail payment structures:
    - UPI & Wallets: Exact fiat amounts denominated in national currency (e.g. INR, USD).
    - Card networks: Dual-message authorization and batch settlement amounts.
    - SWIFT: Correspondent banking settlement amounts with associated intermediary fees.
    - Blockchains: High-precision native transfers and gas fees requiring up to 18 decimals.

    Crucially, floats are NEVER permitted. Arithmetic between differing currencies is
    blocked unless normalized by downstream FX conversion services.
    """

    model_config = ConfigDict(frozen=True)

    amount: Decimal = Field(
        ...,
        description="Exact numerical quantity represented as a Decimal. Floats are prohibited.",
    )
    currency: str = Field(
        ...,
        description="ISO-4217 fiat code or crypto ticker symbol in uppercase (e.g. 'USD', 'ETH').",
    )
    scale: int = Field(
        default=2,
        ge=0,
        le=18,
        description="Number of decimal places (minor units) supported, from 0 up to 18 for crypto.",
    )
    normalized_value: Decimal | None = Field(
        default=None,
        description=(
            "Optional reference-currency valuation (e.g. USD equivalent) populated by "
            "downstream FX normalization services. Pure domain models do not execute FX logic."
        ),
    )

    def __init__(self, **data: Any) -> None:
        # Pre-check for float input to raise PrecisionError explicitly before standard parsing
        for field in ("amount", "normalized_value"):
            if field in data and isinstance(data[field], float):
                raise PrecisionError(
                    f"Floats are prohibited in monetary field '{field}' due to precision loss. "
                    "Use Decimal, int, or string representations."
                )
        super().__init__(**data)

    @field_validator("amount", "normalized_value", mode="before")
    @classmethod
    def _reject_float_and_coerce_decimal(cls, v: Any) -> Any:
        if isinstance(v, float):
            raise PrecisionError(
                "Floats are prohibited in monetary amounts due to IEEE 754 precision loss. "
                "Use Decimal, int, or string representations."
            )
        if isinstance(v, int | str):
            try:
                return Decimal(str(v))
            except InvalidOperation as exc:
                raise PrecisionError(f"Cannot parse '{v}' into an exact Decimal amount.") from exc
        return v

    @field_validator("currency")
    @classmethod
    def _validate_currency(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if not cleaned:
            raise ValueError("Currency cannot be empty.")
        return cleaned

    @model_validator(mode="after")
    def _validate_scale_precision(self) -> Self:
        # Check that fractional digits in amount do not exceed specified scale
        if self.scale < 0 or self.scale > 18:
            raise PrecisionError(f"Scale must be between 0 and 18, got {self.scale}.")

        exp = self.amount.as_tuple().exponent
        # If exponent is negative, -exp is the number of decimal places
        if isinstance(exp, int) and exp < 0:
            num_decimals = -exp
            if num_decimals > self.scale:
                # If there are trailing zeros beyond scale that can be safely reduced:
                step = Decimal(10) ** -self.scale
                if self.amount != self.amount.quantize(step):
                    raise PrecisionError(
                        f"Amount {self.amount} has {num_decimals} decimal places, "
                        f"which exceeds the configured scale of {self.scale}."
                    )
        return self

    def to_minor_units(self) -> int:
        """Convert amount to integer minor units (e.g. cents, paise, or wei).

        For example:
        - $10.50 USD with scale 2 -> 1050 cents
        - 1.5 ETH with scale 18 -> 1500000000000000000 wei
        """
        multiplier = Decimal(10) ** self.scale
        return int(self.amount * multiplier)

    @classmethod
    def from_minor_units(
        cls,
        minor_units: int,
        currency: str,
        scale: int = 2,
        normalized_value: Decimal | None = None,
    ) -> "Money":
        """Construct a Money instance from integer minor units.

        Reverses `to_minor_units()`, providing exact round-trip preservation without float drift.
        """
        divisor = Decimal(10) ** scale
        amount = Decimal(minor_units) / divisor
        return cls(
            amount=amount,
            currency=currency,
            scale=scale,
            normalized_value=normalized_value,
        )

    def __add__(self, other: Any) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot add different currencies: {self.currency} and {other.currency}. "
                "Normalize via reference FX rate before arithmetic."
            )
        target_scale = max(self.scale, other.scale)
        return Money(
            amount=self.amount + other.amount,
            currency=self.currency,
            scale=target_scale,
        )

    def __sub__(self, other: Any) -> "Money":
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot subtract different currencies: {self.currency} and {other.currency}. "
                "Normalize via reference FX rate before arithmetic."
            )
        target_scale = max(self.scale, other.scale)
        return Money(
            amount=self.amount - other.amount,
            currency=self.currency,
            scale=target_scale,
        )

    def __mul__(self, scalar: Any) -> "Money":
        if isinstance(scalar, float):
            raise PrecisionError(
                "Multiplication by float is prohibited to prevent precision loss. "
                "Use Decimal or int."
            )
        if not isinstance(scalar, int | Decimal):
            return NotImplemented
        dec_scalar = Decimal(str(scalar))
        return Money(
            amount=self.amount * dec_scalar,
            currency=self.currency,
            scale=self.scale,
        )

    def __rmul__(self, scalar: Any) -> "Money":
        return self.__mul__(scalar)

    def __neg__(self) -> "Money":
        return Money(amount=-self.amount, currency=self.currency, scale=self.scale)

    def __abs__(self) -> "Money":
        return Money(amount=abs(self.amount), currency=self.currency, scale=self.scale)

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Money):
            return False
        return self.currency == other.currency and self.amount == other.amount

    def __lt__(self, other: Any) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot compare different currencies: {self.currency} and {other.currency}."
            )
        return self.amount < other.amount

    def __le__(self, other: Any) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot compare different currencies: {self.currency} and {other.currency}."
            )
        return self.amount <= other.amount

    def __gt__(self, other: Any) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot compare different currencies: {self.currency} and {other.currency}."
            )
        return self.amount > other.amount

    def __ge__(self, other: Any) -> bool:
        if not isinstance(other, Money):
            return NotImplemented
        if self.currency != other.currency:
            raise CurrencyMismatchError(
                f"Cannot compare different currencies: {self.currency} and {other.currency}."
            )
        return self.amount >= other.amount

    def __str__(self) -> str:
        """Formatted string representation with appropriate decimal places."""
        return f"{self.amount:.{self.scale}f} {self.currency}"

    def __repr__(self) -> str:
        return (
            f"Money(amount=Decimal('{self.amount}'), currency='{self.currency}', "
            f"scale={self.scale}, normalized_value={self.normalized_value})"
        )
