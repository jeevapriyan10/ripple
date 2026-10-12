"""Unit tests for PartyIdentifier value objects and format validators."""

import pytest

from ripple.domain.errors import InvalidIdentifierError
from ripple.domain.identity import IdentifierType, PartyIdentifier


def test_vpa_validation() -> None:
    """Test UPI Virtual Payment Address format validation pass and fail cases."""
    # Valid VPAs
    p1 = PartyIdentifier(type=IdentifierType.VPA, value="alice@oksbi", network="UPI")
    assert p1.value == "alice@oksbi"

    p2 = PartyIdentifier(type=IdentifierType.VPA, value="merchant.ops_1@hdfcbank", network="UPI")
    assert p2.value == "merchant.ops_1@hdfcbank"

    # Invalid VPAs
    invalid_vpas = ["plainstring", "@bank", "user@", "user@@bank", ""]
    for inv in invalid_vpas:
        with pytest.raises(InvalidIdentifierError):
            PartyIdentifier(type=IdentifierType.VPA, value=inv, network="UPI")


def test_phone_validation() -> None:
    """Test E.164 phone number structural validation pass and fail cases."""
    # Valid phones
    p1 = PartyIdentifier(type=IdentifierType.PHONE, value="+14155552671", network="TELECOM")
    assert p1.value == "+14155552671"

    p2 = PartyIdentifier(type=IdentifierType.PHONE, value="+91 98765 43210", network="UPI")
    assert p2.value == "+91 98765 43210"

    # Invalid phones
    invalid_phones = ["123", "abc", "+0123456", ""]
    for inv in invalid_phones:
        with pytest.raises(InvalidIdentifierError):
            PartyIdentifier(type=IdentifierType.PHONE, value=inv, network="TELECOM")


def test_iban_validation() -> None:
    """Test IBAN structural and MOD-97 checksum validation."""
    # Valid IBAN (GB82WEST12345698765432 is a well-known test IBAN)
    valid_iban = PartyIdentifier(
        type=IdentifierType.IBAN,
        value="GB82WEST12345698765432",
        network="SEPA",
    )
    assert valid_iban.value == "GB82WEST12345698765432"

    # Checksum corruption (GB81 instead of GB82)
    with pytest.raises(InvalidIdentifierError, match="Fails ISO 13616"):
        PartyIdentifier(
            type=IdentifierType.IBAN,
            value="GB81WEST12345698765432",
            network="SEPA",
        )

    # Malformed length
    with pytest.raises(InvalidIdentifierError):
        PartyIdentifier(type=IdentifierType.IBAN, value="GB1234", network="SEPA")


def test_bic_validation() -> None:
    """Test Bank Identifier Code (ISO 9362) 8- and 11-character validation."""
    # Valid 8-char BIC
    bic8 = PartyIdentifier(type=IdentifierType.BIC, value="DEUTDEDD", network="SWIFT")
    assert bic8.value == "DEUTDEDD"

    # Valid 11-char BIC
    bic11 = PartyIdentifier(type=IdentifierType.BIC, value="DEUTDEDD500", network="SWIFT")
    assert bic11.value == "DEUTDEDD500"

    # Invalid BICs
    invalid_bics = ["DEUT", "DEUTDED", "DEUTDEDD5000", "DEUT12DD"]
    for inv in invalid_bics:
        with pytest.raises(InvalidIdentifierError):
            PartyIdentifier(type=IdentifierType.BIC, value=inv, network="SWIFT")


def test_crypto_address_validation() -> None:
    """Test cryptocurrency public address validation."""
    # Valid Ethereum address
    eth = PartyIdentifier(
        type=IdentifierType.CRYPTO_ADDRESS,
        value="0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
        network="ETHEREUM",
        is_pseudonymous=True,
    )
    assert eth.is_pseudonymous is True

    # Valid Bitcoin Bech32 address
    btc = PartyIdentifier(
        type=IdentifierType.CRYPTO_ADDRESS,
        value="bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq",
        network="BITCOIN",
        is_pseudonymous=True,
    )
    assert btc.value.startswith("bc1")

    # Invalid: whitespace or too short
    with pytest.raises(InvalidIdentifierError, match="cannot contain whitespace"):
        PartyIdentifier(
            type=IdentifierType.CRYPTO_ADDRESS,
            value="0xd8da6bf2 6964af9d7eed9e03e53415d37aa96045",
            network="ETHEREUM",
        )

    with pytest.raises(InvalidIdentifierError, match="length"):
        PartyIdentifier(
            type=IdentifierType.CRYPTO_ADDRESS,
            value="0x123",
            network="ETHEREUM",
        )


def test_email_validation() -> None:
    """Test email address formatting rules."""
    valid_email = PartyIdentifier(
        type=IdentifierType.EMAIL,
        value="alice.smith+alerts@example.com",
        network="PAYPAL",
    )
    assert valid_email.value == "alice.smith+alerts@example.com"

    invalid_emails = ["notanemail", "@example.com", "user@", "user@example"]
    for inv in invalid_emails:
        with pytest.raises(InvalidIdentifierError):
            PartyIdentifier(type=IdentifierType.EMAIL, value=inv, network="PAYPAL")


def test_identifier_normalization_and_deduplication() -> None:
    """Test canonical normalization for deduplication across dirty input variations."""
    # Email normalization: lowercase and trim
    email_ident = PartyIdentifier(
        type=IdentifierType.EMAIL,
        value="  ALICE@Example.COM  ",
        network="PAYPAL",
    )
    assert email_ident.normalize() == "alice@example.com"

    # VPA normalization: lowercase and trim
    vpa_ident = PartyIdentifier(
        type=IdentifierType.VPA,
        value="  Alice@OkSBI  ",
        network="UPI",
    )
    assert vpa_ident.normalize() == "alice@oksbi"

    # IBAN normalization: uppercase and strip spaces
    iban_ident = PartyIdentifier(
        type=IdentifierType.IBAN,
        value="gb82 west 1234 5698 7654 32",
        network="SEPA",
    )
    assert iban_ident.normalize() == "GB82WEST12345698765432"

    # BIC normalization: uppercase and trim
    bic_ident = PartyIdentifier(
        type=IdentifierType.BIC,
        value="  deutdedd500  ",
        network="SWIFT",
    )
    assert bic_ident.normalize() == "DEUTDEDD500"

    # Phone normalization: digits with +
    phone_ident = PartyIdentifier(
        type=IdentifierType.PHONE,
        value="+1 (415) 555-2671",
        network="TELECOM",
    )
    assert phone_ident.normalize() == "+14155552671"

    # Crypto address normalization: lowercased if hex
    crypto_ident = PartyIdentifier(
        type=IdentifierType.CRYPTO_ADDRESS,
        value="0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",
        network="ETHEREUM",
    )
    assert crypto_ident.normalize() == "0xd8da6bf26964af9d7eed9e03e53415d37aa96045"
