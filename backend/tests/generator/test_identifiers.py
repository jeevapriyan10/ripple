"""Tests validating synthetic identifiers against real domain and adapter validators."""

from ripple.adapters.crypto.adapter import (
    _BTC_BECH32_PATTERN,
    _EVM_ADDRESS_PATTERN,
    _SOLANA_BASE58_PATTERN,
    _TRON_BASE58_PATTERN,
)
from ripple.domain.identity import (
    IdentifierType,
    PartyIdentifier,
    _is_valid_iban,
)
from ripple.generator.identifiers import (
    bank_account_number,
    bic,
    card_token,
    crypto_address,
    email,
    iban,
    ifsc,
    instrument_id,
    is_luhn_valid,
    phone,
    vpa,
)
from ripple.generator.rng import Rng


def test_iban_mod97_across_all_countries() -> None:
    """Assert generated IBANs pass real ISO 7064 MOD-97 checksum and structure checks."""
    countries = ["GB", "DE", "NL", "FR", "ES", "IT", "CH", "AE"]
    rng = Rng(seed=101)

    for country in countries:
        for i in range(10):
            acc = f"{i * 12345:08d}"
            gen_iban = iban(country=country, bank_code="ZRPA", account_num=acc, rng=rng)

            # 1. Structural check via domain helper
            assert _is_valid_iban(gen_iban), f"IBAN {gen_iban} failed MOD-97 check for {country}"

            # 2. PartyIdentifier model construction
            p_ident = PartyIdentifier(
                type=IdentifierType.IBAN,
                value=gen_iban,
                network="SWIFT",
            )
            assert p_ident.value == gen_iban


def test_bic_validation() -> None:
    """Assert generated BICs pass ISO 9362 structural validation in PartyIdentifier."""
    for b_code in ["ZRPA", "ZRPB", "ZRPC", "ZRPD"]:
        for country in ["GB", "DE", "IN", "US"]:
            gen_bic = bic(bank_code=b_code, country=country, branch="XXX")
            ident = PartyIdentifier(
                type=IdentifierType.BIC,
                value=gen_bic,
                network="SWIFT",
            )
            assert ident.value == gen_bic


def test_phone_validation() -> None:
    """Assert synthetic phones under +999 pass E.164 PartyIdentifier validation."""
    rng = Rng(seed=202)
    for _ in range(50):
        ph = phone(rng)
        assert ph.startswith("+999")
        ident = PartyIdentifier(
            type=IdentifierType.PHONE,
            value=ph,
            network="UPI",
        )
        assert ident.value == ph


def test_email_validation_and_reserved_domains() -> None:
    """Assert synthetic emails pass PartyIdentifier validation and use RFC 2606 domains."""
    rng = Rng(seed=303)
    allowed_domains = {"example.com", "example.org", "example.net"}
    for _ in range(50):
        em = email(rng)
        domain = em.split("@")[1]
        assert domain in allowed_domains
        ident = PartyIdentifier(
            type=IdentifierType.EMAIL,
            value=em,
            network="WALLET",
        )
        assert ident.value == em


def test_crypto_addresses_against_adapter_regexes() -> None:
    """Assert crypto addresses match adapter regex patterns."""
    rng = Rng(seed=404)

    for _ in range(20):
        evm_addr = crypto_address("evm", rng)
        assert _EVM_ADDRESS_PATTERN.match(evm_addr)

        btc_addr = crypto_address("bitcoin", rng)
        assert _BTC_BECH32_PATTERN.match(btc_addr)

        sol_addr = crypto_address("solana", rng)
        assert _SOLANA_BASE58_PATTERN.match(sol_addr)

        tron_addr = crypto_address("tron", rng)
        assert _TRON_BASE58_PATTERN.match(tron_addr)


def test_vpa_and_ifsc_structures() -> None:
    """Assert synthetic VPA and IFSC match expected shapes."""
    rng = Rng(seed=505)
    for _ in range(20):
        gen_vpa = vpa(rng, handle="rplbank1")
        assert gen_vpa.endswith("@rplbank1")
        ident = PartyIdentifier(type=IdentifierType.VPA, value=gen_vpa, network="UPI")
        assert ident.value == gen_vpa

        gen_ifsc = ifsc(bank_code="RPLX", branch_index=rng.randint(1, 99999))
        assert gen_ifsc.startswith("RPLX0")
        assert len(gen_ifsc) == 11


def test_safety_no_luhn_valid_pan_strings() -> None:
    """Rule R2 Non-Negotiable: assert no 13-19 digit Luhn-valid string is generated anywhere."""
    rng = Rng(seed=606)

    # Check bank account numbers
    for _ in range(100):
        acc = bank_account_number(rng)
        # 10-12 digits: not in 13-19 PAN range
        assert 10 <= len(acc) <= 12

    # Check card tokens
    for _ in range(100):
        tok = card_token(rng)
        assert tok.startswith("tok_")
        assert len(tok) == 36  # tok_ + 32 hex
        # Non-digits present
        assert not tok.isdigit()

    # Check instrument IDs
    for _ in range(100):
        ins = instrument_id(rng)
        assert ins.startswith("ins_")
        assert not ins.isdigit()

    # Verify our is_luhn_valid helper on known test vectors
    assert is_luhn_valid("49927398716") is True
    assert is_luhn_valid("49927398717") is False
