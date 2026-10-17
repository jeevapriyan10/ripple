"""Tests for engine priority queue, non-decreasing timestamps, laziness, and tie breaking."""

import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from ripple.generator.config import GeneratorConfig
from ripple.generator.engine import (
    SimulationContext,
    SourcePlanner,
    generate_stream,
)
from ripple.generator.plan import StagePlan, TxnPlan, register_renderer


class DummyTestPlanner(SourcePlanner):
    """Test source emitting predictable multi-stage transaction plans."""

    source_key: str = "test_dummy_source"

    def __init__(self, count_per_tick: int = 2) -> None:
        self.count_per_tick = count_per_tick
        self._counter = 0

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        plans: list[TxnPlan] = []
        actors = ctx.world.actors
        sender = actors[0].actor_id
        receiver = actors[1].actor_id

        for _ in range(self.count_per_tick):
            self._counter += 1
            plan_id = f"plan_{self._counter:04d}"
            stage1 = StagePlan(
                name="stage1",
                event_time=tick_start + timedelta(minutes=10),
                outcome_data={},
            )
            stage2 = StagePlan(
                name="stage2",
                event_time=tick_start + timedelta(minutes=25),
                outcome_data={},
            )
            plan = TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="upi",
                kind="test_transfer",
                sender_actor_id=sender,
                receiver_actor_id=receiver,
                amount=Decimal("100.00"),
                currency="INR",
                stages=[stage1, stage2],
                attrs={},
                truth_seed={"scenario_id": None, "is_fraud": False},
            )
            plans.append(plan)
        return plans


@pytest.fixture(autouse=True)
def setup_dummy_renderer() -> None:
    """Register dummy renderer for test purposes."""

    def dummy_renderer(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
        return {
            "txn_id": plan.plan_id,
            "stage": plan.stages[stage_idx].name,
            "amount": str(plan.amount),
        }

    register_renderer("upi", dummy_renderer)


def test_engine_ordering_non_decreasing() -> None:
    """Assert all yielded events have strictly non-decreasing received_at timestamps."""
    config = GeneratorConfig(seed=42, actors=100, days=2)
    planner = DummyTestPlanner(count_per_tick=3)

    events = list(generate_stream(config, sources=[planner]))
    assert len(events) > 0

    for i in range(len(events) - 1):
        t1 = events[i].event.received_at
        t2 = events[i + 1].event.received_at
        assert t1 <= t2, f"Event {i} timestamp {t1} > Event {i+1} timestamp {t2}"


def test_engine_laziness() -> None:
    """Assert generator is lazy and does not simulate all ticks when only head is consumed."""
    config = GeneratorConfig(seed=42, actors=100, days=10)  # 240 hours
    planner = DummyTestPlanner(count_per_tick=2)

    stream = generate_stream(config, sources=[planner])
    first_10 = list(itertools.islice(stream, 10))

    assert len(first_10) == 10
    # The planner counter should be far less than the full 240 hours * 2 = 480 plans
    assert planner._counter < 100


def test_engine_drain_vs_truncation() -> None:
    """Assert engine drains remaining queue events when drain=True."""
    config_drain = GeneratorConfig(seed=42, actors=50, days=1, drain=True)
    planner_drain = DummyTestPlanner(count_per_tick=1)
    events_drain = list(generate_stream(config_drain, sources=[planner_drain]))

    config_nodrain = GeneratorConfig(seed=42, actors=50, days=1, drain=False)
    planner_nodrain = DummyTestPlanner(count_per_tick=1)
    events_nodrain = list(generate_stream(config_nodrain, sources=[planner_nodrain]))

    assert len(events_drain) >= len(events_nodrain)
