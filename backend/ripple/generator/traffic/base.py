"""Common background traffic mechanics, heavy-tailed amount sampling, and counterparties."""

from collections.abc import Sequence
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal

from ripple.adapters.currencies import lookup_currency_by_alpha3
from ripple.generator.identifiers import device_id, ip_address
from ripple.generator.rng import Rng, RngFactory
from ripple.generator.timeutil import diurnal_weight, to_local
from ripple.generator.world import Actor, World

# Psychological round amount steps (for psychological rounding bias)
ROUND_STEPS = [10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000]


def sample_amount(
    rng: Rng,
    median: float,
    sigma: float,
    currency: str,
    cap: float | None = None,
    round_bias: float = 0.35,
) -> Decimal:
    """Sample monetary amount from LogNormal with psychological rounding and exact decimal scale."""
    _, scale = lookup_currency_by_alpha3(currency)

    # 1. Sample raw heavy-tailed amount
    raw_val = rng.lognormal(median=median, sigma=sigma)
    if cap is not None:
        raw_val = min(cap, raw_val)
    raw_val = max(1.0, raw_val)

    # 2. Psychological rounding bias (humans frequently pay round numbers: 50, 100, 500)
    if rng.bernoulli(round_bias):
        # Find closest round step
        applicable_steps = [s for s in ROUND_STEPS if s <= raw_val * 2.0]
        if applicable_steps:
            step = rng.choice(applicable_steps)
            rounded = round(raw_val / step) * step
            if rounded > 0:
                raw_val = float(rounded)

    # 3. Quantize with Decimal ROUND_HALF_EVEN to exact currency minor unit scale
    dec_format = f"{raw_val:.{scale}f}"
    quantizer = Decimal("10") ** -scale
    return Decimal(dec_format).quantize(quantizer, rounding=ROUND_HALF_EVEN)


def get_actor_telemetry(actor: Actor, factory: RngFactory) -> tuple[str, str]:
    """Retrieve or deterministically derive primary device ID and IP address for an actor."""
    rng = factory.stream("world", "actor", str(actor.actor_index), "telemetry")
    dev = device_id(rng)
    ip = ip_address(rng)
    return dev, ip


class ContactBook:
    """Deterministic, lazy contact list cache for peer-to-peer counterparties."""

    def __init__(self, factory: RngFactory, world: World) -> None:
        self.factory = factory
        self.world = world
        self._cache: dict[str, list[Actor]] = {}

    def get_contacts(
        self, actor: Actor, eligible_actors: Sequence[Actor], k: int = 4
    ) -> list[Actor]:
        """Return stable small list of repeat counterparties for an actor."""
        if actor.actor_id in self._cache:
            return self._cache[actor.actor_id]

        rng = self.factory.stream("contacts", actor.actor_id)
        # Filter same-country eligible actors
        same_country = [
            a
            for a in eligible_actors
            if a.country == actor.country and a.actor_id != actor.actor_id
        ]
        pool = (
            same_country
            if len(same_country) >= k
            else [a for a in eligible_actors if a.actor_id != actor.actor_id]
        )
        if not pool:
            return []

        sample_k = min(k, len(pool))
        contacts = rng.sample_without_replacement(pool, sample_k)
        self._cache[actor.actor_id] = contacts
        return contacts


def sample_tick_actors(
    eligible_actors: list[Actor],
    tick_start: datetime,
    base_rate_per_hour: float,
    rng: Rng,
    hourly_curve: list[float] | None = None,
    weekday_factors: list[float] | None = None,
) -> list[Actor]:
    """Sample actors initiating transactions in a virtual tick using Poisson volume
    and weighted bisect.

    Scales strictly with volume O(events), not O(actors).
    """
    if not eligible_actors:
        return []

    # Filter out actors not yet created as of tick_start
    active_actors = [a for a in eligible_actors if a.created_at <= tick_start]
    if not active_actors:
        return []

    # Group by timezone for accurate diurnal activity weight
    tz_groups: dict[str, list[Actor]] = {}
    for a in active_actors:
        tz_groups.setdefault(a.timezone, []).append(a)

    selected_actors: list[Actor] = []

    for tz_name, actors_in_tz in tz_groups.items():
        local_time = to_local(tick_start, tz_name)
        d_weight = diurnal_weight(
            local_hour=local_time.hour,
            weekday=local_time.weekday(),
            hourly_curve=hourly_curve,
            weekday_factors=weekday_factors,
        )

        sum_activity = sum(a.activity_multiplier for a in actors_in_tz)
        expected_events = sum_activity * base_rate_per_hour * d_weight
        num_events = rng.poisson(expected_events)

        if num_events <= 0:
            continue

        # Cumulative weight sampling (bisect)
        weights = [a.activity_multiplier for a in actors_in_tz]
        for _ in range(num_events):
            picked = rng.weighted_choice(actors_in_tz, weights)
            selected_actors.append(picked)

    return selected_actors
