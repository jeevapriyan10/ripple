"""Account Takeover Drain scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A legitimate retail account experiences credential stuffing, session hijacking, or SIM swap.
The compromised account suddenly registers login telemetry from an unrecognized device_id
and foreign IP address. Within minutes of this anomalous session establishment, the adversary
initiates 1-4 high-velocity outbound transfers to newly registered recipient accounts,
transferring amounts multiple times higher than the account's historical baseline and draining
the balance.

Expected Graph Signature:
Baseline low-velocity node -> sudden out-degree surge to new counterparty nodes with new device/IP.

Hard Negatives Resembled:
- traveler_cross_border (legitimate travel abroad with new roaming IP)
"""

from datetime import timedelta
from decimal import Decimal

from ripple.domain.party import MuleRole
from ripple.generator.identifiers import device_id, ip_address
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.scenarios.base import (
    Scenario,
    ScenarioContext,
    ScenarioPlan,
    register_scenario,
)


@register_scenario
class AccountTakeoverDrainScenario(Scenario):
    """Account takeover followed by immediate high-velocity balance drain."""

    scenario_type: str = "account_takeover_drain"
    pattern: str = "account_takeover"
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

        min_payouts = int(calib.get("scenarios.account_takeover_drain.payout_count_min", 1))
        max_payouts = int(calib.get("scenarios.account_takeover_drain.payout_count_max", 4))
        num_payouts = rng.randint(min_payouts, max_payouts)

        # Victim is an established individual
        victim = rng.choice(individuals)
        available_mules = [m for m in mule_pool if m.actor_id != victim.actor_id]
        if not available_mules:
            available_mules = [a for a in individuals if a.actor_id != victim.actor_id]

        recipients = rng.sample_without_replacement(
            available_mules, min(num_payouts, len(available_mules))
        )
        if not recipients:
            recipients = [victim]

        roles: dict[str, MuleRole | None] = {victim.actor_id: MuleRole.VICTIM}
        for r in recipients:
            roles[r.actor_id] = MuleRole.FIRST_HOP

        # Anomalous rogue session telemetry
        rogue_device = device_id(rng)
        rogue_ip = ip_address(rng)

        txn_plans: list[TxnPlan] = []
        curr_time = start_time
        # Multiplier of ordinary size (several times normal)
        base_size = int(calib.get("wallet.amount_median", 35.0)) * 12
        currency = "USD"

        for p_idx, recipient in enumerate(recipients):
            t_drain = curr_time + timedelta(minutes=rng.uniform(2.0, 15.0) * (p_idx + 1))
            drain_amt = Decimal(str(rng.randint(base_size, base_size * 2))).quantize(
                Decimal("1.00")
            )
            plan_id = f"ato_dr_{ctx.instance_id}_{p_idx:02d}"

            v_acct = victim.wallet_identities.get("account_id", f"wal_{victim.actor_id.lower()}")
            r_acct = recipient.wallet_identities.get(
                "account_id", f"wal_{recipient.actor_id.lower()}"
            )

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="wallet",
                    kind="p2p_send",
                    sender_actor_id=victim.actor_id,
                    receiver_actor_id=recipient.actor_id,
                    amount=drain_amt,
                    currency=currency,
                    stages=[
                        StagePlan(
                            name="p2p",
                            event_time=t_drain,
                            outcome_data={"status": "COMPLETED"},
                        )
                    ],
                    attrs={
                        "flow": "send",
                        "sender_wallet_id": v_acct,
                        "receiver_wallet_id": r_acct,
                        "device_id": rogue_device,
                        "ip_address": rogue_ip,
                        "payment_type": "personal",
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

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "victim_id": victim.actor_id,
                "payout_count": len(recipients),
                "rogue_device": rogue_device,
                "rogue_ip": rogue_ip,
            },
        )
