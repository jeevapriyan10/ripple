"""Wallet Provisional Funding Abuse scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A perpetrator links an external payment instrument (stolen card or fraudulent bank account)
to a closed-loop digital wallet and initiates a funding top-up. The wallet grants
immediate provisional credit. The fraudster rapidly moves the funds via P2P transfers
or external withdrawal within minutes to hours. Days later, the original funding
instrument settlement fails (ACH return or chargeback), reversing the provisional credit
and leaving the wallet balance negative.

Expected Graph Signature:
Provisional funding node -> rapid high-velocity outbound transfer -> delayed funding reversal.

Hard Negatives Resembled:
- legitimate wallet funding followed by normal retail spend and rare bank return.
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
class WalletProvisionalFundingAbuseScenario(Scenario):
    """Provisional credit exploitation in closed-loop digital wallets."""

    scenario_type: str = "wallet_provisional_funding_abuse"
    pattern: str = "funding_abuse"
    required_networks: list[str] = ["wallet"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        actors = world.actors_for_network("wallet")
        individuals = [a for a in actors if a.kind == "individual"]
        mule_pool = [a for a in world.mule_pool if a.has_network("wallet")]
        if not mule_pool:
            mule_pool = individuals

        min_hops = int(calib.get("scenarios.wallet_provisional_funding_abuse.hops_min", 1))
        max_hops = int(calib.get("scenarios.wallet_provisional_funding_abuse.hops_max", 3))
        num_hops = rng.randint(min_hops, max_hops)

        reversal_delay_min = float(
            calib.get("scenarios.wallet_provisional_funding_abuse.reversal_delay_days_min", 2.0)
        )
        reversal_delay_max = float(
            calib.get("scenarios.wallet_provisional_funding_abuse.reversal_delay_days_max", 5.0)
        )

        # Select fraudster and downstream mules
        fraudster = rng.choice(mule_pool)
        available_cashouts = [a for a in mule_pool if a.actor_id != fraudster.actor_id]
        if not available_cashouts:
            available_cashouts = [a for a in individuals if a.actor_id != fraudster.actor_id]

        cashout_nodes = rng.sample_without_replacement(
            available_cashouts, min(num_hops, len(available_cashouts))
        )
        if not cashout_nodes:
            cashout_nodes = [fraudster]

        roles: dict[str, MuleRole | None] = {fraudster.actor_id: MuleRole.FIRST_HOP}
        for co in cashout_nodes:
            roles[co.actor_id] = MuleRole.CASH_OUT

        txn_plans: list[TxnPlan] = []
        amount = Decimal(str(rng.randint(800, 3500))).quantize(Decimal("1.00"))
        currency = "USD"
        curr_time = start_time

        f_acct = fraudster.wallet_identities.get("account_id", f"wal_{fraudster.actor_id.lower()}")
        f_inst = fraudster.wallet_identities.get("instrument_ids", ["ins_fund01"])[0]

        # 1. Provisional funding top-up
        t_fund = curr_time
        t_rev = t_fund + timedelta(days=rng.uniform(reversal_delay_min, reversal_delay_max))
        funding_plan_id = f"wal_fa_fund_{ctx.instance_id}"

        txn_plans.append(
            TxnPlan(
                plan_id=funding_plan_id,
                source_key="scenario_engine",
                network="wallet",
                kind="funding",
                sender_actor_id=fraudster.actor_id,
                receiver_actor_id=fraudster.actor_id,
                amount=amount,
                currency=currency,
                stages=[
                    StagePlan(
                        name="funding",
                        event_time=t_fund,
                        outcome_data={"status": "COMPLETED", "provisional_credit": True},
                    ),
                    StagePlan(
                        name="reversal",
                        event_time=t_rev,
                        outcome_data={"status": "FAILED", "reason": "funding_failed"},
                    ),
                ],
                attrs={
                    "account_id": f_acct,
                    "instrument_id": f_inst,
                    "instrument_type": "bank_account",
                    "channel": "mobile_app",
                },
                truth_seed={
                    "scenario_id": ctx.instance_id,
                    "pattern": self.pattern,
                    "regime": ctx.regime,
                    "is_fraud": True,
                    "sender_role": MuleRole.FIRST_HOP.value,
                    "receiver_role": MuleRole.FIRST_HOP.value,
                },
            )
        )

        # 2. Rapid outbound transfers/withdrawals within minutes to hours
        prev_node = fraudster
        rem_amount = amount
        for k, co_node in enumerate(cashout_nodes):
            t_out = t_fund + timedelta(minutes=(k + 1) * rng.uniform(10.0, 45.0))
            hop_amt = (rem_amount / Decimal(max(1, len(cashout_nodes) - k))).quantize(
                Decimal("1.00")
            )
            rem_amount -= hop_amt
            transfer_plan_id = f"wal_fa_out_{ctx.instance_id}_{k:02d}"

            snd_acct = prev_node.wallet_identities.get(
                "account_id", f"wal_{prev_node.actor_id.lower()}"
            )
            rcv_acct = co_node.wallet_identities.get(
                "account_id", f"wal_{co_node.actor_id.lower()}"
            )

            txn_plans.append(
                TxnPlan(
                    plan_id=transfer_plan_id,
                    source_key="scenario_engine",
                    network="wallet",
                    kind="p2p_send",
                    sender_actor_id=prev_node.actor_id,
                    receiver_actor_id=co_node.actor_id,
                    amount=hop_amt,
                    currency=currency,
                    stages=[
                        StagePlan(
                            name="p2p",
                            event_time=t_out,
                            outcome_data={"status": "COMPLETED"},
                        )
                    ],
                    attrs={
                        "flow": "send",
                        "sender_wallet_id": snd_acct,
                        "receiver_wallet_id": rcv_acct,
                        "payment_type": "personal",
                    },
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        # pyrefly: ignore[missing-attribute]
                        "sender_role": roles[prev_node.actor_id].value
                        if roles.get(prev_node.actor_id)
                        else None,
                        "receiver_role": MuleRole.CASH_OUT.value,
                    },
                )
            )
            prev_node = co_node

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "funding_amount": amount,
                "reversal_delay_days": (t_rev - t_fund).total_seconds() / 86400.0,
                "hop_count": len(cashout_nodes),
            },
        )
