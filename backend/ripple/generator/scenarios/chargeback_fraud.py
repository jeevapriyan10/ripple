"""Chargeback Fraud Merchant scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A rogue or bust-out merchant processes unauthorized card-not-present sales across dozens
of compromised credit cards over a period of weeks. Settlement funds are captured and
withdrawn by the colluding merchant. 30-60 days later, as billing statements arrive,
genuine cardholders dispute the charges, resulting in a coordinated wave of chargebacks
with reason code 4837 (fraud / cardholder not recognizing transaction).

Expected Graph Signature:
Dense cluster of card-not-present authorizations into one merchant, progressing to settlement,
followed 30-60 days later by a wave of chargeback dispute events.

Hard Negatives Resembled:
- high_chargeback_merchant (legitimate merchant with high disputes)
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
class ChargebackFraudMerchantScenario(Scenario):
    """Bust-out merchant chargeback wave scenario."""

    scenario_type: str = "chargeback_fraud_merchant"
    pattern: str = "merchant_chargeback_fraud"
    required_networks: list[str] = ["card"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        card_actors = world.actors_for_network("card")
        merchants = [a for a in card_actors if a.kind == "merchant"]
        individuals = [
            a for a in card_actors if a.kind == "individual" and a.card_identities.get("tokens")
        ]
        if not merchants or len(individuals) < 10:
            return ScenarioPlan(txn_plans=[], roles={}, summary={"skipped": True})

        min_sales = int(calib.get("scenarios.chargeback_fraud_merchant.sale_count_min", 8))
        max_sales = int(calib.get("scenarios.chargeback_fraud_merchant.sale_count_max", 20))
        cb_min_days = float(
            calib.get("scenarios.chargeback_fraud_merchant.chargeback_delay_days_min", 30.0)
        )
        cb_max_days = float(
            calib.get("scenarios.chargeback_fraud_merchant.chargeback_delay_days_max", 60.0)
        )

        fraud_merchant = rng.choice(merchants)
        num_sales = rng.randint(min_sales, max_sales)
        sampled_victims = rng.sample_without_replacement(
            individuals, min(num_sales, len(individuals))
        )

        roles: dict[str, MuleRole | None] = {}
        for v in sampled_victims:
            roles[v.actor_id] = MuleRole.VICTIM
        roles[fraud_merchant.actor_id] = MuleRole.CASH_OUT

        txn_plans: list[TxnPlan] = []
        curr_time = start_time

        for idx, victim in enumerate(sampled_victims):
            t_auth = curr_time + timedelta(hours=idx * rng.uniform(2.0, 12.0))
            t_clear = t_auth + timedelta(days=1)
            t_settle = t_auth + timedelta(days=2)
            t_cb = t_settle + timedelta(days=rng.uniform(cb_min_days, cb_max_days))

            token = victim.card_identities["tokens"][0]
            bin_val = victim.card_identities["bins"][0]
            last4_val = victim.card_identities["last4s"][0]
            amt = Decimal(str(rng.randint(95, 450))).quantize(Decimal("1.00"))

            plan_id = f"cd_cbf_{ctx.instance_id}_{idx:02d}"
            stages = [
                StagePlan(
                    name="authorization",
                    event_time=t_auth,
                    outcome_data={"response_code": "00", "approved": True},
                ),
                StagePlan(
                    name="clearing",
                    event_time=t_clear,
                    outcome_data={},
                ),
                StagePlan(
                    name="settlement",
                    event_time=t_settle,
                    outcome_data={},
                ),
                StagePlan(
                    name="chargeback",
                    event_time=t_cb,
                    outcome_data={"reason_code": "4837"},
                ),
            ]

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="card",
                    kind="ecommerce_purchase",
                    sender_actor_id=victim.actor_id,
                    receiver_actor_id=fraud_merchant.actor_id,
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
                        "merchant_id": f"mid_{fraud_merchant.actor_id.lower()}",
                        "merchant_mcc": "5999",
                        "acquirer_institution_id": fraud_merchant.institution_id,
                        "issuer_institution_id": victim.institution_id,
                    },
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        "sender_role": MuleRole.VICTIM.value,
                        "receiver_role": MuleRole.CASH_OUT.value,
                    },
                )
            )

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "fraud_sales_count": len(sampled_victims),
                "chargeback_delay_min_days": cb_min_days,
                "chargeback_delay_max_days": cb_max_days,
            },
        )
