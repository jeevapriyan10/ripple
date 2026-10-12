"""Unit tests for the Money value object and Currency models."""

from decimal import Decimal

import pytest

from ripple.domain.errors import CurrencyMismatchError, PrecisionError
from ripple.domain.money import KNOWN_CURRENCIES, AssetClass, Currency, Money


def test_money_precision_18_decimal_crypto() -> None:
    """Ensure exact precision is preserved with an 18-decimal cryptocurrency amount."""
    raw_amount = Decimal("0.123456789012345678")
    crypto_money = Money(amount=raw_amount, currency="ETH", scale=18)

    assert crypto_money.amount == raw_amount
    assert crypto_money.scale == 18
    assert crypto_money.currency == "ETH"
    assert str(crypto_money) == "0.123456789012345678 ETH"


def test_money_float_input_rejected() -> None:
    """Ensure floats are explicitly rejected on amount and normalized_value."""
    # Amount as float
    with pytest.raises(PrecisionError, match="Floats are prohibited"):
        Money(amount=12.34, currency="USD")

    # Normalized value as float
    with pytest.raises(PrecisionError, match="Floats are prohibited"):
        Money(amount=Decimal("12.34"), currency="USD", normalized_value=12.34)

    # Multiplication by float
    m = Money(amount=Decimal("10.00"), currency="USD", scale=2)
    with pytest.raises(PrecisionError, match="Multiplication by float is prohibited"):
        _ = m * 2.5


def test_money_currency_mismatch_arithmetic() -> None:
    """Ensure operations across differing currencies raise CurrencyMismatchError."""
    usd = Money(amount=Decimal("100.00"), currency="USD", scale=2)
    eur = Money(amount=Decimal("100.00"), currency="EUR", scale=2)

    with pytest.raises(CurrencyMismatchError, match="Cannot add different currencies"):
        _ = usd + eur

    with pytest.raises(CurrencyMismatchError, match="Cannot subtract different currencies"):
        _ = usd - eur

    with pytest.raises(CurrencyMismatchError, match="Cannot compare different currencies"):
        _ = usd < eur

    with pytest.raises(CurrencyMismatchError, match="Cannot compare different currencies"):
        _ = usd >= eur


def test_money_minor_unit_round_tripping_fiat() -> None:
    """Test minor-unit conversion and round-tripping for 2-decimal fiat (cents)."""
    original = Money(amount=Decimal("49.99"), currency="USD", scale=2)
    minor_units = original.to_minor_units()
    assert minor_units == 4999

    recovered = Money.from_minor_units(minor_units, currency="USD", scale=2)
    assert recovered == original
    assert recovered.amount == Decimal("49.99")


def test_money_minor_unit_round_tripping_crypto() -> None:
    """Test minor-unit conversion and round-tripping for 18-decimal crypto (Wei)."""
    raw_amount = Decimal("1.000000000000000001")
    original = Money(amount=raw_amount, currency="ETH", scale=18)
    minor_units = original.to_minor_units()
    assert minor_units == 1000000000000000001

    recovered = Money.from_minor_units(minor_units, currency="ETH", scale=18)
    assert recovered == original
    assert recovered.amount == raw_amount


def test_money_exceeds_configured_scale() -> None:
    """Test that amounts with higher precision than configured scale raise PrecisionError."""
    with pytest.raises(PrecisionError, match="exceeds the configured scale"):
        Money(amount=Decimal("10.555"), currency="USD", scale=2)


def test_money_arithmetic_operations() -> None:
    """Test valid addition, subtraction, scalar multiplication, and unary operators."""
    m1 = Money(amount=Decimal("10.50"), currency="USD", scale=2)
    m2 = Money(amount=Decimal("4.25"), currency="USD", scale=2)

    added = m1 + m2
    assert added.amount == Decimal("14.75")
    assert added.currency == "USD"

    subtracted = m1 - m2
    assert subtracted.amount == Decimal("6.25")

    multiplied_int = m1 * 2
    assert multiplied_int.amount == Decimal("21.00")

    rmultiplied_int = 3 * m2
    assert rmultiplied_int.amount == Decimal("12.75")

    multiplied_decimal = m1 * Decimal("0.5")
    assert multiplied_decimal.amount == Decimal("5.25")

    negated = -m1
    assert negated.amount == Decimal("-10.50")

    assert abs(negated) == m1


def test_money_comparisons() -> None:
    """Test equality and ordering operators within the same currency."""
    m1 = Money(amount=Decimal("10.00"), currency="USD", scale=2)
    m2 = Money(amount=Decimal("20.00"), currency="USD", scale=2)
    m3 = Money(amount=Decimal("10.00"), currency="USD", scale=2)

    assert m1 < m2
    assert m1 <= m2
    assert m1 <= m3
    assert m2 > m1
    assert m2 >= m1
    assert m1 == m3
    assert m1 != m2
    assert m1 != "not_money"


def test_currency_model_and_asset_classes() -> None:
    """Validate Currency value objects and KNOWN_CURRENCIES mapping."""
    usd = KNOWN_CURRENCIES["USD"]
    assert usd.asset_class == AssetClass.FIAT
    assert usd.default_scale == 2

    eth = KNOWN_CURRENCIES["ETH"]
    assert eth.asset_class == AssetClass.CRYPTO
    assert eth.default_scale == 18

    custom = Currency(code="SOL", asset_class=AssetClass.CRYPTO, default_scale=9)
    assert custom.code == "SOL"
    assert custom.default_scale == 9
