"""Generator configuration, presets, canonical hashing, and validation."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ALGORITHM_VERSION: str = "1"

# Allowed payment networks for generation ("universal" is explicitly forbidden)
ALLOWED_NETWORKS: frozenset[str] = frozenset({"upi", "card", "crypto", "swift", "wallet"})

# Known scenario typology identifiers
KNOWN_SCENARIO_IDS: frozenset[str] = frozenset(
    {
        "upi_mule_chain",
        "fan_in_smurfing",
        "wallet_provisional_funding_abuse",
        "card_testing",
        "chargeback_fraud_merchant",
        "crypto_peeling_layering",
        "nft_wash_trading",
        "account_takeover_drain",
        "swift_structuring_layering",
        "cross_network_laundering",
    }
)

# Network requirements for each scenario typology
SCENARIO_REQUIRED_NETWORKS: dict[str, set[str]] = {
    "upi_mule_chain": {"upi"},
    "fan_in_smurfing": {"upi"},  # also has wallet variant
    "wallet_provisional_funding_abuse": {"wallet"},
    "card_testing": {"card"},
    "chargeback_fraud_merchant": {"card"},
    "crypto_peeling_layering": {"crypto"},
    "nft_wash_trading": {"crypto"},
    "account_takeover_drain": {"upi"},  # or wallet
    "swift_structuring_layering": {"swift"},
    "cross_network_laundering": {"wallet", "swift", "crypto"},
}


class ForcedStart(BaseModel):
    """Pin a scenario start time to a specific simulation hour."""

    model_config = ConfigDict(frozen=True)

    scenario: str = Field(..., description="Target scenario identifier")
    at_hour: int = Field(..., ge=0, description="Virtual tick hour when scenario should ignite")


class FraudConfig(BaseModel):
    """Fraud and financial crime injection configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Enable fraud scenario generation")
    intensity: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="Target fraud volume intensity (~0.2% low, ~1% medium, ~4% high)",
    )
    scenario_mix: dict[str, float] | None = Field(
        default=None,
        description="Optional relative scenario weights. None = uniform across usable scenarios",
    )
    label_coverage: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Fraction of fraud events that eventually receive ground-truth labels",
    )
    label_delay_median_hours: float = Field(
        default=48.0,
        gt=0.0,
        description="Median delay between fraud event and ground-truth label availability",
    )
    forced_starts: list[ForcedStart] = Field(
        default_factory=list,
        description="Specific scenario starts pinned to exact simulation hours",
    )

    @field_validator("scenario_mix")
    @classmethod
    def _validate_scenario_mix(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is not None:
            for sc_id in v:
                if sc_id not in KNOWN_SCENARIO_IDS:
                    raise ValueError(f"Unknown scenario ID '{sc_id}' in scenario_mix")
        return v

    @field_validator("forced_starts")
    @classmethod
    def _validate_forced_starts(cls, v: list[ForcedStart]) -> list[ForcedStart]:
        for fs in v:
            if fs.scenario not in KNOWN_SCENARIO_IDS:
                raise ValueError(f"Unknown scenario ID '{fs.scenario}' in forced_starts")
        return v


class HardNegativesConfig(BaseModel):
    """Hard-negative legitimate traffic look-alikes."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="Inject legitimate fraud look-alikes")
    rate: float = Field(
        default=0.03,
        ge=0.0,
        le=0.5,
        description="Fraction of background volume injected as hard negatives",
    )


class DriftConfig(BaseModel):
    """Distributional drift and adversarial regime shift configuration."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=False, description="Enable regime shift mid-simulation")
    regime: str = Field(default="v2_mule_template", description="Target adversarial regime name")
    shift_day: int = Field(default=15, description="Simulation day when shift occurs")
    shape: Literal["abrupt", "gradual"] = Field(
        default="abrupt",
        description="Shape of the regime shift transition ('abrupt' or 'gradual')",
    )
    gradual_days: int = Field(
        default=5,
        ge=1,
        description="Transition duration in days when shape is 'gradual'",
    )


class FeedConfig(BaseModel):
    """Ingestion pipeline realism and message transport quirks."""

    model_config = ConfigDict(frozen=True)

    disorder_max_seconds: float = Field(
        default=30.0,
        ge=0.0,
        description="Maximum transport jitter in seconds causing received_at disorder",
    )
    duplicate_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=0.2,
        description="Rate of identical event_id duplicates emitted later in stream",
    )
    malformed_rate: float = Field(
        default=0.0,
        ge=0.0,
        le=0.2,
        description="Rate of malformed events injected to exercise adapter error handlers",
    )
    naive_timestamp_rate: float = Field(
        default=0.01,
        ge=0.0,
        le=1.0,
        description="Rate of naive timestamp emission in network local time",
    )
    de7_rate: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description="Rate of card DE7 MMDDhhmmss timestamp formatting",
    )


class GeneratorConfig(BaseModel):
    """Immutable configuration for the Ripple synthetic transaction generator."""

    model_config = ConfigDict(frozen=True)

    seed: int = Field(default=42, description="Master random seed for full determinism")
    start_time: datetime = Field(
        default=datetime(2026, 1, 5, 0, 0, 0, tzinfo=UTC),
        description="Simulation start instant in UTC (defaults to Monday 2026-01-05T00:00:00Z)",
    )
    days: int = Field(default=7, ge=1, le=180, description="Duration of simulation in days")
    actors: int = Field(
        default=2000,
        ge=50,
        le=200000,
        description="Total population size (individuals, merchants, businesses, exchanges)",
    )
    networks: list[str] = Field(
        default_factory=lambda: ["upi", "card", "crypto", "swift", "wallet"],
        description="Enabled payment network rails",
    )
    institutions: int = Field(
        default=4,
        ge=2,
        le=12,
        description="Number of simulated financial institutions",
    )
    difficulty: Literal["easy", "medium", "hard"] = Field(
        default="medium",
        description="Separability difficulty level",
    )
    calibration: str = Field(
        default="default",
        description="Name of the calibration profile to load",
    )
    fraud: FraudConfig = Field(default_factory=FraudConfig)
    hard_negatives: HardNegativesConfig = Field(default_factory=HardNegativesConfig)
    drift: DriftConfig = Field(default_factory=DriftConfig)
    feed: FeedConfig = Field(default_factory=FeedConfig)
    drain: bool = Field(
        default=True,
        description="Drain remaining events in the priority queue at simulation close",
    )

    # Resolved warnings during configuration validation (not user provided)
    resolved_warnings: list[str] = Field(default_factory=list)

    @field_validator("start_time")
    @classmethod
    def _validate_start_time(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("GeneratorConfig start_time must be timezone-aware (UTC)")
        return v.astimezone(UTC)

    @field_validator("networks")
    @classmethod
    def _validate_networks(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("At least one payment network must be enabled")
        clean_nets = [net.strip().lower() for net in v]
        for net in clean_nets:
            if net == "universal":
                raise ValueError(
                    "Network 'universal' is a reference/testing adapter and cannot be generated"
                )
            if net not in ALLOWED_NETWORKS:
                raise ValueError(
                    f"Unsupported network '{net}'. Allowed networks: {sorted(ALLOWED_NETWORKS)}"
                )
        return sorted(set(clean_nets))

    @model_validator(mode="after")
    def _validate_drift_and_scenarios(self) -> Self:
        # Check drift shift_day
        if self.drift.enabled:
            if not (0 < self.drift.shift_day < self.days):
                raise ValueError(
                    f"drift.shift_day ({self.drift.shift_day}) must be strictly between "
                    f"0 and total days ({self.days})"
                )

        # Filter and validate scenario usability against enabled networks
        enabled_net_set = set(self.networks)
        warnings: list[str] = list(self.resolved_warnings)

        if self.fraud.enabled and self.fraud.scenario_mix is not None:
            for sc_id in list(self.fraud.scenario_mix.keys()):
                reqs = SCENARIO_REQUIRED_NETWORKS.get(sc_id, set())
                if not reqs.issubset(enabled_net_set):
                    warnings.append(
                        f"Scenario '{sc_id}' dropped because required networks {reqs} "
                        f"are not all enabled in {self.networks}."
                    )

        object.__setattr__(self, "resolved_warnings", warnings)
        return self

    def canonical_json(self) -> str:
        """Produce deterministic, sort-keyed, compact JSON string of the configuration."""
        data = self.model_dump(mode="json")
        # Exclude resolved_warnings from config hash to preserve hash purity
        data.pop("resolved_warnings", None)
        return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

    def config_hash(self) -> str:
        """Produce SHA-256 hex digest of the canonical JSON configuration."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    @classmethod
    def from_preset(cls, name: str, **overrides: Any) -> "GeneratorConfig":
        """Construct GeneratorConfig from named presets: 'tiny', 'small', 'demo', 'large'."""
        presets: dict[str, dict[str, Any]] = {
            "tiny": {
                "actors": 200,
                "days": 2,
            },
            "small": {
                "actors": 2000,
                "days": 7,
            },
            "demo": {
                "actors": 5000,
                "days": 30,
            },
            "large": {
                "actors": 50000,
                "days": 30,
            },
        }

        clean_name = name.strip().lower()
        if clean_name not in presets:
            raise ValueError(f"Unknown preset '{name}'. Available presets: {list(presets.keys())}")

        config_args = dict(presets[clean_name])
        config_args.update(overrides)
        return cls(**config_args)
