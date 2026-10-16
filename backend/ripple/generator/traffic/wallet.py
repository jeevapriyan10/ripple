"""Background traffic planner for Closed-Loop Wallet payments."""

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


class WalletTrafficPlanner(SourcePlanner):
    """Generates ordinary, non-fraudulent closed-loop wallet payment volume."""

    source_key: str = "bg_wallet"

    def __init__(self) -> None:
        self._contact_book: ContactBook | None = None
        self._plan_seq = 0

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        if "wallet" not in ctx.config.networks:
            return []

        if self._contact_book is None or self._contact_book.world is not ctx.world:
            self._contact_book = ContactBook(ctx.factory, ctx.world)

        wallet_actors = ctx.world.actors_for_network("wallet")
        if not wallet_actors:
            return []

        calib = ctx.calibration
        rng = ctx.factory.stream("traffic", "wallet", str(int(tick_start.timestamp())))
        base_rate = float(calib.get("wallet.base_rate_per_actor_hour", 0.03))

        initiators = sample_tick_actors(
            eligible_actors=wallet_actors,
            tick_start=tick_start,
            base_rate_per_hour=base_rate,
            rng=rng,
            hourly_curve=calib.get("diurnal.hourly_curve"),
            weekday_factors=calib.get("diurnal.weekday_factors"),
        )

        merchants = [a for a in wallet_actors if a.kind == "merchant"]
        individuals = [a for a in wallet_actors if a.kind == "individual"]

        plans: list[TxnPlan] = []
        p2p_ratio = float(calib.get("wallet.p2p_ratio", 0.50))
        merchant_ratio = float(calib.get("wallet.merchant_ratio", 0.30))
        funding_ratio = float(calib.get("wallet.funding_ratio", 0.12))

        median_amt = float(calib.get("wallet.amount_median", 35.0))
        sigma_amt = float(calib.get("wallet.amount_sigma", 1.1))
        cap_amt = float(calib.get("wallet.cap_amount", 10000.0))

        plan_idx = 0
        for actor in initiators:
            plan_idx += 1
            plan_id = f"wal_bg_{int(tick_start.timestamp())}_{plan_idx:04d}"
            r_kind = rng.random()

            # Assign currency based on actor country (default USD if unknown)
            currency = "USD"
            if actor.country == "IN":
                currency = "INR"
            elif actor.country in {"DE", "FR", "NL", "ES", "IT"}:
                currency = "EUR"
            elif actor.country == "GB":
                currency = "GBP"

            actor_wal = actor.network_identities.get("wallet", {})
            actor_acct = actor_wal.get("account_id", f"wal_{actor.actor_id.lower()}")
            actor_instruments = actor_wal.get("instruments", [f"ins_{actor.actor_id.lower()}_01"])
            primary_instr = (
                rng.choice(actor_instruments)
                if actor_instruments
                else f"ins_{actor.actor_id.lower()}_01"
            )

            minute_offset = rng.uniform(0.0, 50.0)
            t_created = tick_start + timedelta(minutes=minute_offset)
            dev, ip = get_actor_telemetry(actor, ctx.factory)

            # Quirk handling
            is_malformed = ctx.config.feed.malformed_rate > 0.0 and rng.bernoulli(
                ctx.config.feed.malformed_rate
            )
            is_naive_ts = ctx.config.feed.naive_timestamp_rate > 0.0 and rng.bernoulli(
                ctx.config.feed.naive_timestamp_rate
            )

            # --- 1. P2P (Send or Request) ---
            if r_kind < p2p_ratio and individuals:
                flow = "request" if rng.bernoulli(0.20) else "send"
                contacts = self._contact_book.get_contacts(actor, individuals, k=4)
                if contacts and rng.bernoulli(0.65):
                    counterparty = rng.choice(contacts)
                else:
                    eligible = [a for a in individuals if a.actor_id != actor.actor_id]
                    counterparty = rng.choice(eligible) if eligible else actor

                if counterparty.actor_id == actor.actor_id:
                    continue

                counter_wal = counterparty.network_identities.get("wallet", {})
                counter_acct = counter_wal.get("account_id", f"wal_{counterparty.actor_id.lower()}")

                amount = sample_amount(rng, median_amt, sigma_amt, currency=currency, cap=cap_amt)
                sender_actor_id = actor.actor_id if flow == "send" else counterparty.actor_id
                receiver_actor_id = counterparty.actor_id if flow == "send" else actor.actor_id

                sender_acct = actor_acct if flow == "send" else counter_acct
                receiver_acct = counter_acct if flow == "send" else actor_acct
                sender_email = (
                    actor_wal.get("email") if flow == "send" else counter_wal.get("email")
                )
                sender_phone = (
                    actor_wal.get("phone") if flow == "send" else counter_wal.get("phone")
                )
                receiver_email = (
                    counter_wal.get("email") if flow == "send" else actor_wal.get("email")
                )
                receiver_phone = (
                    counter_wal.get("phone") if flow == "send" else actor_wal.get("phone")
                )

                stages: list[StagePlan] = []
                odata: dict[str, Any] = {
                    "status": "COMPLETED",
                    "device_id": dev,
                    "ip_address": ip,
                }
                if is_malformed:
                    odata["malformed_mutation"] = "missing_status"
                if is_naive_ts:
                    odata["naive_timestamp"] = True

                if flow == "request":
                    # Requested stage then approved stage
                    t_approved = t_created + timedelta(minutes=rng.uniform(5.0, 45.0))
                    odata["approved_at"] = t_approved
                    stages.append(StagePlan(name="p2p", event_time=t_approved, outcome_data=odata))
                else:
                    stages.append(StagePlan(name="p2p", event_time=t_created, outcome_data=odata))

                plan = TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="wallet",
                    kind=f"p2p_{flow}",
                    sender_actor_id=sender_actor_id,
                    receiver_actor_id=receiver_actor_id,
                    amount=amount,
                    currency=currency,
                    stages=stages,
                    attrs={
                        "created_at": t_created,
                        "flow": flow,
                        "sender_wallet_id": sender_acct,
                        "sender_email": sender_email,
                        "sender_phone": sender_phone,
                        "receiver_wallet_id": receiver_acct,
                        "receiver_email": receiver_email,
                        "receiver_phone": receiver_phone,
                        "payment_type": "friends_family",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "expected_error": is_malformed,
                    },
                )
                plans.append(plan)

            # --- 2. Merchant Payment ---
            elif r_kind < p2p_ratio + merchant_ratio and merchants:
                merchant = rng.choice(merchants)
                merch_wal = merchant.network_identities.get("wallet", {})
                merch_acct = merch_wal.get("account_id", f"wal_{merchant.actor_id.lower()}")
                mcc = merchant.mcc or "5411"

                amount = sample_amount(
                    rng, median_amt * 1.5, sigma_amt, currency=currency, cap=cap_amt
                )
                stages = []
                odata = {
                    "status": "COMPLETED",
                    "device_id": dev,
                    "ip_address": ip,
                }
                if is_malformed:
                    odata["malformed_mutation"] = "missing_status"
                if is_naive_ts:
                    odata["naive_timestamp"] = True

                stages.append(
                    StagePlan(name="merchant_payment", event_time=t_created, outcome_data=odata)
                )

                # Rare legitimate refund (5%) days later
                if rng.bernoulli(0.05):
                    t_refund = t_created + timedelta(days=rng.uniform(2.0, 7.0))
                    stages.append(
                        StagePlan(
                            name="refund",
                            event_time=t_refund,
                            outcome_data={"status": "COMPLETED"},
                        )
                    )

                # Rare dispute (1%)
                elif rng.bernoulli(0.01):
                    t_dispute = t_created + timedelta(days=rng.uniform(5.0, 14.0))
                    stages.append(
                        StagePlan(
                            name="dispute",
                            event_time=t_dispute,
                            outcome_data={
                                "case_id": f"case_{plan_id}",
                                "dispute_status": "opened",
                                "dispute_reason": "item_not_received",
                            },
                        )
                    )

                plan = TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="wallet",
                    kind="merchant_payment",
                    sender_actor_id=actor.actor_id,
                    receiver_actor_id=merchant.actor_id,
                    amount=amount,
                    currency=currency,
                    stages=stages,
                    attrs={
                        "created_at": t_created,
                        "sender_wallet_id": actor_acct,
                        "merchant_wallet_id": merch_acct,
                        "merchant_id": f"mid_{merchant.actor_id.lower()}",
                        "mcc": mcc,
                        "buyer_protection": True,
                        "reversal_window_days": 180,
                    },
                    truth_seed={
                        "is_fraud": False,
                        "expected_error": is_malformed,
                    },
                )
                plans.append(plan)

            # --- 3. Balance Funding (Cash-In) ---
            elif r_kind < p2p_ratio + merchant_ratio + funding_ratio:
                amount = sample_amount(
                    rng, median_amt * 2.0, sigma_amt, currency=currency, cap=cap_amt
                )
                stages = []
                odata = {
                    "status": "COMPLETED",
                    "device_id": dev,
                    "ip_address": ip,
                }
                if is_malformed:
                    odata["malformed_mutation"] = "missing_status"
                if is_naive_ts:
                    odata["naive_timestamp"] = True

                stages.append(StagePlan(name="funding", event_time=t_created, outcome_data=odata))

                # Rare legitimate funding reversal (funding_failed) 2-4 days later (1%)
                if rng.bernoulli(0.01):
                    t_rev = t_created + timedelta(days=rng.uniform(2.0, 4.0))
                    stages.append(
                        StagePlan(
                            name="reversal",
                            event_time=t_rev,
                            outcome_data={"reversal_reason": "funding_failed"},
                        )
                    )

                plan = TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="wallet",
                    kind="funding",
                    sender_actor_id=actor.actor_id,
                    receiver_actor_id=actor.actor_id,
                    amount=amount,
                    currency=currency,
                    stages=stages,
                    attrs={
                        "created_at": t_created,
                        "wallet_account_id": actor_acct,
                        "instrument_id": primary_instr,
                        "instrument_type": "bank",
                        "provisional_credit": True,
                        "original_event_type": "funding",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "expected_error": is_malformed,
                    },
                )
                plans.append(plan)

            # --- 4. Balance Withdrawal (Cash-Out) ---
            else:
                amount = sample_amount(
                    rng, median_amt * 1.8, sigma_amt, currency=currency, cap=cap_amt
                )
                odata = {
                    "status": "COMPLETED",
                    "device_id": dev,
                    "ip_address": ip,
                }
                if is_malformed:
                    odata["malformed_mutation"] = "missing_status"
                if is_naive_ts:
                    odata["naive_timestamp"] = True

                stages = [StagePlan(name="withdrawal", event_time=t_created, outcome_data=odata)]

                plan = TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="wallet",
                    kind="withdrawal",
                    sender_actor_id=actor.actor_id,
                    receiver_actor_id=actor.actor_id,
                    amount=amount,
                    currency=currency,
                    stages=stages,
                    attrs={
                        "created_at": t_created,
                        "wallet_account_id": actor_acct,
                        "instrument_id": primary_instr,
                        "instrument_type": "bank",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "expected_error": is_malformed,
                    },
                )
                plans.append(plan)

        return plans
