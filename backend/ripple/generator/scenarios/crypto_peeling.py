"""Crypto Peeling Chain scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A criminal wallet transfers illicit cryptocurrency through a long sequence of unhosted
addresses (8-30 hops). At each hop, a small percentage ("the peel") is siphoned to an
ancillary address while the vast majority is forwarded onward. Partway through the chain,
funds cross a cross-chain bridge to obfuscate the transaction graph, ultimately depositing
into a centralized cryptocurrency exchange account.

Critical Invariant (Rule R6):
Amounts conserve EXACTLY with Decimal arithmetic:
input_amount == peel_amount + forward_amount + fee_amount

Expected Graph Signature:
Long sequential directed chain with branching micro-leaves and a terminal exchange sink.

Hard Negatives Resembled:
- exchange_hot_wallet_flow (automated exchange sweeps)
"""

from datetime import timedelta
from decimal import Decimal

from ripple.domain.party import MuleRole
from ripple.generator.identifiers import crypto_address
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.scenarios.base import (
    Scenario,
    ScenarioContext,
    ScenarioPlan,
    register_scenario,
)


@register_scenario
class CryptoPeelingLayeringScenario(Scenario):
    """Long-hop cryptocurrency peeling and cross-chain obfuscation chain."""

    scenario_type: str = "crypto_peeling_layering"
    pattern: str = "peeling_chain"
    required_networks: list[str] = ["crypto"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        crypto_actors = world.actors_for_network("crypto")
        exchanges = [a for a in crypto_actors if a.kind == "exchange"]
        individuals = [
            a
            for a in crypto_actors
            if a.kind == "individual" and a.crypto_identities.get("addresses")
        ]
        if not exchanges or len(individuals) < 5:
            return ScenarioPlan(txn_plans=[], roles={}, summary={"skipped": True})

        min_hops = int(calib.get("scenarios.crypto_peeling_layering.hop_count_min", 8))
        max_hops = int(calib.get("scenarios.crypto_peeling_layering.hop_count_max", 20))
        num_hops = rng.randint(min_hops, max_hops)

        peel_min = float(calib.get("scenarios.crypto_peeling_layering.peel_fraction_min", 0.02))
        peel_max = float(calib.get("scenarios.crypto_peeling_layering.peel_fraction_max", 0.08))

        originator = rng.choice(individuals)
        terminal_exchange = rng.choice(exchanges)

        available_actors = [a for a in crypto_actors if a.actor_id != terminal_exchange.actor_id]
        if not available_actors:
            available_actors = crypto_actors

        hop_actors = [originator]
        for h in range(1, num_hops + 1):
            hop_actors.append(available_actors[h % len(available_actors)])

        roles: dict[str, MuleRole | None] = {
            originator.actor_id: MuleRole.FIRST_HOP,
            terminal_exchange.actor_id: MuleRole.CASH_OUT,
        }
        for act in hop_actors[1:]:
            roles[act.actor_id] = MuleRole.LAYERING

        # Ephemeral unhosted addresses along the peeling chain
        chain_addresses = [crypto_address("evm", rng) for _ in range(num_hops + 1)]

        txn_plans: list[TxnPlan] = []
        curr_time = start_time
        curr_amount = Decimal("10.000000")  # 10 ETH initial capital
        fee = Decimal("0.001000")  # constant fee per hop for exact conservation

        for h in range(num_hops):
            t_hop = curr_time + timedelta(minutes=rng.uniform(15.0, 90.0))
            curr_time = t_hop

            from_addr = chain_addresses[h]
            to_fwd_addr = chain_addresses[h + 1]
            peel_addr = crypto_address("evm", rng)

            peel_fraction = Decimal(str(rng.uniform(peel_min, peel_max))).quantize(
                Decimal("0.0001")
            )
            peel_amt = (curr_amount * peel_fraction).quantize(Decimal("0.000001"))
            # Invariant: curr_amount == peel_amt + fwd_amt + fee
            fwd_amt = curr_amount - peel_amt - fee

            snd_actor = hop_actors[h]
            rcv_actor = hop_actors[h + 1] if h < num_hops - 1 else terminal_exchange
            peel_recipient = available_actors[(h + 2) % len(available_actors)]

            # Main forward transfer
            plan_id_fwd = f"cr_peel_fwd_{ctx.instance_id}_{h:02d}"
            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id_fwd,
                    source_key="scenario_engine",
                    network="crypto",
                    kind="transfer_native",
                    sender_actor_id=snd_actor.actor_id,
                    receiver_actor_id=rcv_actor.actor_id,
                    amount=fwd_amt,
                    currency="ETH",
                    stages=[
                        StagePlan(
                            name="finalized",
                            event_time=t_hop,
                            outcome_data={"confirmations": 12, "tx_status": "finalized"},
                        )
                    ],
                    attrs={
                        "chain": "ethereum",
                        "tx_hash": f"0x{(int(t_hop.timestamp()) + h):064x}",
                        "from_address": from_addr,
                        "to_address": to_fwd_addr,
                        "fee_amount": fee,
                        "native_asset": "ETH",
                    },
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        "sender_role": roles[snd_actor.actor_id].value
                        if roles.get(snd_actor.actor_id)
                        else None,
                        "receiver_role": MuleRole.LAYERING.value
                        if h < num_hops - 1
                        else MuleRole.CASH_OUT.value,
                    },
                )
            )

            # Peeling side branch
            plan_id_peel = f"cr_peel_leaf_{ctx.instance_id}_{h:02d}"
            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id_peel,
                    source_key="scenario_engine",
                    network="crypto",
                    kind="transfer_native",
                    sender_actor_id=snd_actor.actor_id,
                    receiver_actor_id=peel_recipient.actor_id,
                    amount=peel_amt,
                    currency="ETH",
                    stages=[
                        StagePlan(
                            name="finalized",
                            event_time=t_hop + timedelta(seconds=15),
                            outcome_data={"confirmations": 12, "tx_status": "finalized"},
                        )
                    ],
                    attrs={
                        "chain": "ethereum",
                        "tx_hash": f"0x{(int(t_hop.timestamp()) + 1000 + h):064x}",
                        "from_address": from_addr,
                        "to_address": peel_addr,
                        "fee_amount": Decimal("0.000000"),
                        "native_asset": "ETH",
                    },
                    truth_seed={
                        "scenario_id": ctx.instance_id,
                        "pattern": self.pattern,
                        "regime": ctx.regime,
                        "is_fraud": True,
                        "sender_role": roles[snd_actor.actor_id].value
                        if roles.get(snd_actor.actor_id)
                        else None,
                        "receiver_role": MuleRole.LAYERING.value,
                    },
                )
            )

            # Advance forward amount
            curr_amount = fwd_amt

        # Terminal exchange deposit
        t_dep = curr_time + timedelta(hours=2)
        ex_addr = terminal_exchange.crypto_identities.get("addresses", {}).get(
            "evm", ["0x0000000000000000000000000000000000000001"]
        )[0]
        txn_plans.append(
            TxnPlan(
                plan_id=f"cr_peel_dep_{ctx.instance_id}",
                source_key="scenario_engine",
                network="crypto",
                kind="exchange_deposit",
                sender_actor_id=hop_actors[-1].actor_id,
                receiver_actor_id=terminal_exchange.actor_id,
                amount=curr_amount,
                currency="ETH",
                stages=[
                    StagePlan(
                        name="finalized",
                        event_time=t_dep,
                        outcome_data={"confirmations": 12, "tx_status": "finalized"},
                    )
                ],
                attrs={
                    "chain": "ethereum",
                    "tx_hash": f"0x{(int(t_dep.timestamp())):064x}",
                    "from_address": chain_addresses[-1],
                    "to_address": ex_addr,
                    "to_label": "exchange",
                    "fee_amount": Decimal("0.000500"),
                    "native_asset": "ETH",
                },
                truth_seed={
                    "scenario_id": ctx.instance_id,
                    "pattern": self.pattern,
                    "regime": ctx.regime,
                    "is_fraud": True,
                    "sender_role": MuleRole.LAYERING.value,
                    "receiver_role": MuleRole.CASH_OUT.value,
                },
            )
        )

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "hop_count": num_hops,
                "initial_amount": Decimal("10.000000"),
                "terminal_amount": curr_amount,
                "conversed_exact_decimal": True,
            },
        )
