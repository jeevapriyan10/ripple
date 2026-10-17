"""SWIFT Structuring and Layering scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A syndicated network of corporate shell entities registered across 3-5 international
jurisdictions routes high-value wires through correspondent banking networks. Transfers are
structured just below mandatory regulatory reporting thresholds (e.g. $49,500 vs $50,000 CTR/PTR),
followed by rapid onward transfers within 1-2 business days, often with commercial purpose codes
(e.g. SUPP, GDDS) inconsistent with the entities' registered profiles.

Expected Graph Signature:
Multi-hop cross-border correspondent path with structured amounts and rapid
inter-jurisdictional dwell.

Hard Negatives Resembled:
- psp_settlement_forwarding (commercial settlement forwarding)
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
class SwiftStructuringLayeringScenario(Scenario):
    """Multi-jurisdictional wire structuring and correspondent banking layering."""

    scenario_type: str = "swift_structuring_layering"
    pattern: str = "swift_structuring"
    required_networks: list[str] = ["swift"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        swift_actors = world.actors_for_network("swift")
        businesses = [a for a in swift_actors if a.kind in {"business", "merchant", "psp"}]
        if len(businesses) < 3:
            return ScenarioPlan(txn_plans=[], roles={}, summary={"skipped": True})

        min_jur = int(calib.get("scenarios.swift_structuring_layering.jurisdiction_count_min", 3))
        max_jur = int(calib.get("scenarios.swift_structuring_layering.jurisdiction_count_max", 5))
        threshold = Decimal(
            str(
                calib.get("scenarios.swift_structuring_layering.structuring_threshold_usd", 50000.0)
            )
        )
        num_jur = rng.randint(min_jur, min(max_jur, len(businesses)))

        selected_shells = rng.sample_without_replacement(businesses, num_jur)
        originator = selected_shells[0]
        intermediate_shells = selected_shells[1:-1]
        terminal_entity = selected_shells[-1]

        roles: dict[str, MuleRole | None] = {
            originator.actor_id: MuleRole.FIRST_HOP,
            terminal_entity.actor_id: MuleRole.CASH_OUT,
        }
        for sh in intermediate_shells:
            roles[sh.actor_id] = MuleRole.LAYERING

        txn_plans: list[TxnPlan] = []
        curr_time = start_time

        # Structured amount just beneath threshold (e.g. 48,500 - 49,850)
        structured_amount = threshold - Decimal(str(rng.randint(250, 1500))).quantize(
            Decimal("1.00")
        )

        for h in range(len(selected_shells) - 1):
            snd = selected_shells[h]
            rcv = selected_shells[h + 1]

            t_inst = curr_time + timedelta(days=rng.uniform(1.0, 2.0))
            t_accc = t_inst + timedelta(hours=rng.uniform(4.0, 18.0))
            curr_time = t_inst

            plan_id = f"sw_str_{ctx.instance_id}_{h:02d}"

            debtor_acc = snd.swift_identities.get("iban") or snd.swift_identities.get(
                "account_number", f"acc_{snd.actor_id}"
            )
            creditor_acc = rcv.swift_identities.get("iban") or rcv.swift_identities.get(
                "account_number", f"acc_{rcv.actor_id}"
            )
            debtor_bic = snd.swift_identities.get("bic") or "ZRPAUS33XXX"
            creditor_bic = rcv.swift_identities.get("bic") or "ZRPBUS33XXX"

            # pyrefly: ignore[missing-attribute]
            snd_role = roles[snd.actor_id].value if roles.get(snd.actor_id) else None
            # pyrefly: ignore[missing-attribute]
            rcv_role = roles[rcv.actor_id].value if roles.get(rcv.actor_id) else None
            uetr_ts = int(t_inst.timestamp()) % 100000000000

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="swift",
                    kind="supplier_payment",
                    sender_actor_id=snd.actor_id,
                    receiver_actor_id=rcv.actor_id,
                    amount=structured_amount,
                    currency="USD",
                    stages=[
                        StagePlan(name="instruction", event_time=t_inst, outcome_data={}),
                        StagePlan(name="ACCC", event_time=t_accc, outcome_data={}),
                    ],
                    attrs={
                        "uetr": f"c9b8a7f6-5432-4100-8000-{uetr_ts:012d}",
                        "debtor_agent_bic": debtor_bic,
                        "creditor_agent_bic": creditor_bic,
                        "sender_bic": debtor_bic,
                        "receiver_bic": creditor_bic,
                        "debtor_account": debtor_acc,
                        "creditor_account": creditor_acc,
                        "debtor_country": snd.country,
                        "creditor_country": rcv.country,
                        "purpose_code": "SUPP",
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

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "jurisdiction_count": num_jur,
                "structured_amount": structured_amount,
                "threshold": threshold,
            },
        )
