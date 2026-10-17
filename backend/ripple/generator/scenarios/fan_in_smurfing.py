"""Fan-In Smurfing scenario typology adhering to Rules R1-R6.

Real-World Pattern:
Smurfing and structuring where numerous distinct senders transfer amounts calibrated
just beneath regulatory reporting or automated AML threshold triggers into a single
consolidation account. The collector account then transfers the aggregated balance
out in 1-2 large transfers.

Expected Graph Signature:
High in-degree fan-in star to a collector node, followed by low out-degree consolidation.

Hard Negatives Resembled:
- merchant_flash_sale (e-commerce sales burst)
- split_bill (social reimbursement)
- freelancer_fan_in (independent contractor payments)
"""

from datetime import timedelta
from decimal import Decimal

from ripple.domain.party import MuleRole
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.scenarios.base import (
    Scenario,
    ScenarioContext,
    ScenarioPlan,
    register_scenario,
)


@register_scenario
class FanInSmurfingScenario(Scenario):
    """Fan-In structuring / smurfing scenario under AML detection thresholds."""

    scenario_type: str = "fan_in_smurfing"
    pattern: str = "structuring_smurfing"
    required_networks: list[str] = ["upi", "wallet"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        # Choose network variant: UPI or Wallet
        network = "upi" if rng.bernoulli(0.50) else "wallet"
        actors = world.actors_for_network(network)
        individuals = [a for a in actors if a.kind == "individual"]
        mule_pool = [a for a in world.mule_pool if a.has_network(network)]
        if not mule_pool:
            mule_pool = individuals

        min_senders = int(calib.get("scenarios.fan_in_smurfing.senders_min", 6))
        max_senders = int(calib.get("scenarios.fan_in_smurfing.senders_max", 14))

        if network == "upi":
            threshold = Decimal(
                str(calib.get("scenarios.fan_in_smurfing.structuring_threshold_inr", 10000.0))
            )
            currency = "INR"
        else:
            threshold = Decimal(
                str(calib.get("scenarios.fan_in_smurfing.structuring_threshold_usd", 10000.0))
            )
            currency = "USD"

        collector = rng.choice(mule_pool)
        available_senders = [a for a in individuals if a.actor_id != collector.actor_id]
        num_senders = rng.randint(min_senders, max_senders)
        senders = rng.sample_without_replacement(
            available_senders, min(num_senders, len(available_senders))
        )

        outbound_recipients = [
            a
            for a in mule_pool
            if a.actor_id not in {s.actor_id for s in senders} and a.actor_id != collector.actor_id
        ]
        if not outbound_recipients:
            outbound_recipients = [a for a in individuals if a.actor_id != collector.actor_id]
        outbound_dest = rng.choice(outbound_recipients)

        roles: dict[str, MuleRole | None] = {}
        for s in senders:
            roles[s.actor_id] = MuleRole.VICTIM
        roles[collector.actor_id] = MuleRole.FIRST_HOP
        roles[outbound_dest.actor_id] = MuleRole.CASH_OUT

        txn_plans: list[TxnPlan] = []
        total_inflow = Decimal("0.00")
        curr_time = start_time

        # Fan-in credits
        for i, sender in enumerate(senders):
            t_ev = curr_time + timedelta(minutes=i * rng.uniform(2.0, 15.0))
            # Amount strictly beneath structuring threshold (e.g. 9,000 - 9,850)
            under_margin = Decimal(str(rng.randint(150, 950)))
            amt = threshold - under_margin
            total_inflow += amt
            plan_id = f"smurf_in_{ctx.instance_id}_{i:02d}"

            if network == "upi":
                stages = [
                    StagePlan(
                        name="PAY",
                        event_time=t_ev,
                        outcome_data={"status": "SUCCESS", "response_code": "00"},
                    )
                ]
                attrs = {
                    "txn_type": "PAY",
                    "payer_vpa": sender.upi_identities.get("vpa", "smurf@rplbank1"),
                    "payee_vpa": collector.upi_identities.get("vpa", "coll@rplbank1"),
                    "channel": "mobile_app",
                }
            else:
                stages = [
                    StagePlan(
                        name="p2p",
                        event_time=t_ev,
                        outcome_data={"status": "COMPLETED"},
                    )
                ]
                attrs = {
                    "flow": "send",
                    "sender_wallet_id": f"wal_{sender.actor_id.lower()}",
                    "receiver_wallet_id": f"wal_{collector.actor_id.lower()}",
                }

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network=network,
                    kind="p2p_pay" if network == "upi" else "p2p_send",
                    sender_actor_id=sender.actor_id,
                    receiver_actor_id=collector.actor_id,
                    amount=amt,
                    currency=currency,
                    stages=stages,
                    attrs=attrs,
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        "sender_role": MuleRole.VICTIM.value,
                        "receiver_role": MuleRole.FIRST_HOP.value,
                    },
                )
            )

        # Outbound consolidation transfer
        t_out = curr_time + timedelta(minutes=len(senders) * 15.0 + rng.uniform(20.0, 90.0))
        outbound_plan_id = f"smurf_out_{ctx.instance_id}_01"

        if network == "upi":
            out_stages = [
                StagePlan(
                    name="PAY",
                    event_time=t_out,
                    outcome_data={"status": "SUCCESS", "response_code": "00"},
                )
            ]
            out_attrs = {
                "txn_type": "PAY",
                "payer_vpa": collector.upi_identities.get("vpa", "coll@rplbank1"),
                "payee_vpa": outbound_dest.upi_identities.get("vpa", "cashout@rplbank1"),
                "channel": "internet_banking",
            }
        else:
            out_stages = [
                StagePlan(
                    name="p2p",
                    event_time=t_out,
                    outcome_data={"status": "COMPLETED"},
                )
            ]
            out_attrs = {
                "flow": "send",
                "sender_wallet_id": f"wal_{collector.actor_id.lower()}",
                "receiver_wallet_id": f"wal_{outbound_dest.actor_id.lower()}",
            }

        txn_plans.append(
            TxnPlan(
                plan_id=outbound_plan_id,
                source_key="scenario_engine",
                network=network,
                kind="p2p_pay" if network == "upi" else "p2p_send",
                sender_actor_id=collector.actor_id,
                receiver_actor_id=outbound_dest.actor_id,
                amount=total_inflow,
                currency=currency,
                stages=out_stages,
                attrs=out_attrs,
                truth_seed={
                    "scenario_id": ctx.instance_id,
                    "pattern": self.pattern,
                    "regime": ctx.regime,
                    "is_fraud": True,
                    "sender_role": MuleRole.FIRST_HOP.value,
                    "receiver_role": MuleRole.CASH_OUT.value,
                },
            )
        )

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "senders_count": len(senders),
                "threshold": threshold,
                "total_inflow": total_inflow,
                "network": network,
            },
        )
