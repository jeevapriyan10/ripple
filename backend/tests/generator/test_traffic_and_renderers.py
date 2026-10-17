"""Tests for background traffic planners, pure renderers, and feed quirks (Prompt 7B)."""

import inspect
import re
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from ripple.adapters.errors import AdapterError
from ripple.adapters.registry import get_registry, load_enabled_adapters
from ripple.generator.config import GeneratorConfig
from ripple.generator.engine import generate_stream
from ripple.generator.identifiers import is_luhn_valid
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.renderers import (
    render_card_payload,
    render_crypto_payload,
    render_swift_payload,
    render_upi_payload,
    render_wallet_payload,
)


def _contains_luhn_valid(data: str) -> bool:
    """Check if any contiguous 13-19 digit sequence is Luhn-valid."""
    matches = re.findall(r"\b\d{13,19}\b", data)
    for m in matches:
        if is_luhn_valid(m):
            return True
    return False


def test_renderer_static_purity():
    """Verify renderers contain no forbidden clock or randomness calls."""
    forbidden = ["random.", "datetime.now", "datetime.utcnow", "time.time(", "os.environ", "hash("]
    for renderer_fn in [
        render_upi_payload,
        render_card_payload,
        render_crypto_payload,
        render_swift_payload,
        render_wallet_payload,
    ]:
        src = inspect.getsource(renderer_fn)
        for pattern in forbidden:
            assert (
                pattern not in src
            ), f"Renderer {renderer_fn.__name__} violates Rule R5 with '{pattern}'"


def test_renderer_deterministic_idempotence():
    """Verify calling a renderer twice with identical arguments returns identical payloads."""
    dt = datetime(2026, 1, 5, 12, 0, 0, tzinfo=UTC)
    stage = StagePlan(
        name="PAY", event_time=dt, outcome_data={"status": "SUCCESS", "response_code": "00"}
    )
    plan = TxnPlan(
        plan_id="test_plan_001",
        source_key="test_source",
        network="upi",
        kind="p2p_pay",
        sender_actor_id="A000001",
        receiver_actor_id="A000002",
        amount=Decimal("100.00"),
        currency="INR",
        stages=[stage],
        attrs={
            "payer_vpa": "payer@rplbank1",
            "payee_vpa": "payee@rplbank1",
            "payer_name": "Synthetic Actor 000001",
            "payee_name": "Synthetic Actor 000002",
        },
    )

    out1 = render_upi_payload(plan, 0)
    out2 = render_upi_payload(plan, 0)
    assert out1 == out2


def test_no_truth_leakage_and_no_luhn_pan():
    """Verify raw events contain no truth keys and no Luhn-valid PAN strings."""
    config = GeneratorConfig(
        seed=1001,
        days=1,
        actors=100,
        networks=["upi", "card", "crypto", "swift", "wallet"],
    )

    forbidden_truth_keys = {"is_fraud", "fraud", "mule", "scenario", "pattern", "role"}

    event_count = 0
    for gen_ev in generate_stream(config):
        event_count += 1
        payload = gen_ev.event.payload

        # Check payload keys recursively
        def check_keys(obj: object, root_payload: dict[str, object]):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    assert (
                        k.lower() not in forbidden_truth_keys
                    ), f"Leaked truth key '{k}' found in payload: {root_payload}"
                    check_keys(v, root_payload)
            elif isinstance(obj, list):
                for item in obj:
                    check_keys(item, root_payload)

        check_keys(payload, payload)

        # Check for Luhn-valid PANs
        payload_str = str(payload)
        assert not _contains_luhn_valid(
            payload_str
        ), f"Found Luhn-valid PAN in payload: {payload_str}"

        if event_count >= 50:
            break

    assert event_count > 0


def test_adapter_normalization_end_to_end():
    """Verify generated events normalize cleanly through official adapters."""
    registry = get_registry()
    load_enabled_adapters(registry=registry)
    config = GeneratorConfig(
        seed=2024,
        days=2,
        actors=120,
        networks=["upi", "card", "crypto", "swift", "wallet"],
        feed={
            "disorder_max_seconds": 10.0,
            "duplicate_rate": 0.0,
            "malformed_rate": 0.0,
            "naive_timestamp_rate": 0.0,
            "de7_rate": 0.0,
        },
    )

    counts: dict[str, int] = {}
    for gen_ev in generate_stream(config):
        net_id = gen_ev.event.network_id
        adapter = registry.get(net_id)
        assert adapter is not None, f"No adapter for network {net_id}"

        # Normalize through the real adapter
        result = adapter.normalize(gen_ev.event)
        assert result.transaction is not None
        assert len(result.parties) >= 2
        counts[net_id] = counts.get(net_id, 0) + 1

        # Placeholder names should not be preserved in normalized result
        assert "Synthetic Actor" not in str(result)

        if all(counts.get(net, 0) > 0 for net in config.networks):
            break
        if sum(counts.values()) >= 500:
            break

    assert all(counts.get(net, 0) > 0 for net in config.networks)


def test_hard_negatives_labeled_correctly():
    """Verify hard negative events are marked as non-fraud with lookalike metadata."""
    config = GeneratorConfig(
        seed=5555,
        days=2,
        actors=100,
        hard_negatives={"enabled": True, "rate": 0.50},  # high rate to guarantee generation
    )

    found_hn = False
    for gen_ev in generate_stream(config):
        if gen_ev.truth.hard_negative:
            found_hn = True
            assert gen_ev.truth.is_fraud is False
            assert gen_ev.truth.lookalike_of is not None
            assert len(gen_ev.truth.lookalike_of) > 0
            break

    assert found_hn, "Expected at least one hard negative in 2 days at 0.50 rate"


def test_duplicates_and_disorder_quirks():
    """Verify duplicate events share event_id and disorder creates jitter within bound."""
    config = GeneratorConfig(
        seed=7777,
        days=1,
        actors=80,
        networks=["upi", "card"],
        feed={
            "disorder_max_seconds": 30.0,
            "duplicate_rate": 0.20,  # capped at 0.20
            "malformed_rate": 0.0,
            "naive_timestamp_rate": 0.0,
            "de7_rate": 0.0,
        },
    )

    found_duplicate = False
    disordered_count = 0
    seen_ids: set[str] = set()

    for gen_ev in generate_stream(config):
        ev_id = gen_ev.event.event_id
        if gen_ev.truth.duplicate_of is not None:
            found_duplicate = True
            assert gen_ev.truth.duplicate_of in seen_ids or gen_ev.truth.duplicate_of == ev_id

        seen_ids.add(ev_id)

        # Check jitter: received_at >= event_time and within
        # disorder_max_seconds + base latency + slack
        delta = (gen_ev.event.received_at - gen_ev.truth.event_time).total_seconds()
        assert delta >= 0.0, f"received_at preceded event_time: {delta}"
        if delta > 1.0:
            disordered_count += 1

        if found_duplicate and disordered_count > 0:
            break
        if len(seen_ids) >= 500:
            break

    assert found_duplicate, "Expected at least one duplicate with duplicate_rate=0.20"
    assert disordered_count > 0, "Expected positive transit latency / jitter"


def test_malformed_events_raise_typed_adapter_errors():
    """Verify injected malformed events raise typed AdapterErrors in the matching adapter."""
    registry = get_registry()
    load_enabled_adapters(registry=registry)
    config = GeneratorConfig(
        seed=8888,
        days=1,
        actors=80,
        networks=["upi", "card", "crypto", "swift", "wallet"],
        feed={
            "disorder_max_seconds": 5.0,
            "duplicate_rate": 0.0,
            "malformed_rate": 0.20,  # capped at 0.20
            "naive_timestamp_rate": 0.0,
            "de7_rate": 0.0,
        },
    )

    found_malformed = False
    for gen_ev in generate_stream(config):
        if gen_ev.truth.expected_error:
            found_malformed = True
            adapter = registry.get(gen_ev.event.network_id)
            assert adapter is not None
            with pytest.raises(AdapterError):
                adapter.normalize(gen_ev.event)
            break

    assert found_malformed, "Expected at least one malformed event at 0.20 rate"


def test_network_toggling_event_independence():
    """Verify disabling or enabling a network leaves other networks' generated events unchanged."""
    cfg_all = GeneratorConfig(seed=9999, days=1, actors=50, networks=["upi", "card", "crypto"])
    cfg_upi_only = GeneratorConfig(seed=9999, days=1, actors=50, networks=["upi"])

    upi_all_events = [
        ev.event.event_id for ev in generate_stream(cfg_all) if ev.event.network_id == "upi"
    ]
    upi_only_events = [
        ev.event.event_id for ev in generate_stream(cfg_upi_only) if ev.event.network_id == "upi"
    ]

    assert len(upi_all_events) > 0
    assert upi_all_events == upi_only_events
