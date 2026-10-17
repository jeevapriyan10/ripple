"""Scenario framework, base class, context, and registry adhering to Rules R1-R6.

All random decisions are made eagerly when a ScenarioPlan is built (Rule R5).
Renderers remain pure functions.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ripple.domain.party import MuleRole
from ripple.generator.calibration import CalibrationProfile
from ripple.generator.config import GeneratorConfig
from ripple.generator.plan import TxnPlan
from ripple.generator.rng import Rng
from ripple.generator.world import World


@dataclass(frozen=True)
class ScenarioContext:
    """Execution context provided to a scenario planner."""

    world: World
    substream: Rng
    calibration: CalibrationProfile
    start_time: datetime
    instance_id: str
    regime: str = "baseline"
    difficulty: str = "medium"


@dataclass(frozen=True)
class ScenarioPlan:
    """Complete eagerly planned execution chain of a fraud scenario."""

    txn_plans: list[TxnPlan]
    roles: dict[str, MuleRole | None]
    summary: dict[str, Any]


class Scenario(ABC):
    """Abstract base class for all synthetic fraud and laundering typologies."""

    scenario_type: str = "unknown"
    pattern: str = "unknown"
    required_networks: list[str] = []

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """Return catalog metadata describing this scenario typology."""
        return {
            "scenario_type": cls.scenario_type,
            "pattern": cls.pattern,
            "required_networks": cls.required_networks,
            "doc": cls.__doc__ or "",
        }

    @abstractmethod
    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        """Eagerly plan the complete transaction chain and role assignments."""
        ...


_SCENARIO_REGISTRY: dict[str, type[Scenario]] = {}


def register_scenario(cls: type[Scenario]) -> type[Scenario]:
    """Decorator registering a Scenario implementation in the global catalog."""
    _SCENARIO_REGISTRY[cls.scenario_type] = cls
    return cls


def get_scenario(name: str) -> type[Scenario]:
    """Retrieve scenario implementation class by scenario_type identifier."""
    if name not in _SCENARIO_REGISTRY:
        raise KeyError(
            f"Unknown scenario type '{name}'. Registered: {list(_SCENARIO_REGISTRY.keys())}"
        )
    return _SCENARIO_REGISTRY[name]


def list_scenarios() -> list[dict[str, Any]]:
    """Return list of descriptive metadata dictionaries for all registered scenarios."""
    return [cls.describe() for cls in _SCENARIO_REGISTRY.values()]


def get_scenario_catalog() -> dict[str, type[Scenario]]:
    """Return dictionary mapping scenario_type to implementation classes."""
    return dict(_SCENARIO_REGISTRY)


def estimate_scenario_rates(
    config: GeneratorConfig,
    calibration: CalibrationProfile,
) -> dict[str, float]:
    """Derive per-scenario daily start rates approximating the target fraud intensity.

    Intensity targets:
    - low: ~0.002 (0.2%)
    - medium: ~0.010 (1.0%)
    - high: ~0.040 (4.0%)

    Performs a deterministic dry-run of each usable scenario using a fixed probe seed
    to estimate average event count per scenario instance.
    """
    if not config.fraud.enabled:
        return {}

    intensity_targets = {
        "low": 0.002,
        "medium": 0.010,
        "high": 0.040,
    }
    target_fraud_fraction = intensity_targets.get(config.fraud.intensity, 0.010)

    # Determine usable scenarios (all required networks must be enabled)
    enabled_set = set(config.networks)
    usable_scenarios = [
        cls
        for cls in _SCENARIO_REGISTRY.values()
        if all(net in enabled_set for net in cls.required_networks)
    ]
    if not usable_scenarios:
        return {}

    # Estimate average events per scenario instance using deterministic probe
    events_per_scenario: dict[str, int] = {}
    from ripple.generator.rng import RngFactory
    from ripple.generator.world import build_world

    probe_world = build_world(config, calibration)
    probe_factory = RngFactory(master_seed=9999)

    for cls in usable_scenarios:
        probe_rng = probe_factory.stream("probe", cls.scenario_type)
        ctx = ScenarioContext(
            world=probe_world,
            substream=probe_rng,
            calibration=calibration,
            start_time=config.start_time,
            instance_id="probe_001",
            difficulty=config.difficulty,
        )
        try:
            plan = cls().plan(ctx)
            # Count total lifecycle stages across all transaction plans
            total_events = sum(len(tp.stages) for tp in plan.txn_plans)
            events_per_scenario[cls.scenario_type] = max(1, total_events)
        except Exception:
            events_per_scenario[cls.scenario_type] = 5

    # Estimate daily background volume
    # Background events per day is approximately actors * base_events_per_actor_per_day
    est_bg_events_per_day = config.actors * 12.0
    desired_fraud_events_per_day = est_bg_events_per_day * target_fraud_fraction

    # Equal share per usable scenario unless config.fraud.scenario_mix specifies weights
    mix = config.fraud.scenario_mix or {}
    total_weight = sum(mix.get(cls.scenario_type, 1.0) for cls in usable_scenarios)

    rates: dict[str, float] = {}
    for cls in usable_scenarios:
        weight = mix.get(cls.scenario_type, 1.0) / total_weight
        allocated_events = desired_fraud_events_per_day * weight
        avg_events = events_per_scenario.get(cls.scenario_type, 6)
        daily_rate = allocated_events / avg_events
        rates[cls.scenario_type] = max(0.01, daily_rate)

    return rates
