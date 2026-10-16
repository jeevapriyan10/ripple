"""Background traffic planner for SWIFT correspondent banking."""

import uuid
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from ripple.generator.engine import SimulationContext, SourcePlanner
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.timeutil import add_business_days
from ripple.generator.traffic.base import sample_amount, sample_tick_actors
from ripple.generator.world import COUNTRY_SPECS

# Fictional constant FX rates to USD base
FX_TO_USD: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.25,
    "AED": 0.27,
    "SGD": 0.75,
    "INR": 0.012,
}


class SwiftTrafficPlanner(SourcePlanner):
    """Generates normal, non-fraudulent SWIFT wire transfer volumes."""

    source_key: str = "bg_swift"

    def __init__(self) -> None:
        self._plan_seq = 0

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        if "swift" not in ctx.config.networks:
            return []

        swift_actors = ctx.world.actors_for_network("swift")
        if not swift_actors:
            return []

        calib = ctx.calibration
        rng = ctx.factory.stream("traffic", "swift", str(int(tick_start.timestamp())))
        base_rate = float(calib.get("swift.base_rate_per_actor_hour", 0.002))

        initiators = sample_tick_actors(
            eligible_actors=swift_actors,
            tick_start=tick_start,
            base_rate_per_hour=base_rate,
            rng=rng,
            hourly_curve=calib.get("diurnal.hourly_curve"),
            weekday_factors=calib.get("diurnal.weekday_factors"),
        )

        plans: list[TxnPlan] = []
        median_amt = float(calib.get("swift.amount_median", 15000.0))
        sigma_amt = float(calib.get("swift.amount_sigma", 1.1))

        plan_idx = 0
        for debtor in initiators:
            eligible_creditors = [a for a in swift_actors if a.actor_id != debtor.actor_id]
            if not eligible_creditors:
                continue

            plan_idx += 1
            plan_id = f"swift_bg_{int(tick_start.timestamp())}_{plan_idx:04d}"
            creditor = rng.choice(eligible_creditors)

            txn_kind = rng.choice(["supplier_payment", "remittance", "trade_payment"])
            purpose_code = {
                "supplier_payment": "SUPP",
                "remittance": "GIFT",
                "trade_payment": "TRAD",
            }[txn_kind]

            debtor_curr = COUNTRY_SPECS.get(debtor.country, {}).get("currency", "USD")
            creditor_curr = COUNTRY_SPECS.get(creditor.country, {}).get("currency", "USD")

            amount = sample_amount(rng, median_amt, sigma_amt, currency=creditor_curr)

            # FX conversion if cross-currency
            instructed_amt = None
            instructed_curr = None
            exchange_rate = None
            if debtor_curr != creditor_curr:
                instructed_curr = debtor_curr
                rate_d = FX_TO_USD.get(debtor_curr, 1.0)
                rate_c = FX_TO_USD.get(creditor_curr, 1.0)
                fx_multiplier = rate_c / rate_d
                exchange_rate = f"{fx_multiplier:.4f}"
                instructed_amt = (amount * Decimal(str(fx_multiplier))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_EVEN
                )

            # Reference identification: small share omit UETR to test fallback key
            uetr = None
            sender_ref = f"REF-{debtor.actor_id}-{self._plan_seq:06d}"
            if not rng.bernoulli(0.05):
                uetr = str(uuid.UUID(bytes=rng.getrandbits(128).to_bytes(16, "big")))

            debtor_ident = debtor.swift_identities
            creditor_ident = creditor.swift_identities

            d_acc = debtor_ident.get("iban") or debtor_ident.get("account_number")
            c_acc = creditor_ident.get("iban") or creditor_ident.get("account_number")

            d_agent_bic = debtor_ident["bic"]
            c_agent_bic = creditor_ident["bic"]

            # Intermediaries
            intermediaries = []
            if rng.bernoulli(0.60):
                intermediaries.append(
                    {
                        "bic": "ZRPXUS33XXX",
                        "received_at": (tick_start + timedelta(hours=4)).strftime(
                            "%Y-%m-%dT%H:%M:%SZ"
                        ),
                        "charge": {"amount": "15.00", "currency": creditor_curr},
                    }
                )

            # Eagerly schedule SWIFT gpi lifecycle
            stages: list[StagePlan] = []
            t_instr = tick_start + timedelta(minutes=rng.uniform(0.0, 50.0))
            value_date_str = add_business_days(t_instr, 2).strftime("%Y-%m-%d")

            # Stage 1: payment instruction
            stages.append(StagePlan(name="payment", event_time=t_instr, outcome_data={}))

            # Stage 2: ACTC (1-2 hours later)
            t_actc = t_instr + timedelta(hours=rng.uniform(1.0, 3.0))
            stages.append(
                StagePlan(name="status", event_time=t_actc, outcome_data={"status": "ACTC"})
            )

            r_outcome = rng.random()
            if r_outcome < float(calib.get("swift.rjct_rate", 0.01)):
                # Rejected
                t_rjct = t_actc + timedelta(hours=rng.uniform(2.0, 8.0))
                stages.append(
                    StagePlan(
                        name="status",
                        event_time=t_rjct,
                        outcome_data={"status": "RJCT", "status_reason_code": "AC04"},
                    )
                )

            else:
                # ACSP (settlement in process) next business day
                t_acsp = add_business_days(t_actc, 1)
                stages.append(
                    StagePlan(
                        name="status",
                        event_time=t_acsp,
                        outcome_data={"status": "ACSP", "intermediaries": intermediaries},
                    )
                )

                # ACCC (final settlement)
                t_accc = add_business_days(t_acsp, 1) + timedelta(hours=rng.uniform(1.0, 4.0))
                stages.append(
                    StagePlan(
                        name="status",
                        event_time=t_accc,
                        outcome_data={"status": "ACCC", "intermediaries": intermediaries},
                    )
                )

                # Rare recall request (0.5%)
                if rng.bernoulli(float(calib.get("swift.recall_rate", 0.005))):
                    t_recall = t_accc + timedelta(days=rng.uniform(2.0, 10.0))
                    stages.append(
                        StagePlan(
                            name="recall_request",
                            event_time=t_recall,
                            outcome_data={"recall_reason": rng.choice(["FRAD", "DUPL", "TECH"])},
                        )
                    )

            attrs: dict[str, Any] = {
                "uetr": uetr,
                "sender_reference": sender_ref,
                "value_date": value_date_str,
                "instructed_amount": instructed_amt,
                "instructed_currency": instructed_curr,
                "exchange_rate": exchange_rate,
                "charge_bearer": rng.choice(["SHA", "OUR", "BEN"]),
                "debtor_account": d_acc,
                "debtor_country": debtor.country,
                "debtor_name": debtor.name,
                "creditor_account": c_acc,
                "creditor_country": creditor.country,
                "creditor_name": creditor.name,
                "debtor_agent_bic": d_agent_bic,
                "creditor_agent_bic": c_agent_bic,
                "purpose_code": purpose_code,
            }

            plan = TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="swift",
                kind=txn_kind,
                sender_actor_id=debtor.actor_id,
                receiver_actor_id=creditor.actor_id,
                amount=amount,
                currency=creditor_curr,
                stages=stages,
                attrs=attrs,
                truth_seed={"scenario_id": None, "is_fraud": False, "hard_negative": False},
            )
            plans.append(plan)

        return plans
