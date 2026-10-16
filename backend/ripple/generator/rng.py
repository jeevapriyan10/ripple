"""Deterministic random number generator and substream isolation for Ripple.

Adheres strictly to Rule R1:
- Substream isolation: all randomness derives from named child streams of a master seed.
- Determinism across Python runs: only Random.random() and getrandbits() are used from stdlib.
  All statistical distributions (uniform, randint, normal, lognormal, poisson, etc.)
  are implemented explicitly on top of Random.random() so behavior cannot drift across platforms
  or Python patch releases.
- Never use builtin hash(), datetime.now(), os.environ, or stdlib distribution methods.
"""

import bisect
import hashlib
import math
import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def zipf_weights(n: int, s: float = 1.0) -> list[float]:
    """Generate normalized Zipf distribution weights for n items with exponent s."""
    if n <= 0:
        return []
    raw = [1.0 / (math.pow(i, s)) for i in range(1, n + 1)]
    total = sum(raw)
    if total <= 0.0:
        return [1.0 / n] * n
    return [w / total for w in raw]


class Rng:
    """Deterministic random number generator bound to an isolated substream."""

    def __init__(self, seed: int) -> None:
        """Initialize with an explicit integer seed."""
        self._seed = seed
        self._r = random.Random(seed)

    @property
    def seed(self) -> int:
        """Return the seed for this substream."""
        return self._seed

    def random(self) -> float:
        """Return a uniform float in [0.0, 1.0)."""
        return self._r.random()

    def getrandbits(self, k: int) -> int:
        """Return an integer with k random bits."""
        return self._r.getrandbits(k)

    def uniform(self, a: float, b: float) -> float:
        """Return a uniform float in [a, b)."""
        return a + (b - a) * self.random()

    def randint(self, a: int, b: int) -> int:
        """Return a random integer N such that a <= N <= b (inclusive)."""
        if a > b:
            raise ValueError(f"randint: lower bound a ({a}) > upper bound b ({b})")
        if a == b:
            return a
        range_size = (b - a) + 1
        return min(b, a + int(self.random() * range_size))

    def bernoulli(self, p: float) -> bool:
        """Return True with probability p."""
        if p <= 0.0:
            return False
        if p >= 1.0:
            return True
        return self.random() < p

    def choice(self, seq: Sequence[T]) -> T:
        """Return a randomly chosen element from a non-empty sequence."""
        if not seq:
            raise IndexError("Cannot choose from an empty sequence")
        idx = self.randint(0, len(seq) - 1)
        return seq[idx]

    def weighted_choice(self, items: Sequence[T], weights: Sequence[float]) -> T:
        """Return an element sampled according to the provided positive weights."""
        if len(items) != len(weights):
            raise ValueError("items and weights must have identical length")
        if not items:
            raise IndexError("Cannot choose from empty items")

        cum_weights: list[float] = []
        total = 0.0
        for w in weights:
            total += max(0.0, float(w))
            cum_weights.append(total)

        if total <= 0.0:
            # Fall back to uniform choice if all weights are zero
            return self.choice(items)

        target = self.uniform(0.0, total)
        idx = bisect.bisect_right(cum_weights, target)
        if idx >= len(items):
            idx = len(items) - 1
        return items[idx]

    def sample_without_replacement(self, seq: Sequence[T], k: int) -> list[T]:
        """Sample k unique items from seq using partial Fisher-Yates shuffle."""
        n = len(seq)
        if k < 0 or k > n:
            raise ValueError(f"Sample size k ({k}) must be between 0 and len(seq) ({n})")
        if k == 0:
            return []

        pool = list(seq)
        for i in range(k):
            j = self.randint(i, n - 1)
            pool[i], pool[j] = pool[j], pool[i]
        return pool[:k]

    def shuffle(self, lst: list[T]) -> None:
        """In-place Fisher-Yates shuffle."""
        n = len(lst)
        for i in range(n - 1, 0, -1):
            j = self.randint(0, i)
            lst[i], lst[j] = lst[j], lst[i]

    def normal(self, mean: float = 0.0, std: float = 1.0) -> float:
        """Sample from Normal(mean, std) via Box-Muller transform with zero cached state."""
        u1 = max(1e-15, self.random())
        u2 = self.random()
        z0 = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        return mean + std * z0

    def lognormal(self, median: float, sigma: float) -> float:
        """Sample from LogNormal with specified median (e^mu) and log-scale sigma."""
        if median <= 0.0:
            raise ValueError(f"Lognormal median must be positive, got {median}")
        mu = math.log(median)
        return math.exp(self.normal(mu, sigma))

    def bounded_lognormal(self, median: float, sigma: float, lo: float, hi: float) -> float:
        """Sample from truncated LogNormal bounded within [lo, hi]."""
        if lo >= hi:
            raise ValueError(f"bounded_lognormal: lo ({lo}) must be strictly less than hi ({hi})")
        for _ in range(50):
            val = self.lognormal(median, sigma)
            if lo <= val <= hi:
                return val
        # If rejection limit is reached, clamp sample
        val = self.lognormal(median, sigma)
        return max(lo, min(hi, val))

    def exponential(self, rate: float) -> float:
        """Sample from Exponential(rate)."""
        if rate <= 0.0:
            raise ValueError(f"Exponential rate must be positive, got {rate}")
        u = max(1e-15, self.random())
        return -math.log(u) / rate

    def pareto(self, alpha: float, x_m: float = 1.0) -> float:
        """Sample from Pareto(alpha, x_m)."""
        if alpha <= 0.0 or x_m <= 0.0:
            raise ValueError("Pareto parameters alpha and x_m must be strictly positive")
        u = max(1e-15, self.random())
        return x_m / math.pow(u, 1.0 / alpha)

    def poisson(self, lam: float) -> int:
        """Sample from Poisson(lam). Uses Knuth algorithm for lam < 30, normal approx for large."""
        if lam <= 0.0:
            return 0
        if lam < 30.0:
            # Knuth algorithm
            limit = math.exp(-lam)
            k = 0
            p = 1.0
            while p > limit:
                k += 1
                p *= self.random()
            return k - 1
        # Large lambda: rounded normal approximation
        val = round(self.normal(lam, math.sqrt(lam)))
        return max(0, int(val))

    def jitter(self, value: float, fraction: float) -> float:
        """Perturb value uniformly by +/- fraction."""
        if fraction <= 0.0:
            return value
        return value * (1.0 + self.uniform(-fraction, fraction))


class RngFactory:
    """Master random factory creating isolated, named RNG substreams."""

    def __init__(self, master_seed: int) -> None:
        self.master_seed = master_seed

    def stream(self, *name_parts: str) -> Rng:
        """Derive a named substream deterministically using SHA-256."""
        path_str = "/".join(str(p) for p in name_parts)
        composite = f"{self.master_seed}|{path_str}"
        digest = hashlib.sha256(composite.encode("utf-8")).digest()
        # Take first 16 bytes for a 128-bit seed
        substream_seed = int.from_bytes(digest[:16], "big")
        return Rng(substream_seed)
