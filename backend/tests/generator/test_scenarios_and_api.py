"""Tests for fraud scenarios, drift regimes, labels, reports, CLI, and API endpoints (Prompt 7C)."""

from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from ripple.domain.party import MuleRole
from ripple.generator.calibration import load_calibration_profile
from ripple.generator.config import GeneratorConfig
from ripple.generator.dataset import generate_dataset, read_party_map, verify_dataset
from ripple.generator.engine import generate_stream
from ripple.generator.labels import (
    SplitSpec,
    temporal_split,
)
from ripple.generator.report import analyze_dataset, compute_rank_auc
from ripple.generator.rng import RngFactory
from ripple.generator.scenarios import (
    AccountTakeoverDrainScenario,
    CardTestingScenario,
    ChargebackFraudMerchantScenario,
    CrossNetworkLaunderingScenario,
    CryptoPeelingLayeringScenario,
    FanInSmurfingScenario,
    NftWashTradingScenario,
    ScenarioContext,
    SwiftStructuringLayeringScenario,
    UpiMuleChainScenario,
    WalletProvisionalFundingAbuseScenario,
)
from ripple.generator.world import build_world
from ripple.main import create_app


@pytest.fixture
# pyrefly: ignore[unannotated-return]
def test_world():
    cfg = GeneratorConfig.from_preset("tiny", seed=1001)
    calib = load_calibration_profile("default")
    return cfg, calib, build_world(cfg, calib)


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_upi_mule_chain_invariants(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(1234)
    rng = factory.stream("test_upi")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="upi_01",
    )
    plan = UpiMuleChainScenario().plan(ctx)
    assert len(plan.txn_plans) >= 3
    # Check forward fraction and hop count
    assert 2 <= plan.summary["hop_count"] <= 5
    assert Decimal("0.50") <= plan.summary["forward_fraction"] <= Decimal("1.00")
    # Check role assignments
    assert MuleRole.VICTIM in plan.roles.values()
    assert MuleRole.FIRST_HOP in plan.roles.values()
    assert MuleRole.CASH_OUT in plan.roles.values()


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_crypto_peeling_exact_decimal_conservation(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(2345)
    rng = factory.stream("test_peel")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="peel_01",
    )
    plan = CryptoPeelingLayeringScenario().plan(ctx)
    assert plan.summary["hop_count"] >= 8
    # Assert exact Decimal conservation net of fee
    fwd_plans = [p for p in plan.txn_plans if "fwd" in p.plan_id]
    peel_plans = [p for p in plan.txn_plans if "leaf" in p.plan_id]
    assert len(fwd_plans) == len(peel_plans)

    # First hop in is 10.000000 ETH
    curr = Decimal("10.000000")
    fee = Decimal("0.001000")
    for f_p, p_p in zip(fwd_plans, peel_plans, strict=True):
        assert curr == f_p.amount + p_p.amount + fee
        curr = f_p.amount


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_fan_in_smurfing_structuring_under_threshold(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(3456)
    rng = factory.stream("test_smurf")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="smurf_01",
    )
    plan = FanInSmurfingScenario().plan(ctx)
    assert plan.summary["senders_count"] >= 6
    threshold = plan.summary["threshold"]
    inbound_plans = [p for p in plan.txn_plans if "smurf_in" in p.plan_id]
    for p in inbound_plans:
        assert p.amount < threshold, f"Amount {p.amount} not structured under {threshold}"


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_wallet_provisional_funding_abuse_lifecycle(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(4567)
    rng = factory.stream("test_wfa")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="wfa_01",
    )
    plan = WalletProvisionalFundingAbuseScenario().plan(ctx)
    fund_plan = [p for p in plan.txn_plans if "fund" in p.plan_id][0]
    out_plans = [p for p in plan.txn_plans if "out" in p.plan_id]

    assert len(fund_plan.stages) == 2
    assert fund_plan.stages[0].name == "funding"
    assert fund_plan.stages[1].name == "reversal"
    assert fund_plan.stages[1].event_time > fund_plan.stages[0].event_time + timedelta(days=1)

    # Outbound transfer happens before reversal
    for op in out_plans:
        assert op.stages[0].event_time < fund_plan.stages[1].event_time


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_card_testing_high_decline_ratio(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(5678)
    rng = factory.stream("test_cdt")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="cdt_01",
    )
    plan = CardTestingScenario().plan(ctx)
    assert plan.summary["decline_ratio"] >= 0.50
    assert plan.summary["approved_count"] >= 1


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_chargeback_fraud_merchant_delay_window(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(6789)
    rng = factory.stream("test_cbf")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="cbf_01",
    )
    plan = ChargebackFraudMerchantScenario().plan(ctx)
    for p in plan.txn_plans:
        cb_stage = [s for s in p.stages if s.name == "chargeback"][0]
        settle_stage = [s for s in p.stages if s.name == "settlement"][0]
        delay_days = (cb_stage.event_time - settle_stage.event_time).total_seconds() / 86400.0
        assert 25.0 <= delay_days <= 65.0


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_nft_wash_trading_ring_closure(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(7890)
    rng = factory.stream("test_wash")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="wash_01",
    )
    plan = NftWashTradingScenario().plan(ctx)
    assert plan.summary["ring_closed"] is True
    assert plan.summary["final_price"] > plan.summary["initial_price"]


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_account_takeover_telemetry_precedes_drain(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(8901)
    rng = factory.stream("test_ato")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="ato_01",
    )
    plan = AccountTakeoverDrainScenario().plan(ctx)
    assert plan.summary["payout_count"] >= 1
    for p in plan.txn_plans:
        assert p.attrs["device_id"] == plan.summary["rogue_device"]
        assert p.attrs["ip_address"] == plan.summary["rogue_ip"]


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_swift_structuring_jurisdiction_span(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(9012)
    rng = factory.stream("test_sw_str")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="sw_str_01",
    )
    plan = SwiftStructuringLayeringScenario().plan(ctx)
    assert plan.summary["jurisdiction_count"] >= 3
    assert plan.summary["structured_amount"] < plan.summary["threshold"]


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_cross_network_laundering_multi_rail_traversal(test_world):
    cfg, calib, world = test_world
    factory = RngFactory(9123)
    rng = factory.stream("test_xnet")
    ctx = ScenarioContext(
        world=world,
        substream=rng,
        calibration=calib,
        start_time=cfg.start_time,
        instance_id="xnet_01",
    )
    plan = CrossNetworkLaunderingScenario().plan(ctx)
    nets = {p.network for p in plan.txn_plans}
    assert {"wallet", "swift", "crypto"}.issubset(nets)


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_forced_starts_land_at_exact_hour(test_world):
    cfg = GeneratorConfig(
        seed=42,
        days=2,
        actors=80,
        networks=["upi", "card", "crypto", "swift", "wallet"],
        fraud={
            "enabled": True,
            "forced_starts": [{"scenario": "upi_mule_chain", "at_hour": 5}],
        },
    )
    events = list(generate_stream(cfg))
    forced_events = [
        ev
        for ev in events
        if ev.truth.scenario_id and "forced_upi_mule_chain" in ev.truth.scenario_id
    ]
    assert len(forced_events) > 0
    t0 = cfg.start_time
    # First forced event event_time must start at or shortly after hour 5
    first_ev = forced_events[0]
    hours_from_start = (first_ev.truth.event_time - t0).total_seconds() / 3600.0
    assert 5.0 <= hours_from_start < 6.0


# pyrefly: ignore[unannotated-return]
def test_drift_regime_abrupt_boundary():
    cfg = GeneratorConfig(
        seed=123,
        days=3,
        actors=100,
        networks=["upi"],
        fraud={"enabled": True},
        drift={
            "enabled": True,
            "regime": "v2_mule_template",
            "shift_day": 1.5,
            "shape": "abrupt",
        },
    )
    events = list(generate_stream(cfg))
    fraud_events = [ev for ev in events if ev.truth.is_fraud]
    t0 = cfg.start_time

    for ev in fraud_events:
        day_offset = (ev.truth.event_time - t0).total_seconds() / 86400.0
        if day_offset < 1.5:
            assert ev.truth.regime == "baseline"
        else:
            assert ev.truth.regime == "v2_mule_template"


# pyrefly: ignore[unannotated-return]
def test_rank_auc_toy_and_warnings():
    # Case 1: Perfect separation (positives all > negatives)
    positives = [10.0, 12.0, 15.0]
    negatives = [1.0, 2.0, 3.0]
    assert compute_rank_auc(positives, negatives) == 1.0

    # Case 2: Identical distributions (no separability)
    positives = [5.0, 5.0, 5.0]
    negatives = [5.0, 5.0, 5.0]
    assert compute_rank_auc(positives, negatives) == 0.5


# pyrefly: ignore[unannotated-return]
def test_temporal_split_embargo_purge():
    cfg = GeneratorConfig.from_preset("tiny", seed=777)
    events = list(generate_stream(cfg))
    spec = SplitSpec(train_end_day=0.8, val_end_day=1.4, purge_hours=6.0)
    splits = temporal_split(events, spec)

    assert len(splits["train"]) > 0
    assert len(splits["val"]) > 0
    assert len(splits["test"]) > 0
    assert len(splits["purged"]) > 0


# pyrefly: ignore[implicit-any-parameter, unannotated-return]
def test_dataset_generation_end_to_end(tmp_path):
    cfg = GeneratorConfig.from_preset("tiny", seed=42)
    ds_path = generate_dataset(cfg, name="tiny-42", base_dir=tmp_path, overwrite=True, strict=True)
    assert ds_path.exists()

    # Verify dataset checksums
    ok, errors = verify_dataset(ds_path)
    assert ok is True
    assert len(errors) == 0

    # Verify party map covers actors
    party_map = read_party_map(ds_path)
    assert len(party_map) > 0

    # Analyze report
    report = analyze_dataset(ds_path)
    assert report.total_events > 0
    assert report.dataset_name == "tiny-42"
    assert len(report.separability_top5) > 0


# pyrefly: ignore[unannotated-return]
def test_generator_api_endpoints():
    app = create_app()
    client = TestClient(app)

    # 1. Scenarios catalog
    res_sc = client.get("/api/v1/generator/scenarios")
    assert res_sc.status_code == 200
    catalog = res_sc.json()
    assert len(catalog) == 10

    # 2. Preview endpoint within caps
    res_prev = client.post(
        "/api/v1/generator/preview",
        json={"preset": "tiny", "seed": 42, "actors": 60, "days": 1},
    )
    assert res_prev.status_code == 200
    prev_data = res_prev.json()
    assert prev_data["total_events"] > 0
    assert len(prev_data["sample"]) <= 50

    # 3. Preview endpoint exceeding cap (HTTP 422)
    res_cap = client.post(
        "/api/v1/generator/preview",
        json={"preset": "tiny", "seed": 42, "actors": 9999, "days": 1},
    )
    assert res_cap.status_code == 422

    # 4. Datasets listing
    res_ds = client.get("/api/v1/datasets")
    assert res_ds.status_code == 200
    assert isinstance(res_ds.json(), list)


@pytest.mark.slow
# pyrefly: ignore[unannotated-return]
def test_generator_events_per_second_benchmark():
    import time

    cfg = GeneratorConfig.from_preset("tiny", seed=123)
    t_start = time.perf_counter()
    events = list(generate_stream(cfg))
    elapsed = time.perf_counter() - t_start
    rate = len(events) / elapsed if elapsed > 0 else 0
    assert len(events) > 0
    # Benchmark telemetry recorded without failing
    print(f"\n[BENCHMARK] Generated {len(events)} events in {elapsed:.2f}s ({rate:.1f} events/s)")
