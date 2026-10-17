"""Card Testing scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A criminal acquires a database of stolen payment card credentials (PAN tokens)
and runs rapid automated scripts through an online merchant gateway with small amounts
($1.00-$5.00) to ascertain which cards are active. The majority are declined (response codes
05 or 51), but successful authorizations are immediately followed by 1-3 high-value
fraudulent purchases.

Expected Graph Signature:
Dense fan-in cluster of micro-authorizations from many card tokens into one merchant,
predominantly declined, followed by larger approvals.

Hard Negatives Resembled:
- traveler_cross_border (card decline due to foreign travel risk filter)
- merchant_flash_sale (high volume burst)
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
class CardTestingScenario(Scenario):
    """Card testing / enumeration attack at online merchants."""

    scenario_type: str = "card_testing"
    pattern: str = "card_testing"
    required_networks: list[str] = ["card"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        is_v2_regime = ctx.regime == "v2_card_testing"

        card_actors = world.actors_for_network("card")
        merchants = [a for a in card_actors if a.kind == "merchant"]
        individuals = [
            a for a in card_actors if a.kind == "individual" and a.card_identities.get("tokens")
        ]
        if not merchants or len(individuals) < 10:
            return ScenarioPlan(txn_plans=[], roles={}, summary={"skipped": True})

        min_burst = int(calib.get("scenarios.card_testing.burst_count_min", 6))
        max_burst = int(calib.get("scenarios.card_testing.burst_count_max", 18))
        if is_v2_regime:
            min_burst = max(3, min_burst // 2)
            max_burst = max(5, max_burst // 2)

        target_merchant = rng.choice(merchants)
        burst_size = rng.randint(min_burst, max_burst)
        sampled_victims = rng.sample_without_replacement(
            individuals, min(burst_size, len(individuals))
        )

        num_approved = rng.randint(1, 3)
        # Ensure decline ratio is high (mostly declines)
        approved_indices = set(
            rng.sample_without_replacement(
                list(range(len(sampled_victims))), min(num_approved, len(sampled_victims))
            )
        )

        roles: dict[str, MuleRole | None] = {}
        for v in sampled_victims:
            roles[v.actor_id] = MuleRole.VICTIM
        roles[target_merchant.actor_id] = MuleRole.LAYERING

        txn_plans: list[TxnPlan] = []
        curr_time = start_time
        declined_count = 0
        approved_count = 0

        # 1. Burst of small test authorizations
        for idx, victim in enumerate(sampled_victims):
            delay_sec = rng.uniform(1.0, 5.0) if not is_v2_regime else rng.uniform(5.0, 25.0)
            t_auth = curr_time + timedelta(seconds=idx * delay_sec)
            token = victim.card_identities["tokens"][0]
            bin_val = victim.card_identities["bins"][0]
            last4_val = victim.card_identities["last4s"][0]

            is_appr = idx in approved_indices
            if is_appr:
                amt = Decimal(str(rng.randint(250, 850))).quantize(Decimal("1.00"))
                resp_code = "00"
                approved_count += 1
            else:
                amt = Decimal(str(rng.randint(1, 5))).quantize(Decimal("1.00"))
                resp_code = rng.choice(["05", "51"])
                declined_count += 1

            plan_id = f"cd_test_{ctx.instance_id}_{idx:02d}"
            stages = [
                StagePlan(
                    name="authorization",
                    event_time=t_auth,
                    outcome_data={"response_code": resp_code, "approved": is_appr},
                )
            ]
            if is_appr:
                stages.append(
                    StagePlan(
                        name="clearing",
                        event_time=t_auth + timedelta(days=1),
                        outcome_data={},
                    )
                )
                stages.append(
                    StagePlan(
                        name="settlement",
                        event_time=t_auth + timedelta(days=2),
                        outcome_data={},
                    )
                )

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="card",
                    kind="ecommerce_purchase",
                    sender_actor_id=victim.actor_id,
                    receiver_actor_id=target_merchant.actor_id,
                    amount=amt,
                    currency="USD",
                    stages=stages,
                    attrs={
                        "auth_time": t_auth,
                        "card_token": token,
                        "card_bin": bin_val,
                        "card_last4": last4_val,
                        "pos_entry_mode": "01",
                        "card_present": False,
                        "merchant_id": f"mid_{target_merchant.actor_id.lower()}",
                        "merchant_mcc": "5999",
                        "acquirer_institution_id": target_merchant.institution_id,
                        "issuer_institution_id": victim.institution_id,
                    },
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        "sender_role": MuleRole.VICTIM.value,
                        "receiver_role": MuleRole.LAYERING.value,
                    },
                )
            )

        total_tested = declined_count + approved_count
        decline_ratio = (declined_count / total_tested) if total_tested > 0 else 0.0

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "burst_size": total_tested,
                "declined_count": declined_count,
                "approved_count": approved_count,
                "decline_ratio": decline_ratio,
            },
        )
