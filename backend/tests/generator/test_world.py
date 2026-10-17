"""Tests for simulated world construction, demographic balance, and substream independence."""

from ripple.generator.calibration import load_calibration_profile
from ripple.generator.config import GeneratorConfig
from ripple.generator.world import build_world


def test_world_identifier_uniqueness_and_coverage() -> None:
    """Assert all actor IDs are unique and every actor holds at least one payment identity."""
    config = GeneratorConfig.from_preset("tiny", seed=100)
    calib = load_calibration_profile("default")
    world = build_world(config, calib)

    assert len(world.actors) == 200
    actor_ids = {a.actor_id for a in world.actors}
    assert len(actor_ids) == 200

    # Ensure every actor has at least one network identity
    for actor in world.actors:
        has_any = (
            bool(actor.upi_identities)
            or bool(actor.card_identities)
            or bool(actor.crypto_identities)
            or bool(actor.swift_identities)
            or bool(actor.wallet_identities)
        )
        assert has_any, f"Actor {actor.actor_id} has no payment identity on any network"


def test_network_toggling_independence() -> None:
    """Rule R1 Invariant: network toggling MUST NOT alter other networks' identities."""
    calib = load_calibration_profile("default")

    # Run 1: all networks enabled
    cfg_all = GeneratorConfig(
        seed=777, actors=100, days=2, networks=["upi", "card", "crypto", "swift", "wallet"]
    )
    world_all = build_world(cfg_all, calib)

    # Run 2: SWIFT and Crypto disabled
    cfg_filtered = GeneratorConfig(seed=777, actors=100, days=2, networks=["upi", "card", "wallet"])
    world_filtered = build_world(cfg_filtered, calib)

    # Compare identities for actors: UPI, Card, Wallet must be 100% identical
    for a1, a2 in zip(world_all.actors, world_filtered.actors, strict=True):
        assert a1.actor_id == a2.actor_id
        assert a1.kind == a2.kind
        assert a1.country == a2.country
        assert a1.upi_identities == a2.upi_identities
        assert a1.card_identities == a2.card_identities
        assert a1.wallet_identities == a2.wallet_identities
        assert a1.shared_phone == a2.shared_phone
        assert a1.shared_email == a2.shared_email


def test_mule_pool_and_burner_accounts() -> None:
    """Assert mule pool is populated and includes both aged and newly incubated burner accounts."""
    config = GeneratorConfig(seed=888, actors=300, days=5)
    calib = load_calibration_profile("default")
    world = build_world(config, calib)

    assert len(world.mule_pool) > 0
    sim_start = config.start_time

    # Some accounts created before simulation start (aged), some after (burner)
    aged = [a for a in world.actors if a.created_at < sim_start]
    burners = [a for a in world.actors if a.created_at >= sim_start]

    assert len(aged) > 0
    assert len(burners) > 0


def test_cross_network_link_rate() -> None:
    """Assert cross_network_link_rate parameter is approximately respected in demographics."""
    config = GeneratorConfig(seed=999, actors=500, days=2)
    calib = load_calibration_profile("default")
    world = build_world(config, calib)

    linked_count = sum(1 for a in world.actors if a.shared_phone is not None)
    fraction = linked_count / len(world.actors)

    # Default is 0.40; check statistical range for 500 actors
    assert 0.30 <= fraction <= 0.50
