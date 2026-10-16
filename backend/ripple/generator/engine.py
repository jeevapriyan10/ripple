"""Discrete-event simulation engine, priority queue ordering, and streaming generation.

Rule R1 & R5 Invariants:
- Engine simulates in 1-hour virtual ticks.
- Stage events are scheduled with received_at = event_time + latency + jitter (never < event_time).
- Min-heap ordering key: (received_at, source_key, per_source_sequence) guaranteeing
  deterministic, global-counter-free tie-breaking.
- Memory consumption is strictly O(actors + pending lifecycle events), never O(total events).
- Events are yielded lazily as virtual time advances.
"""

import heapq
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any, Protocol

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.generator.calibration import CalibrationProfile, load_calibration_profile
from ripple.generator.config import GeneratorConfig
from ripple.generator.plan import StagePlan, TxnPlan, get_renderer
from ripple.generator.rng import RngFactory
from ripple.generator.timeutil import to_utc
from ripple.generator.truth import EventTruth, GeneratedEvent
from ripple.generator.world import World, build_world


class SimulationContext:
    """Contextual state provided to planners during virtual tick processing."""

    def __init__(
        self,
        config: GeneratorConfig,
        world: World,
        calibration: CalibrationProfile,
        factory: RngFactory,
    ) -> None:
        self.config = config
        self.world = world
        self.calibration = calibration
        self.factory = factory


class SourcePlanner(Protocol):
    """Protocol for traffic and scenario planners that emit eager transaction plans."""

    source_key: str

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        """Produce transaction plans whose first stage executes within this virtual tick."""
        ...


# Runtime registry of active background and scenario traffic planners
_SOURCE_REGISTRY: list[SourcePlanner] = []


def register_source(source: SourcePlanner) -> None:
    """Register a traffic or scenario planner."""
    _SOURCE_REGISTRY.append(source)


def clear_sources() -> None:
    """Clear registered planners (used primarily in test suites)."""
    _SOURCE_REGISTRY.clear()


def get_sources() -> list[SourcePlanner]:
    """Return all currently registered source planners."""
    if not _SOURCE_REGISTRY:
        try:
            from ripple.generator import renderers, traffic  # noqa: F401
        except ImportError:
            pass
    return list(_SOURCE_REGISTRY)


def _compute_event_id(source_key: str, plan_id: str, stage_name: str, stage_index: int) -> str:
    """Derive deterministic event_id from plan and stage coordinates per engine spec."""
    import hashlib

    composite = f"{source_key}|{plan_id}|{stage_name}|{stage_index}"
    digest = hashlib.sha256(composite.encode("utf-8")).hexdigest()
    return f"ev_{digest[:20]}"


def generate_stream(
    config: GeneratorConfig,
    calibration_profile: CalibrationProfile | None = None,
    sources: list[SourcePlanner] | None = None,
) -> Iterator[GeneratedEvent]:
    """Generate a lazy, non-decreasing stream of simulated events and ground-truth sidecars."""
    # 1. Load calibration
    calib = calibration_profile or load_calibration_profile(config.calibration)
    factory = RngFactory(config.seed)

    # 2. Build world model
    world = build_world(config, calib)
    ctx = SimulationContext(config, world, calib, factory)

    # 3. Active planners
    active_sources = sources if sources is not None else get_sources()

    # Priority queue min-heap of pending stage events:
    # (received_at, source_key, per_source_seq, plan, stage_idx, stage_plan, event_id, is_dup)
    heap: list[tuple[datetime, str, int, TxnPlan, int, StagePlan, str, bool]] = []
    source_sequences: dict[str, int] = {}

    seq_counter = 0
    total_hours = config.days * 24
    start_dt = to_utc(config.start_time)

    # Retrieve base latencies
    latency_dict: dict[str, float] = calib.get("feed.ingest_latency_seconds", {})

    for tick_idx in range(total_hours):
        tick_start = start_dt + timedelta(hours=tick_idx)
        tick_end = tick_start + timedelta(hours=1)

        # Plan tick across all sources
        for src in active_sources:
            s_key = src.source_key
            src_rng = factory.stream("engine", "jitter", s_key, str(tick_idx))
            plans = src.plan_tick(tick_start, ctx)

            for plan in plans:
                base_lat = float(latency_dict.get(plan.network.lower(), 1.0))
                for s_idx, stage in enumerate(plan.stages):
                    ev_time = to_utc(stage.event_time)

                    # Compute jitter and latency
                    jitter_secs = src_rng.uniform(0.0, max(0.0, config.feed.disorder_max_seconds))
                    rec_at = ev_time + timedelta(seconds=base_lat + jitter_secs)
                    if rec_at < ev_time:
                        rec_at = ev_time

                    s_seq = source_sequences.get(s_key, 0)
                    source_sequences[s_key] = s_seq + 1

                    ev_id = _compute_event_id(s_key, plan.plan_id, stage.name, s_idx)
                    heapq.heappush(
                        heap,
                        (rec_at, s_key, s_seq, plan, s_idx, stage, ev_id, False),
                    )

                    # Optional duplicate feed quirk injection
                    if config.feed.duplicate_rate > 0.0 and src_rng.bernoulli(
                        config.feed.duplicate_rate
                    ):
                        dup_rec_at = rec_at + timedelta(seconds=src_rng.uniform(1.0, 10.0))
                        s_seq_dup = source_sequences[s_key]
                        source_sequences[s_key] = s_seq_dup + 1
                        heapq.heappush(
                            heap,
                            (
                                dup_rec_at,
                                s_key,
                                s_seq_dup,
                                plan,
                                s_idx,
                                stage,
                                ev_id,
                                True,
                            ),
                        )

        # Emit events whose received_at < tick_end
        while heap and heap[0][0] < tick_end:
            (
                rec_at,
                s_key,
                _,
                plan,
                s_idx,
                stage,
                ev_id,
                is_dup,
            ) = heapq.heappop(heap)

            renderer = get_renderer(plan.network)
            payload = renderer(plan, s_idx)

            raw_event = RawEvent(
                network_id=plan.network.lower(),
                payload=payload,
                payload_format=PayloadFormat.JSON,
                received_at=rec_at,
                event_id=ev_id,
                source="synthetic_generator",
            )

            # Build EventTruth
            t_seed = plan.truth_seed
            sender_inst = world.get_actor(plan.sender_actor_id).institution_id
            recv_inst = world.get_actor(plan.receiver_actor_id).institution_id

            truth = EventTruth(
                event_id=ev_id,
                logical_txn_id=f"{plan.network.lower()}:{plan.plan_id}",
                stage=stage.name,
                network_id=plan.network.lower(),
                event_time=stage.event_time,
                scenario_id=t_seed.get("scenario_id"),
                pattern=t_seed.get("pattern"),
                regime=t_seed.get("regime", "baseline"),
                is_fraud=bool(t_seed.get("is_fraud", False)),
                hard_negative=bool(t_seed.get("hard_negative", False)),
                lookalike_of=t_seed.get("lookalike_of"),
                sender_actor_id=plan.sender_actor_id,
                receiver_actor_id=plan.receiver_actor_id,
                sender_role=t_seed.get("sender_role"),
                receiver_role=t_seed.get("receiver_role"),
                sender_institution_id=sender_inst,
                receiver_institution_id=recv_inst,
                label_available_at=t_seed.get("label_available_at"),
                expected_error=bool(
                    stage.outcome_data.get("expected_error", False)
                    or t_seed.get("expected_error", False)
                    or "malformed_mutation" in stage.outcome_data
                ),
                duplicate_of=ev_id if is_dup else None,
            )

            yield GeneratedEvent(seq=seq_counter, event=raw_event, truth=truth)
            seq_counter += 1

    # Finalization: drain heap if config.drain is True
    if config.drain:
        while heap:
            (
                rec_at,
                s_key,
                _,
                plan,
                s_idx,
                stage,
                ev_id,
                is_dup,
            ) = heapq.heappop(heap)

            renderer = get_renderer(plan.network)
            payload = renderer(plan, s_idx)

            raw_event = RawEvent(
                network_id=plan.network.lower(),
                payload=payload,
                payload_format=PayloadFormat.JSON,
                received_at=rec_at,
                event_id=ev_id,
                source="synthetic_generator",
            )

            t_seed = plan.truth_seed
            sender_inst = world.get_actor(plan.sender_actor_id).institution_id
            recv_inst = world.get_actor(plan.receiver_actor_id).institution_id

            truth = EventTruth(
                event_id=ev_id,
                logical_txn_id=f"{plan.network.lower()}:{plan.plan_id}",
                stage=stage.name,
                network_id=plan.network.lower(),
                event_time=stage.event_time,
                scenario_id=t_seed.get("scenario_id"),
                pattern=t_seed.get("pattern"),
                regime=t_seed.get("regime", "baseline"),
                is_fraud=bool(t_seed.get("is_fraud", False)),
                hard_negative=bool(t_seed.get("hard_negative", False)),
                lookalike_of=t_seed.get("lookalike_of"),
                sender_actor_id=plan.sender_actor_id,
                receiver_actor_id=plan.receiver_actor_id,
                sender_role=t_seed.get("sender_role"),
                receiver_role=t_seed.get("receiver_role"),
                sender_institution_id=sender_inst,
                receiver_institution_id=recv_inst,
                label_available_at=t_seed.get("label_available_at"),
                expected_error=bool(
                    stage.outcome_data.get("expected_error", False)
                    or t_seed.get("expected_error", False)
                    or "malformed_mutation" in stage.outcome_data
                ),
                duplicate_of=ev_id if is_dup else None,
            )

            yield GeneratedEvent(seq=seq_counter, event=raw_event, truth=truth)
            seq_counter += 1


def generate_transaction_stream(
    accounts: int,
    days: int,
    seed: int,
    **overrides: Any,
) -> Iterator[GeneratedEvent]:
    """Convenience wrapper for lightweight transaction stream generation."""
    config = GeneratorConfig(actors=accounts, days=days, seed=seed, **overrides)
    return generate_stream(config)
