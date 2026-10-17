"""Background traffic planner for Card payment network."""

from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from ripple.generator.engine import SimulationContext, SourcePlanner
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.timeutil import add_business_days
from ripple.generator.traffic.base import (
    get_actor_telemetry,
    sample_amount,
    sample_tick_actors,
)
from ripple.generator.world import COUNTRY_SPECS


class CardTrafficPlanner(SourcePlanner):
    """Generates normal, non-fraudulent Card background transaction volume."""

    source_key: str = "bg_card"

    def __init__(self) -> None:
        self._plan_seq = 0

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        if "card" not in ctx.config.networks:
            return []

        card_actors = ctx.world.actors_for_network("card")
        if not card_actors:
            return []

        calib = ctx.calibration
        rng = ctx.factory.stream("traffic", "card", str(int(tick_start.timestamp())))
        base_rate = float(calib.get("card.base_rate_per_actor_hour", 0.03))

        initiators = sample_tick_actors(
            eligible_actors=card_actors,
            tick_start=tick_start,
            base_rate_per_hour=base_rate,
            rng=rng,
            hourly_curve=calib.get("diurnal.hourly_curve"),
            weekday_factors=calib.get("diurnal.weekday_factors"),
        )

        merchants = [a for a in card_actors if a.kind == "merchant"]
        if not merchants:
            return []

        plans: list[TxnPlan] = []
        pos_ratio = float(calib.get("card.pos_ratio", 0.55))
        ecom_ratio = float(calib.get("card.ecommerce_ratio", 0.30))
        atm_ratio = float(calib.get("card.atm_ratio", 0.10))
        median_amt = float(calib.get("card.amount_median", 45.0))
        sigma_amt = float(calib.get("card.amount_sigma", 1.0))
        approval_rate = float(calib.get("card.approval_rate", 0.93))
        mdr_rate = float(calib.get("card.mdr_fee_rate", 0.015))

        plan_idx = 0
        for cardholder in initiators:
            # Must have card tokens
            c_ident = cardholder.card_identities
            tokens = c_ident.get("tokens", [])
            if not tokens:
                continue

            plan_idx += 1
            plan_id = f"card_bg_{int(tick_start.timestamp())}_{plan_idx:04d}"

            r_kind = rng.random()
            if r_kind < pos_ratio:
                channel = "pos"
                proc_code = "00"
                merchant = rng.choice(merchants)
            elif r_kind < pos_ratio + ecom_ratio:
                channel = "ecommerce"
                proc_code = "00"
                merchant = rng.choice(merchants)
            elif r_kind < pos_ratio + ecom_ratio + atm_ratio:
                channel = "atm"
                proc_code = "01"
                merchant = rng.choice(merchants)  # Terminal provider
            else:
                channel = "recurring"
                proc_code = "00"
                merchant = rng.choice(merchants)

            currency = COUNTRY_SPECS.get(merchant.country, {}).get("currency", "USD")
            amount = sample_amount(rng, median_amt, sigma_amt, currency=currency)

            # Card tokens and BIN
            token_idx = rng.randint(0, len(tokens) - 1)
            token = tokens[token_idx]
            bin_val = c_ident.get("bins", ["499999"])[token_idx]
            last4_val = c_ident.get("last4s", ["1234"])[token_idx]

            dev, ip = get_actor_telemetry(cardholder, ctx.factory)
            m_ident = merchant.card_identities

            minute_offset = rng.uniform(0.0, 50.0)
            t_auth = tick_start + timedelta(minutes=minute_offset)
            is_approved = rng.bernoulli(approval_rate)

            # Quirk check: DE7 formatting
            use_de7 = rng.bernoulli(ctx.config.feed.de7_rate)

            stages: list[StagePlan] = []
            if not is_approved:
                # Declined authorization terminates lifecycle
                decline_code = rng.choice(["05", "51", "61"])
                s_auth = StagePlan(
                    name="authorization",
                    event_time=t_auth,
                    outcome_data={
                        "response_code": decline_code,
                        "use_de7": use_de7,
                    },
                )
                stages.append(s_auth)
            else:
                # Approved authorization -> clearing -> settlement
                mdr_fee_amt = (amount * Decimal(str(mdr_rate))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )
                s_auth = StagePlan(
                    name="authorization",
                    event_time=t_auth,
                    outcome_data={
                        "response_code": "00",
                        "mdr_fee": mdr_fee_amt,
                        "use_de7": use_de7,
                    },
                )
                stages.append(s_auth)

                # Same-day reversal check (0.5%)
                if rng.bernoulli(0.005):
                    t_rev = t_auth + timedelta(minutes=rng.uniform(5.0, 45.0))
                    stages.append(StagePlan(name="reversal", event_time=t_rev, outcome_data={}))
                else:
                    # Clearing on next business day
                    t_clear = add_business_days(t_auth, 1) + timedelta(hours=rng.uniform(1.0, 6.0))
                    # Tip adjustment for restaurants (MCC 5812)
                    clear_amount = amount
                    if merchant.mcc == "5812" and rng.bernoulli(0.35):
                        clear_amount = (amount * Decimal("1.18")).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_EVEN
                        )

                    stages.append(
                        StagePlan(
                            name="clearing",
                            event_time=t_clear,
                            outcome_data={"amount": clear_amount},
                        )
                    )

                    # Settlement 1-2 business days later
                    t_settle = add_business_days(t_clear, 1) + timedelta(
                        hours=rng.uniform(1.0, 4.0)
                    )
                    stages.append(
                        StagePlan(
                            name="settlement",
                            event_time=t_settle,
                            outcome_data={
                                "amount": clear_amount,
                                "settlement_date": t_settle,
                            },
                        )
                    )

                    # Rare legitimate chargeback 10-60 days later (0.3%)
                    if rng.bernoulli(0.003):
                        t_cb = t_settle + timedelta(days=rng.uniform(10.0, 60.0))
                        stages.append(
                            StagePlan(
                                name="chargeback",
                                event_time=t_cb,
                                outcome_data={
                                    "amount": clear_amount,
                                    "reason_code": rng.choice(["4837", "4853"]),
                                },
                            )
                        )

            attrs: dict[str, Any] = {
                "auth_time": t_auth,
                "processing_code": proc_code,
                "card_token": token,
                "bin": bin_val,
                "last4": last4_val,
                "scheme": rng.choice(["scheme_a", "scheme_b"]),
                "acquirer_id": m_ident.get("acquirer_id", "ACQ01"),
                "issuer_id": c_ident.get("issuer_id", "ISS01"),
                "issuer_country": cardholder.country,
                "merchant_id": m_ident.get("merchant_id", "mid_001"),
                "merchant_name": f"Store {merchant.actor_id}",
                "cardholder_name": cardholder.name,
                "mcc": merchant.mcc or "5411",
                "merchant_country": merchant.country,
                "merchant_city": "Capital City",
                "channel": channel,
                "device_id": dev,
                "ip_address": ip,
            }

            plan = TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="card",
                kind="card_payment",
                sender_actor_id=cardholder.actor_id,
                receiver_actor_id=merchant.actor_id,
                amount=amount,
                currency=currency,
                stages=stages,
                attrs=attrs,
                truth_seed={"scenario_id": None, "is_fraud": False, "hard_negative": False},
            )
            plans.append(plan)

        return plans
