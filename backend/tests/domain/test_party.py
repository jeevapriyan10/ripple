"""Unit tests for the Party aggregate model."""

from datetime import UTC, datetime

import pytest

from ripple.domain.errors import RippleDomainError
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.party import KycLevel, MuleRole, Party, PartyType, RiskFlags


def test_party_multi_identifier_ownership() -> None:
    """Test that a single real-world party can hold multiple identifiers across networks."""
    party = Party(
        party_type=PartyType.INDIVIDUAL,
        home_network="UPI",
        country_code="IN",
    )

    upi_id = PartyIdentifier(type=IdentifierType.VPA, value="alice@oksbi", network="UPI")
    phone_id = PartyIdentifier(type=IdentifierType.PHONE, value="+919876543210", network="UPI")
    crypto_id = PartyIdentifier(
        type=IdentifierType.CRYPTO_ADDRESS,
        value="0xd8da6bf26964af9d7eed9e03e53415d37aa96045",
        network="POLYGON",
        is_pseudonymous=True,
    )

    assert party.add_identifier(upi_id) is True
    assert party.add_identifier(phone_id) is True
    assert party.add_identifier(crypto_id) is True

    # Duplicate addition should return False and not inflate list
    assert party.add_identifier(upi_id) is False
    assert len(party.identifiers) == 3

    # Check ownership
    assert party.owns_identifier(upi_id) is True
    assert party.owns_identifier(phone_id) is True
    assert party.owns_identifier("alice@oksbi") is True
    assert party.owns_identifier("0xd8da6bf26964af9d7eed9e03e53415d37aa96045") is True

    # Non-owned identifier
    other_id = PartyIdentifier(type=IdentifierType.VPA, value="bob@okhdfc", network="UPI")
    assert party.owns_identifier(other_id) is False
    assert party.owns_identifier("unknown@upi") is False


def test_party_merge_identifiers() -> None:
    """Test merging identifiers from another Party or list into an existing Party."""
    party1 = Party(home_network="UPI")
    party2 = Party(home_network="SEPA")

    id1 = PartyIdentifier(type=IdentifierType.VPA, value="charlie@oksbi", network="UPI")
    id2 = PartyIdentifier(type=IdentifierType.EMAIL, value="charlie@example.com", network="PAYPAL")
    id3 = PartyIdentifier(type=IdentifierType.IBAN, value="GB82WEST12345698765432", network="SEPA")

    party1.add_identifier(id1)
    party2.add_identifier(id1)  # shared identifier (e.g. resolved to same human)
    party2.add_identifier(id2)
    party2.add_identifier(id3)

    # Merge party2 into party1
    newly_added = party1.merge_identifiers(party2)
    assert newly_added == 2  # id2 and id3 added; id1 was duplicate
    assert len(party1.identifiers) == 3
    assert party1.owns_identifier(id2) is True
    assert party1.owns_identifier(id3) is True


def test_party_risk_flags_and_mule_labeling() -> None:
    """Test party risk flags, KYC tiers, and mule typology labeling."""
    flags = RiskFlags(
        is_newly_created=True,
        kyc_level=KycLevel.BASIC,
        is_sanctioned=False,
        is_high_risk_jurisdiction=True,
    )
    party = Party(
        home_network="ETHEREUM",
        party_type=PartyType.EXCHANGE,
        risk_flags=flags,
        is_mule=True,
        mule_role=MuleRole.LAYERING,
        attributes={"chain": "ethereum", "cluster_id": "syndicate-42"},
    )

    assert party.risk_flags.is_newly_created is True
    assert party.risk_flags.kyc_level == KycLevel.BASIC
    assert party.risk_flags.is_high_risk_jurisdiction is True
    assert party.is_mule is True
    assert party.mule_role == MuleRole.LAYERING
    assert party.attributes["cluster_id"] == "syndicate-42"


def test_party_country_code_validation() -> None:
    """Ensure country_code enforces 2-letter uppercase ISO-3166 alpha-2 format."""
    party = Party(home_network="SWIFT", country_code="us")
    assert party.country_code == "US"

    with pytest.raises(RippleDomainError, match="must be a 2-letter uppercase ISO-3166"):
        Party(home_network="SWIFT", country_code="USA")

    with pytest.raises(RippleDomainError, match="must be a 2-letter uppercase ISO-3166"):
        Party(home_network="SWIFT", country_code="12")


def test_party_rejects_naive_created_at() -> None:
    """Ensure party creation fails if a naive (non-timezone-aware) datetime is supplied."""
    naive_dt = datetime.now()  # no tzinfo
    with pytest.raises(RippleDomainError, match="must be timezone-aware"):
        Party(home_network="UPI", created_at=naive_dt)

    aware_dt = datetime.now(UTC)
    party = Party(home_network="UPI", created_at=aware_dt)
    assert party.created_at == aware_dt
