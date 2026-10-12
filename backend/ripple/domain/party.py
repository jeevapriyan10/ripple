"""Party aggregate model representing entities (nodes) in the Ripple graph.

A Party models any real-world economic actor in the financial graph. Counterparties
interact across distinct payment rails using diverse identities (e.g. a merchant with
an acquiring terminal ID on Visa, an IBAN on SEPA, and an unhosted Ethereum address).
The Party model ties these identifiers to a stable internal identity node while
retaining compliance metadata, risk flags, and fraud research ground-truth labels.
"""

import re
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ripple.domain.errors import RippleDomainError
from ripple.domain.identity import PartyIdentifier


class PartyType(str, Enum):
    """Categorization of counterparty entity types across diverse payment networks.

    INDIVIDUAL: Retail consumers or peer-to-peer account holders (UPI P2P, Venmo, retail crypto).
    MERCHANT: Commercial sellers accepting payments via Point-of-Sale (POS), eCommerce, or QR.
    BUSINESS: Corporate and commercial entities conducting B2B or payroll disbursements.
    EXCHANGE: Centralized cryptocurrency platforms, OTC trading desks, or currency exchange bureaus.
              Crucial as off-ramps/on-ramps in mule layering networks.
    FINANCIAL_INSTITUTION: Banks, payment system operators, card issuers, or credit unions.
    UNKNOWN: Entities whose structural classification is unverified at ingestion time.
    """

    INDIVIDUAL = "INDIVIDUAL"
    MERCHANT = "MERCHANT"
    BUSINESS = "BUSINESS"
    EXCHANGE = "EXCHANGE"
    FINANCIAL_INSTITUTION = "FINANCIAL_INSTITUTION"
    UNKNOWN = "UNKNOWN"


class KycLevel(str, Enum):
    """Graded Know-Your-Customer verification tier for the party.

    NONE: Unverified unhosted wallets (crypto) or anonymous prepaid cards.
    BASIC: Light mobile verification (phone verified, tier-1 wallet limits).
    FULL: Comprehensive identity verification (ID, address, biometric, AML checked).
    """

    NONE = "NONE"
    BASIC = "BASIC"
    FULL = "FULL"


class MuleRole(str, Enum):
    """Ground-truth typology role in money mule and layering networks.

    VICTIM: Legitimate account holder defrauded via authorized push payment (APP) scam or theft.
    FIRST_HOP: Mule account receiving direct transfers from the victim.
    LAYERING: Intermediary mule node moving funds rapidly across multiple rails.
    CASH_OUT: Terminal node converting laundered funds to physical cash (ATM) or crypto off-ramp.
    UNKNOWN: Flagged as mule-related without confirmed topological stage.
    """

    VICTIM = "VICTIM"
    FIRST_HOP = "FIRST_HOP"
    LAYERING = "LAYERING"
    CASH_OUT = "CASH_OUT"
    UNKNOWN = "UNKNOWN"


class RiskFlags(BaseModel):
    """Intrinsic static and graded risk attributes known about a party independent of graph.

    These attributes provide baseline prior probabilities before graph topology
    algorithms (Mesh / Chain) analyze transaction flow patterns.
    """

    model_config = ConfigDict(frozen=True)

    is_newly_created: bool = Field(
        default=False,
        description="True if account was registered within incubation window (e.g. < 48 hours).",
    )
    kyc_level: KycLevel = Field(
        default=KycLevel.NONE,
        description="Depth of customer identity verification completed on this party.",
    )
    is_sanctioned: bool = Field(
        default=False,
        description="True if party appears on global watchlists (OFAC, UN, EU sanctions).",
    )
    is_high_risk_jurisdiction: bool = Field(
        default=False,
        description="True if party is based in FATF grey/blacklisted countries or offshore zones.",
    )


_ISO_3166_ALPHA2 = re.compile(r"^[A-Z]{2}$")


class Party(BaseModel):
    """Party aggregate entity representing a NODE in the Ripple fraud detection graph.

    Aggregates:
    - Multiple `PartyIdentifier` values across diverse payment networks.
    - KYC and pre-graph risk indicators.
    - Mule research ground-truth labels (`is_mule`, `mule_role`).
    - Flexible network-specific attributes.
    """

    model_config = ConfigDict(frozen=False)

    party_id: UUID = Field(
        default_factory=uuid4,
        description="Stable internal UUID uniquely identifying this entity across all networks.",
    )
    identifiers: list[PartyIdentifier] = Field(
        default_factory=list,
        description="All counterparty identifiers owned by this party across disparate rails.",
    )
    party_type: PartyType = Field(
        default=PartyType.UNKNOWN,
        description="Economic category of the party (INDIVIDUAL, MERCHANT, EXCHANGE, etc.).",
    )
    home_network: str = Field(
        ...,
        description="Primary originating payment network (e.g. 'UPI', 'ETHEREUM', 'SWIFT').",
    )
    country_code: str | None = Field(
        default=None,
        description="ISO-3166 alpha-2 jurisdiction code (e.g. 'US', 'GB', 'IN').",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when party was created, in timezone-aware UTC.",
    )
    risk_flags: RiskFlags = Field(
        default_factory=RiskFlags,
        description="Static compliance and risk attributes.",
    )
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Rail-specific properties that don't belong in universal schema.",
    )
    is_mule: bool | None = Field(
        default=None,
        description="Ground-truth label indicating if party operates as a money mule.",
    )
    mule_role: MuleRole | None = Field(
        default=None,
        description="Topological role in laundering chain (FIRST_HOP, LAYERING, CASH_OUT, etc.).",
    )

    @field_validator("country_code")
    @classmethod
    def _validate_country_code(cls, v: str | None) -> str | None:
        if v is None:
            return None
        cleaned = v.strip().upper()
        if not _ISO_3166_ALPHA2.match(cleaned):
            raise RippleDomainError(
                f"Country code '{v}' must be a 2-letter uppercase ISO-3166 alpha-2 code."
            )
        return cleaned

    @field_validator("created_at")
    @classmethod
    def _validate_created_at(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise RippleDomainError("created_at datetime must be timezone-aware (UTC).")
        return v.astimezone(UTC)

    def owns_identifier(self, identifier: PartyIdentifier | str) -> bool:
        """Check whether this party owns the specified identifier.

        Matches by normalized identity token or PartyIdentifier equality.
        """
        if isinstance(identifier, PartyIdentifier):
            norm_target = identifier.normalize()
            target_type = identifier.type
            target_net = identifier.network.upper()
            return any(
                i.normalize() == norm_target
                and i.type == target_type
                and i.network.upper() == target_net
                for i in self.identifiers
            )
        if isinstance(identifier, str):
            clean_str = identifier.strip().lower()
            return any(
                i.value == identifier
                or i.normalize().lower() == clean_str
                or i.to_canonical_uri().lower() == clean_str
                for i in self.identifiers
            )
        return False

    def has_identifier(self, identifier: PartyIdentifier) -> bool:
        """Alias for checking identifier ownership via PartyIdentifier value object."""
        return self.owns_identifier(identifier)

    def add_identifier(self, identifier: PartyIdentifier) -> bool:
        """Attach an additional counterparty identifier to this party aggregate.

        Deduplicates against existing identifiers using normalized form, network, and type.
        Returns True if added, False if already present.
        """
        if self.owns_identifier(identifier):
            return False
        self.identifiers.append(identifier)
        return True

    def merge_identifiers(self, source: "Party | list[PartyIdentifier]") -> int:
        """Merge identifiers from another party or list into this party.

        Returns the count of newly attached identifiers.
        """
        candidates: list[PartyIdentifier] = (
            source.identifiers if isinstance(source, Party) else source
        )
        added_count = 0
        for ident in candidates:
            if self.add_identifier(ident):
                added_count += 1
        return added_count
