"""UPI Mule Chain scenario typology adhering to Rules R1-R6.

Real-World Pattern:
Authorized Push Payment (APP) scams where multiple retail victims send funds to a
primary money mule (first-hop). The mule rapidly forwards 90-98% of the funds across
2-5 intermediary layering mules in seconds to minutes, terminating in a cash-out
actor who disperses funds to colluding QR merchants or cash withdrawal points.

Expected Graph Signature:
Fan-in tree (victims -> first_hop) -> linear or split layering chain -> fan-out to merchants.

Hard Negatives Resembled:
- payroll_fanout (business paying employees rapidly)
- split_bill (friends reimbursing one organizer)
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
class UpiMuleChainScenario(Scenario):
    """UPI Mule Chain scam scenario with rapid multi-hop layering and cash-out."""

    scenario_type: str = "upi_mule_chain"
    pattern: str = "mule_layering"
    required_networks: list[str] = ["upi"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        upi_actors = world.actors_for_network("upi")
        individuals = [a for a in upi_actors if a.kind == "individual"]
        merchants = [a for a in upi_actors if a.kind == "merchant"]
        mule_pool = [a for a in world.mule_pool if a.has_network("upi")]
        if not mule_pool:
            mule_pool = individuals

        # Check regime
        is_v2_regime = ctx.regime == "v2_mule_template"

        # Parameters from calibration
        min_hops = int(calib.get("scenarios.upi_mule_chain.hop_count_min", 2))
        max_hops = int(calib.get("scenarios.upi_mule_chain.hop_count_max", 5))
        fwd_min = float(calib.get("scenarios.upi_mule_chain.forward_fraction_min", 0.90))
        fwd_max = float(calib.get("scenarios.upi_mule_chain.forward_fraction_max", 0.98))
        v_min = int(calib.get("scenarios.upi_mule_chain.victim_count_min", 2))
        v_max = int(calib.get("scenarios.upi_mule_chain.victim_count_max", 4))

        if ctx.difficulty == "hard":
            fwd_min = max(0.80, fwd_min - 0.08)
            min_delay = 300.0  # longer delays
            max_delay = 1800.0
        elif ctx.difficulty == "medium":
            min_delay = 15.0
            max_delay = 180.0
        else:
            min_delay = 5.0
            max_delay = 60.0

        num_victims = rng.randint(v_min, v_max)
        victims = rng.sample_without_replacement(individuals, min(num_victims, len(individuals)))

        # Assign First Hop Mule
        available_mules = [m for m in mule_pool if m.actor_id not in {v.actor_id for v in victims}]
        if not available_mules:
            available_mules = individuals
        first_hop = rng.choice(available_mules)

        # Select layering chain mules
        num_hops = rng.randint(min_hops, max_hops)
        layering_candidates = [m for m in available_mules if m.actor_id != first_hop.actor_id]
        layering_mules = rng.sample_without_replacement(
            layering_candidates, min(num_hops, len(layering_candidates))
        )
        if not layering_mules:
            layering_mules = [first_hop]

        cash_out = layering_mules[-1] if len(layering_mules) > 1 else first_hop
        intermediate_mules = layering_mules[:-1] if len(layering_mules) > 1 else []

        roles: dict[str, MuleRole | None] = {}
        for v in victims:
            roles[v.actor_id] = MuleRole.VICTIM
        roles[first_hop.actor_id] = MuleRole.FIRST_HOP
        for m in intermediate_mules:
            roles[m.actor_id] = MuleRole.LAYERING
        roles[cash_out.actor_id] = MuleRole.CASH_OUT

        txn_plans: list[TxnPlan] = []
        curr_time = start_time
        total_inflow = Decimal("0.00")

        # 1. Victim fan-in stage
        for i, victim in enumerate(victims):
            t_v = curr_time + timedelta(seconds=i * rng.uniform(10.0, 45.0))
            amt = Decimal(str(rng.randint(25000, 75000)))
            total_inflow += amt
            plan_id = f"upi_mc_v_{ctx.instance_id}_{i:02d}"

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="upi",
                    kind="p2p_pay",
                    sender_actor_id=victim.actor_id,
                    receiver_actor_id=first_hop.actor_id,
                    amount=amt,
                    currency="INR",
                    stages=[
                        StagePlan(
                            name="PAY",
                            event_time=t_v,
                            outcome_data={"status": "SUCCESS", "response_code": "00"},
                        )
                    ],
                    attrs={
                        "txn_type": "PAY",
                        "payer_vpa": victim.upi_identities.get("vpa", "vic@rplbank1"),
                        "payee_vpa": first_hop.upi_identities.get("vpa", "first@rplbank1"),
                        "channel": "mobile_app",
                    },
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

        curr_time = curr_time + timedelta(
            seconds=len(victims) * 45.0 + rng.uniform(min_delay, max_delay)
        )

        # 2. Layering Cascade
        prev_node = first_hop
        current_amount = total_inflow

        if is_v2_regime:
            # v2_mule_template: parallel fan-out star with smaller transfers under U16 cap
            cap_threshold = Decimal("10000.00")
            for j, layer_node in enumerate(intermediate_mules + [cash_out]):
                t_hop = curr_time + timedelta(seconds=j * rng.uniform(5.0, 20.0))
                chunk_amt = min(
                    cap_threshold - Decimal("500.00"),
                    current_amount / Decimal(max(1, len(intermediate_mules) + 1)),
                )
                chunk_amt = chunk_amt.quantize(Decimal("1.00"))
                plan_id = f"upi_mc_star_{ctx.instance_id}_{j:02d}"
                txn_plans.append(
                    TxnPlan(
                        plan_id=plan_id,
                        source_key="scenario_engine",
                        network="upi",
                        kind="p2p_pay",
                        sender_actor_id=first_hop.actor_id,
                        receiver_actor_id=layer_node.actor_id,
                        amount=chunk_amt,
                        currency="INR",
                        stages=[
                            StagePlan(
                                name="PAY",
                                event_time=t_hop,
                                outcome_data={"status": "SUCCESS", "response_code": "00"},
                            )
                        ],
                        attrs={
                            "txn_type": "PAY",
                            "payer_vpa": first_hop.upi_identities.get("vpa", "first@rplbank1"),
                            "payee_vpa": layer_node.upi_identities.get("vpa", "layer@rplbank1"),
                            "channel": "mobile_app",
                        },
                        truth_seed={
                            "scenario_id": ctx.instance_id,
                            "pattern": self.pattern,
                            "regime": ctx.regime,
                            "is_fraud": True,
                            "sender_role": MuleRole.FIRST_HOP.value,
                            "receiver_role": MuleRole.LAYERING.value
                            if layer_node != cash_out
                            else MuleRole.CASH_OUT.value,
                        },
                    )
                )
        else:
            # Baseline linear chain
            for j, layer_node in enumerate(intermediate_mules + [cash_out]):
                if layer_node.actor_id == prev_node.actor_id:
                    continue
                fwd_fraction = Decimal(str(rng.uniform(fwd_min, fwd_max)))
                fwd_amount = (current_amount * fwd_fraction).quantize(Decimal("1.00"))
                current_amount = fwd_amount

                t_hop = curr_time + timedelta(seconds=rng.uniform(min_delay, max_delay))
                curr_time = t_hop
                plan_id = f"upi_mc_hop_{ctx.instance_id}_{j:02d}"

                snd_role = (
                    MuleRole.FIRST_HOP.value if prev_node == first_hop else MuleRole.LAYERING.value
                )
                rcv_role = (
                    MuleRole.CASH_OUT.value if layer_node == cash_out else MuleRole.LAYERING.value
                )

                txn_plans.append(
                    TxnPlan(
                        plan_id=plan_id,
                        source_key="scenario_engine",
                        network="upi",
                        kind="p2p_pay",
                        sender_actor_id=prev_node.actor_id,
                        receiver_actor_id=layer_node.actor_id,
                        amount=fwd_amount,
                        currency="INR",
                        stages=[
                            StagePlan(
                                name="PAY",
                                event_time=t_hop,
                                outcome_data={"status": "SUCCESS", "response_code": "00"},
                            )
                        ],
                        attrs={
                            "txn_type": "PAY",
                            "payer_vpa": prev_node.upi_identities.get("vpa", "mule_p@rplbank1"),
                            "payee_vpa": layer_node.upi_identities.get("vpa", "mule_c@rplbank1"),
                            "channel": "mobile_app",
                        },
                        truth_seed={
                            "scenario_id": ctx.instance_id,
                            "pattern": self.pattern,
                            "regime": ctx.regime,
                            "is_fraud": True,
                            "sender_role": snd_role,
                            "receiver_role": rcv_role,
                        },
                    )
                )
                prev_node = layer_node

        # 3. Cash-out stage to colluding merchants
        target_merchants = (
            rng.sample_without_replacement(merchants, min(2, len(merchants))) if merchants else []
        )
        for k, merch in enumerate(target_merchants):
            t_co = curr_time + timedelta(minutes=rng.uniform(5.0, 30.0))
            co_amt = (current_amount / Decimal(max(1, len(target_merchants)))).quantize(
                Decimal("1.00")
            )
            plan_id = f"upi_mc_co_{ctx.instance_id}_{k:02d}"
            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="upi",
                    kind="merchant_qr",
                    sender_actor_id=cash_out.actor_id,
                    receiver_actor_id=merch.actor_id,
                    amount=co_amt,
                    currency="INR",
                    stages=[
                        StagePlan(
                            name="PAY",
                            event_time=t_co,
                            outcome_data={"status": "SUCCESS", "response_code": "00"},
                        )
                    ],
                    attrs={
                        "txn_type": "PAY",
                        "payer_vpa": cash_out.upi_identities.get("vpa", "co@rplbank1"),
                        "payee_vpa": merch.upi_identities.get("vpa", "merch@rplbank1"),
                        "mcc": merch.mcc or "5311",
                        "channel": "mobile_app",
                    },
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        "sender_role": MuleRole.CASH_OUT.value,
                        "receiver_role": None,
                    },
                )
            )

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "hop_count": num_hops,
                "victim_count": len(victims),
                "total_inflow": total_inflow,
                "forward_fraction": (current_amount / total_inflow)
                if total_inflow > 0
                else Decimal(0),
            },
        )
