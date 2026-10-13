"""Static declarative profile model for payment network adapters.

Every payment network possesses unique structural invariants, settlement cycles,
counterparty addressing schemas, and operating constraints. Downstream layers
(Score, Drift, synthetic Generator, and Chain graph analytics) consume this static
metadata to calibrate baseline expectations, topology decomposition, and fraud risk weights.
"""

import re
from enum import StrEnum
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

_NETWORK_ID_REGEX = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_ISO_3166_ALPHA2_REGEX = re.compile(r"^[A-Z]{2}$")


class NetworkCategory(StrEnum):
    """Categorization archetype for payment rails.

    Informs downstream detection layers (Score) about the underlying clearance mechanism
    and structural risk profile.
    """

    INSTANT_PAYMENT = "INSTANT_PAYMENT"
    CARD = "CARD"
    CRYPTO = "CRYPTO"
    WIRE = "WIRE"
    WALLET = "WALLET"
    OTHER = "OTHER"


class LedgerVisibility(StrEnum):
    """Transparency level of the network's underlying settlement ledger.

    Informs Chain analytics whether external public ledger transactions can be
    correlated or clustered with internal graph data.
    """

    PRIVATE = "PRIVATE"
    SEMI_PUBLIC = "SEMI_PUBLIC"
    PUBLIC = "PUBLIC"


class OperatingCalendar(StrEnum):
    """Temporal operating schedule of the payment network.

    Informs Drift and Score layers when determining whether an off-hours or weekend
    transaction represents an anomaly or expected activity.
    """

    ALWAYS_ON = "ALWAYS_ON"
    BUSINESS_DAYS = "BUSINESS_DAYS"
    CUSTOM = "CUSTOM"


class NetworkProfile(BaseModel):
    """Frozen Pydantic v2 specification describing static facts about a payment rail.

    Every field captures a distinct structural invariant required by downstream
    detection, drift monitoring, graph generation, or synthetic data synthesis engines.
    """

    model_config = ConfigDict(frozen=True)

    network_id: str = Field(
        ...,
        description=(
            "Unique lowercase slug (e.g. 'upi', 'visa', 'polygon') identifying the network. "
            "Downstream layers (Score, Drift) use this to partition statistical profiles, "
            "and Chain uses it to route edges."
        ),
    )
    display_name: str = Field(
        ...,
        description=(
            "Human-readable display name used in alert dashboards, investigative reports, "
            "and auditor interfaces."
        ),
    )
    description: str = Field(
        ...,
        description=(
            "High-level architectural summary describing the payment network rail, "
            "clearing mechanism, and intended fraud typology coverage."
        ),
    )
    category: NetworkCategory = Field(
        ...,
        description=(
            "Architectural archetype (INSTANT_PAYMENT, CARD, etc.). Downstream Score layers "
            "use this to select archetype-specific risk baseline scoring weights."
        ),
    )
    settlement_model: SettlementModel = Field(
        ...,
        description=(
            "Underlying settlement mechanism (INSTANT, AUTH_CAPTURE, etc.). Chain uses this "
            "to establish expected graph edge topology and latency bounds."
        ),
    )
    supported_identifier_types: list[IdentifierType] = Field(
        ...,
        description=(
            "Permitted counterparty addressing tokens (VPA, IBAN, CRYPTO_ADDRESS, etc.). "
            "Synthetic data generators use this to synthesize valid test identifiers, "
            "and Score uses it to validate addressing integrity."
        ),
    )
    supported_asset_classes: list[AssetClass] = Field(
        ...,
        description=(
            "Asset classes (FIAT, CRYPTO) transacted on this rail. Drift detectors monitor "
            "for unexpected asset shifts, while Money models enforce precision."
        ),
    )
    default_scale: int = Field(
        ...,
        ge=0,
        description=(
            "Default decimal scale for monetary amounts (e.g. 2 for fiat cents, 18 for EVM wei). "
            "Money models and generators use this to enforce lossless decimal representations."
        ),
    )
    supported_currencies: list[str] = Field(
        default_factory=list,
        description=(
            "ISO-4217 currency codes or crypto token tickers supported on this rail. "
            "An empty list indicates permissive acceptance of any currency. Drift monitors "
            "use this to flag unapproved currency corridors."
        ),
    )
    typical_settlement_seconds_min: float = Field(
        ...,
        ge=0.0,
        description=(
            "Lower bound of the normal settlement latency window in seconds. Drift models "
            "use this to establish the lower threshold for expected clearing latency."
        ),
    )
    typical_settlement_seconds_max: float = Field(
        ...,
        ge=0.0,
        description=(
            "Upper bound of normal settlement latency in seconds. Score uses this to detect "
            "delayed settlements, abnormal clearing delays, or stuck payment transfers."
        ),
    )
    is_reversible: bool = Field(
        ...,
        description=(
            "Whether value movements on this rail can be revoked or charged back. "
            "Score heavily elevates fraud risk for irrevocable rails with zero recall ability."
        ),
    )
    typical_reversal_window_seconds: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Maximum duration in seconds during which a transaction may be reversed. "
            "Chain and Score use this to compute active liability windows and exposure decay."
        ),
    )
    intermediaries_visible: bool = Field(
        ...,
        description=(
            "Whether multi-hop intermediary nodes (acquirers, schemes, correspondents) "
            "are observable in event feeds. Chain uses this to decide whether to decompose "
            "transactions into multi-hop ROUTED_THROUGH edges."
        ),
    )
    ledger_visibility: LedgerVisibility = Field(
        ...,
        description=(
            "Transparency of the ledger (PRIVATE, SEMI_PUBLIC, PUBLIC). Chain uses this "
            "to determine whether public on-chain clustering algorithms apply."
        ),
    )
    is_pseudonymous: bool = Field(
        ...,
        description=(
            "Whether counterparties transact via pseudonyms without protocol-level KYC. "
            "Score uses this as a prior baseline multiplier in entity risk assessment."
        ),
    )
    operating_calendar: OperatingCalendar = Field(
        ...,
        description=(
            "Operational schedule of the clearing system. Drift and Score evaluate this "
            "to recognize off-hours or weekend velocity spikes as either normal or anomalous."
        ),
    )
    operating_timezone: str | None = Field(
        default=None,
        description=(
            "Primary operational IANA timezone for clearing. Used with operating_calendar "
            "to calculate local business hours and cut-off windows."
        ),
    )
    typical_fee_bps_range: tuple[float, float] | None = Field(
        default=None,
        description=(
            "Normal network or interchange fee range in basis points [min, max]. "
            "Score and Drift monitor for fee slippage or predatory routing deductions."
        ),
    )
    regions: list[str] = Field(
        default_factory=list,
        description=(
            "ISO-3166 alpha-2 country codes where the network operates. An empty list "
            "designates a global rail. Drift monitors use this for cross-border anomaly detection."
        ),
    )
    schema_version: int = Field(
        default=1,
        ge=1,
        description=(
            "Version number of this network profile schema, supporting backward-compatible "
            "evolution across platform versions."
        ),
    )

    @field_validator("network_id")
    @classmethod
    def _validate_network_id(cls, v: str) -> str:
        """Validate network_id is a lowercase slug matching ^[a-z][a-z0-9_]{1,31}$."""
        if not _NETWORK_ID_REGEX.match(v):
            raise ValueError(
                f"network_id '{v}' must be a lowercase slug matching "
                r"pattern ^[a-z][a-z0-9_]{1,31}$"
            )
        return v

    @field_validator("operating_timezone")
    @classmethod
    def _validate_operating_timezone(cls, v: str | None) -> str | None:
        """Validate operating_timezone is a valid IANA timezone name."""
        if v is not None:
            try:
                ZoneInfo(v)
            except Exception as exc:
                raise ValueError(
                    f"operating_timezone '{v}' is not a valid IANA timezone name"
                ) from exc
        return v

    @field_validator("regions")
    @classmethod
    def _validate_regions(cls, v: list[str]) -> list[str]:
        """Validate each region is an uppercase 2-letter ISO-3166 alpha-2 code."""
        for code in v:
            if not _ISO_3166_ALPHA2_REGEX.match(code):
                raise ValueError(
                    f"Region code '{code}' must be a 2-letter uppercase ISO-3166 alpha-2 code"
                )
        return v

    @model_validator(mode="after")
    def _validate_cross_field_invariants(self) -> Self:
        """Validate cross-field consistency across latency, reversibility, and hops."""
        # 1. Normal settlement latency band sanity: min <= max
        if self.typical_settlement_seconds_min > self.typical_settlement_seconds_max:
            raise ValueError(
                f"typical_settlement_seconds_min ({self.typical_settlement_seconds_min}) "
                f"must be <= typical_settlement_seconds_max ({self.typical_settlement_seconds_max})"
            )

        # 2. Reversibility invariant: if not reversible, typical_reversal_window_seconds is None
        if not self.is_reversible and self.typical_reversal_window_seconds is not None:
            raise ValueError(
                "typical_reversal_window_seconds must be None when is_reversible is False"
            )

        # 3. Intermediaries visible: if CORRESPONDENT_CHAIN or AUTH_CAPTURE, intermediaries_visible
        # may be True or False (not forced to True). No restriction is placed on other models.

        # 4. Fee basis points sanity check if provided
        if self.typical_fee_bps_range is not None:
            min_fee, max_fee = self.typical_fee_bps_range
            if min_fee > max_fee:
                raise ValueError(
                    f"typical_fee_bps_range min ({min_fee}) must be <= max ({max_fee})"
                )

        return self
