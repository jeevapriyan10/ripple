"""Background traffic planner for UPI instant payments."""

from datetime import datetime, timedelta
from typing import Any

from ripple.generator.engine import SimulationContext, SourcePlanner
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.traffic.base import (
    ContactBook,
    get_actor_telemetry,
    sample_amount,
    sample_tick_actors,
)


class UpiTrafficPlanner(SourcePlanner):
    """Generates normal, non-fraudulent UPI background payment volume."""

    source_key: str = "bg_upi"

    def __init__(self) -> None:
        self._contact_book: ContactBook | None = None
        self._plan_seq = 0

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        if "upi" not in ctx.config.networks:
            return []

        if self._contact_book is None or self._contact_book.world is not ctx.world:
            self._contact_book = ContactBook(ctx.factory, ctx.world)

        upi_actors = ctx.world.actors_for_network("upi")
        if not upi_actors:
            return []

        calib = ctx.calibration
        rng = ctx.factory.stream("traffic", "upi", str(int(tick_start.timestamp())))
        base_rate = float(calib.get("upi.base_rate_per_actor_hour", 0.04))

        initiators = sample_tick_actors(
            eligible_actors=upi_actors,
            tick_start=tick_start,
            base_rate_per_hour=base_rate,
            rng=rng,
            hourly_curve=calib.get("diurnal.hourly_curve"),
            weekday_factors=calib.get("diurnal.weekday_factors"),
        )

        merchants = [a for a in upi_actors if a.kind == "merchant"]
        businesses = [a for a in upi_actors if a.kind in {"business", "psp"}]
        individuals = [a for a in upi_actors if a.kind == "individual"]

        plans: list[TxnPlan] = []
        p2p_ratio = float(calib.get("upi.p2p_ratio", 0.60))
        merchant_ratio = float(calib.get("upi.merchant_qr_ratio", 0.25))
        bill_ratio = float(calib.get("upi.bill_pay_ratio", 0.10))
        median_amt = float(calib.get("upi.amount_median", 450.0))
        sigma_amt = float(calib.get("upi.amount_sigma", 1.2))
        cap_amt = float(calib.get("upi.cap_amount", 100000.0))

        plan_idx = 0
        for payer in initiators:
            plan_idx += 1
            plan_id = f"upi_bg_{int(tick_start.timestamp())}_{plan_idx:04d}"
            r_kind = rng.random()

            if r_kind < p2p_ratio and individuals:
                txn_kind = "p2p_pay"
                contacts = self._contact_book.get_contacts(payer, individuals, k=4)
                if contacts and rng.bernoulli(0.70):
                    payee = rng.choice(contacts)
                else:
                    eligible_payees = [a for a in individuals if a.actor_id != payer.actor_id]
                    payee = rng.choice(eligible_payees) if eligible_payees else payer

            elif r_kind < p2p_ratio + merchant_ratio and merchants:
                txn_kind = "merchant_qr"
                payee = rng.choice(merchants)

            elif r_kind < p2p_ratio + merchant_ratio + bill_ratio and businesses:
                txn_kind = "bill_pay"
                payee = rng.choice(businesses)

            else:
                txn_kind = "collect_request"
                eligible_payees = [a for a in individuals if a.actor_id != payer.actor_id]
                payee = rng.choice(eligible_payees) if eligible_payees else payer

            if payer.actor_id == payee.actor_id:
                continue

            amount = sample_amount(rng, median_amt, sigma_amt, currency="INR", cap=cap_amt)
            dev, ip = get_actor_telemetry(payer, ctx.factory)

            # Eagerly plan lifecycle outcome
            r_outcome = rng.random()
            stages: list[StagePlan] = []
            minute_offset = rng.uniform(0.0, 50.0)
            t_event = tick_start + timedelta(minutes=minute_offset)

            # Quirk flags
            use_naive_tz = rng.bernoulli(ctx.config.feed.naive_timestamp_rate)
            malform_mutation: str | None = None
            if ctx.config.feed.malformed_rate > 0.0 and rng.bernoulli(
                ctx.config.feed.malformed_rate
            ):
                malform_mutation = rng.choice(
                    ["drop_txn_id", "float_amount", "bad_currency", "bad_status"]
                )

            if r_outcome < float(calib.get("upi.success_rate", 0.94)):
                # Immediate SUCCESS
                stage = StagePlan(
                    name="completed",
                    event_time=t_event,
                    outcome_data={
                        "status": "SUCCESS",
                        "response_code": "00",
                        "use_naive_timestamp": use_naive_tz,
                        "malform_mutation": malform_mutation,
                        "expected_error": malform_mutation is not None,
                    },
                )
                stages.append(stage)

            elif r_outcome < float(calib.get("upi.success_rate", 0.94)) + float(
                calib.get("upi.pending_success_rate", 0.03)
            ):
                # PENDING then SUCCESS
                s1 = StagePlan(
                    name="pending",
                    event_time=t_event,
                    outcome_data={
                        "status": "PENDING",
                        "use_naive_timestamp": use_naive_tz,
                        "malform_mutation": malform_mutation,
                        "expected_error": malform_mutation is not None,
                    },
                )
                t_comp = t_event + timedelta(seconds=rng.uniform(30.0, 180.0))
                s2 = StagePlan(
                    name="completed",
                    event_time=t_comp,
                    outcome_data={
                        "status": "SUCCESS",
                        "response_code": "00",
                        "completed_at": t_comp,
                        "use_naive_timestamp": use_naive_tz,
                    },
                )
                stages.extend([s1, s2])

            elif r_outcome < 0.99:
                # FAILURE
                resp_code = rng.choice(["U30", "ZM", "U16", "CUSTOM_999"])
                stage = StagePlan(
                    name="failed",
                    event_time=t_event,
                    outcome_data={
                        "status": "FAILURE",
                        "response_code": resp_code,
                        "use_naive_timestamp": use_naive_tz,
                    },
                )
                stages.append(stage)

            elif r_outcome < 0.995:
                # DEEMED
                stage = StagePlan(
                    name="deemed",
                    event_time=t_event,
                    outcome_data={"status": "DEEMED", "use_naive_timestamp": use_naive_tz},
                )
                stages.append(stage)

            else:
                # REVERSED follow-up
                s1 = StagePlan(
                    name="completed",
                    event_time=t_event,
                    outcome_data={
                        "status": "SUCCESS",
                        "response_code": "00",
                        "use_naive_timestamp": use_naive_tz,
                    },
                )
                s2 = StagePlan(
                    name="reversed",
                    event_time=t_event + timedelta(hours=rng.uniform(2.0, 12.0)),
                    outcome_data={"status": "REVERSED", "use_naive_timestamp": use_naive_tz},
                )
                stages.extend([s1, s2])

            payer_ident = payer.upi_identities
            payee_ident = payee.upi_identities

            attrs: dict[str, Any] = {
                "txn_type": "COLLECT" if txn_kind == "collect_request" else "PAY",
                "collect_requested_at": t_event if txn_kind == "collect_request" else None,
                "payer_name": payer.name,
                "payer_vpa": payer_ident.get("vpa"),
                "payer_account": payer_ident.get("account_number"),
                "payer_ifsc": payer_ident.get("ifsc"),
                "payer_phone": payer_ident.get("phone"),
                "payer_tz": payer.timezone,
                "payee_name": payee.name,
                "payee_vpa": payee_ident.get("vpa"),
                "payee_account": payee_ident.get("account_number"),
                "payee_ifsc": payee_ident.get("ifsc"),
                "payee_phone": payee_ident.get("phone"),
                "payee_mcc": payee.mcc if txn_kind == "merchant_qr" else None,
                "rrn": f"{rng.randint(100000000000, 999999999999)}",
                "channel": rng.choice(["mobile_app", "web", "pos_qr", "ussd"]),
                "remarks": f"Payment {txn_kind}",
                "device_id": dev,
                "ip_address": ip,
            }

            plan = TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="upi",
                kind=txn_kind,
                sender_actor_id=payer.actor_id,
                receiver_actor_id=payee.actor_id,
                amount=amount,
                currency="INR",
                stages=stages,
                attrs=attrs,
                truth_seed={"scenario_id": None, "is_fraud": False, "hard_negative": False},
            )
            plans.append(plan)

        return plans
