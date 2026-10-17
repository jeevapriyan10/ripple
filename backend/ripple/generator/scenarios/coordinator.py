"""Scenario Coordinator scheduling fraud and laundering scenarios in engine virtual ticks.

Adheres strictly to Rules R1-R6:
- Deterministic pseudo-randomness using named substreams.
- Stateless execution keyed to virtual tick timestamps.
- Complete eager planning with truth sidecar tags.
"""

from datetime import datetime
from typing import Any

from ripple.generator.engine import SimulationContext, register_source
from ripple.generator.plan import TxnPlan
from ripple.generator.scenarios.base import (
    ScenarioContext,
    estimate_scenario_rates,
    get_scenario_catalog,
)


class ScenarioCoordinator:
    """Coordinates eager generation of fraud and laundering typologies."""

    source_key: str = "fraud_scenarios"

    def __init__(self) -> None:
        self._cached_rates: dict[str, float] | None = None
        self._cached_cfg_hash: str | None = None

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        """Plan fraud scenario executions starting within the current virtual tick."""
        cfg = ctx.config
        if not cfg.fraud.enabled:
            return []

        # Derive daily start rates for usable scenarios
        if self._cached_cfg_hash != cfg.config_hash():
            self._cached_rates = estimate_scenario_rates(cfg, ctx.calibration)
            self._cached_cfg_hash = cfg.config_hash()

        rates = self._cached_rates or {}
        catalog = get_scenario_catalog()
        ts_int = int(tick_start.timestamp())
        hour_offset = int((tick_start - cfg.start_time).total_seconds() // 3600)
        curr_day = (tick_start - cfg.start_time).total_seconds() / 86400.0

        plans: list[TxnPlan] = []
        enabled_networks = set(cfg.networks)

        # 1. Process Forced Starts
        forced_starts = cfg.fraud.forced_starts or []
        for f_idx, forced in enumerate(forced_starts):
            # pyrefly: ignore[missing-attribute]
            f_sc = forced.scenario if hasattr(forced, "scenario") else forced.get("scenario")
            # pyrefly: ignore[missing-attribute]
            f_hr = forced.at_hour if hasattr(forced, "at_hour") else forced.get("at_hour")
            if f_hr == hour_offset and f_sc in catalog:
                cls = catalog[f_sc]
                if all(net in enabled_networks for net in cls.required_networks):
                    inst_id = f"forced_{f_sc}_{ts_int}_{f_idx:02d}"
                    sc_rng = ctx.factory.stream(
                        "scenarios", "forced", f_sc, str(ts_int), str(f_idx)
                    )
                    regime = self._determine_regime(curr_day, cfg, sc_rng)
                    sc_ctx = ScenarioContext(
                        world=ctx.world,
                        substream=sc_rng,
                        calibration=ctx.calibration,
                        start_time=tick_start,
                        instance_id=inst_id,
                        regime=regime,
                        difficulty=cfg.difficulty,
                    )
                    sc_plan = cls().plan(sc_ctx)
                    plans.extend(sc_plan.txn_plans)

        # 2. Process Poisson Rate-Triggered Scenarios
        # Deterministic trigger stream for this virtual tick
        trig_rng = ctx.factory.stream("scenarios", "trigger", str(ts_int))

        for sc_name, daily_rate in rates.items():
            if sc_name not in catalog:
                continue
            cls = catalog[sc_name]
            if not all(net in enabled_networks for net in cls.required_networks):
                continue

            # Hourly start rate = daily_rate / 24.0
            hourly_rate = daily_rate / 24.0
            num_instances = trig_rng.poisson(hourly_rate)

            for inst_idx in range(num_instances):
                inst_id = f"sc_{sc_name}_{ts_int}_{inst_idx:02d}"
                sc_rng = ctx.factory.stream("scenarios", "run", sc_name, str(ts_int), str(inst_idx))
                regime = self._determine_regime(curr_day, cfg, sc_rng)
                sc_ctx = ScenarioContext(
                    world=ctx.world,
                    substream=sc_rng,
                    calibration=ctx.calibration,
                    start_time=tick_start,
                    instance_id=inst_id,
                    regime=regime,
                    difficulty=cfg.difficulty,
                )
                sc_plan = cls().plan(sc_ctx)
                plans.extend(sc_plan.txn_plans)

        return plans

    def _determine_regime(self, curr_day: float, cfg: Any, rng: Any) -> str:
        """Determine whether the scenario executes under baseline or a drifted regime."""
        if not cfg.drift.enabled:
            return "baseline"

        shift_day = float(cfg.drift.shift_day or 0.0)
        shape = cfg.drift.shape
        target_regime = cfg.drift.regime

        if shape == "abrupt":
            return target_regime if curr_day >= shift_day else "baseline"
        elif shape == "gradual":
            gradual_days = float(cfg.drift.gradual_days or 5.0)
            if curr_day < shift_day:
                return "baseline"
            progress = min(1.0, max(0.0, (curr_day - shift_day) / gradual_days))
            return target_regime if rng.bernoulli(progress) else "baseline"

        return "baseline"


# Auto-register coordinator with engine
register_source(ScenarioCoordinator())
