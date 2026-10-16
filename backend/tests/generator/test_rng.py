"""Tests for deterministic RNG, substream derivation, and statistical distributions."""

from ripple.generator.rng import Rng, RngFactory, zipf_weights


def test_rng_golden_values() -> None:
    """Golden values test: assert exact sequence for a pinned master seed.

    If any distribution or RNG implementation accidentally changes, this test MUST fail loudly.
    """
    factory = RngFactory(master_seed=42)
    rng = factory.stream("test", "substream")

    # Sample a sequence of primitive distributions
    u1 = rng.random()
    u2 = rng.uniform(10.0, 50.0)
    i1 = rng.randint(100, 200)
    b1 = rng.bernoulli(0.3)
    n1 = rng.normal(0.0, 1.0)
    ln1 = rng.lognormal(100.0, 0.5)
    p1 = rng.poisson(12.5)

    # Pin expected values with high precision
    assert round(u1, 6) == 0.854893
    assert round(u2, 4) == 15.9080
    assert i1 == 154
    assert b1 is False
    assert round(n1, 4) == 0.3964
    assert round(ln1, 2) == 151.97
    assert p1 == 6


def test_substream_isolation_and_independence() -> None:
    """Assert distinct substreams produce distinct sequences, while same path produces identical."""
    factory = RngFactory(master_seed=12345)

    stream_a1 = factory.stream("actor", "0", "card")
    stream_a2 = factory.stream("actor", "0", "card")
    stream_b = factory.stream("actor", "0", "upi")

    seq_a1 = [stream_a1.random() for _ in range(5)]
    seq_a2 = [stream_a2.random() for _ in range(5)]
    seq_b = [stream_b.random() for _ in range(5)]

    assert seq_a1 == seq_a2
    assert seq_a1 != seq_b


def test_bounded_lognormal() -> None:
    """Assert bounded lognormal strictly respects lower and upper bounds."""
    rng = Rng(seed=999)
    lo = 25.0
    hi = 150.0
    for _ in range(200):
        val = rng.bounded_lognormal(median=80.0, sigma=1.0, lo=lo, hi=hi)
        assert lo <= val <= hi


def test_poisson_distribution() -> None:
    """Assert Poisson distribution produces non-negative integers with expected mean."""
    rng = Rng(seed=777)
    small_samples = [rng.poisson(3.0) for _ in range(500)]
    assert all(x >= 0 for x in small_samples)
    assert 2.7 <= (sum(small_samples) / len(small_samples)) <= 3.3

    large_samples = [rng.poisson(50.0) for _ in range(500)]
    assert all(x >= 0 for x in large_samples)
    assert 48.0 <= (sum(large_samples) / len(large_samples)) <= 52.0


def test_sample_without_replacement_and_shuffle() -> None:
    """Assert partial Fisher-Yates and shuffle maintain element uniqueness and order."""
    rng = Rng(seed=555)
    items = list(range(20))

    sample = rng.sample_without_replacement(items, 5)
    assert len(sample) == 5
    assert len(set(sample)) == 5
    assert all(x in items for x in sample)

    pool = list(range(10))
    rng.shuffle(pool)
    assert sorted(pool) == list(range(10))


def test_zipf_weights() -> None:
    """Assert Zipf weights normalize to 1.0 and are strictly decreasing."""
    weights = zipf_weights(10, s=1.2)
    assert len(weights) == 10
    assert abs(sum(weights) - 1.0) < 1e-9
    for i in range(len(weights) - 1):
        assert weights[i] > weights[i + 1]
