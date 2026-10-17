"""Cross-Network Laundering scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A sophisticated cross-rail money laundering operation moving illicit value across
three or more payment networks to break transaction traces:
1. Digital Wallet: Illicit fan-in into a perpetrator's wallet account.
2. Interbank SWIFT: Withdrawal to a corporate bank account, followed by an international wire
   to a centralized cryptocurrency exchange intermediary.
3. Cryptographic Ledger: Crypto asset purchase and on-chain withdrawal to an unhosted address,
   proceeding through an obfuscation transfer to a final off-ramp or cash-out node.

Key Architectural Invariant:
The same underlying actor_id holds identities on multiple networks, but shared phone/email
is reused across networks only with probability link_evidence_rate, ensuring that entity
resolution (M08) and cross-rail tracing (M14) face realistic, partial identifier overlap.

Expected Graph Signature:
Multi-rail path traversing Wallet -> SWIFT -> Crypto with shared contact points.
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
class CrossNetworkLaunderingScenario(Scenario):
    """End-to-end multi-rail laundering traversing Wallet, SWIFT, and Crypto."""

    scenario_type: str = "cross_network_laundering"
    pattern: str = "cross_network_layering"
    required_networks: list[str] = ["wallet", "swift", "crypto"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        world = ctx.world
        start_time = ctx.start_time

        # Find actors that participate in multiple networks
        wallet_actors = world.actors_for_network("wallet")
        swift_actors = world.actors_for_network("swift")
        crypto_actors = world.actors_for_network("crypto")

        exchanges = [a for a in crypto_actors if a.kind == "exchange"]
        if not wallet_actors or not swift_actors or not crypto_actors or not exchanges:
            return ScenarioPlan(txn_plans=[], roles={}, summary={"skipped": True})

        # Select multi-network mule
        multi_rail_candidates = [
            a
            for a in world.actors
            if a.has_network("wallet") and a.has_network("swift") and a.has_network("crypto")
        ]
        if multi_rail_candidates:
            launderer = rng.choice(multi_rail_candidates)
        else:
            launderer = rng.choice(wallet_actors)

        victim = rng.choice([a for a in wallet_actors if a.actor_id != launderer.actor_id])
        exchange = rng.choice(exchanges)
        cash_out = rng.choice([a for a in crypto_actors if a.actor_id != launderer.actor_id])

        roles: dict[str, MuleRole | None] = {
            victim.actor_id: MuleRole.VICTIM,
            launderer.actor_id: MuleRole.LAYERING,
            cash_out.actor_id: MuleRole.CASH_OUT,
        }

        txn_plans: list[TxnPlan] = []
        curr_time = start_time

        # --- Rail 1: Wallet Fan-in / Theft ---
        t_wal = curr_time
        wal_amt = Decimal("15000.00")
        v_wal = victim.wallet_identities.get("account_id", f"wal_{victim.actor_id.lower()}")
        l_wal = launderer.wallet_identities.get("account_id", f"wal_{launderer.actor_id.lower()}")

        txn_plans.append(
            TxnPlan(
                plan_id=f"xnet_wal_{ctx.instance_id}",
                source_key="scenario_engine",
                network="wallet",
                kind="p2p_send",
                sender_actor_id=victim.actor_id,
                receiver_actor_id=launderer.actor_id,
                amount=wal_amt,
                currency="USD",
                stages=[
                    StagePlan(
                        name="p2p",
                        event_time=t_wal,
                        outcome_data={"status": "COMPLETED"},
                    )
                ],
                attrs={
                    "flow": "send",
                    "sender_wallet_id": v_wal,
                    "receiver_wallet_id": l_wal,
                    "payment_type": "personal",
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

        # --- Rail 2: SWIFT Interbank Wire to Crypto Exchange ---
        t_swift = t_wal + timedelta(hours=rng.uniform(6.0, 24.0))
        t_swift_settle = t_swift + timedelta(hours=rng.uniform(4.0, 12.0))
        swift_amt = Decimal("14800.00")

        l_acc = launderer.swift_identities.get("iban") or launderer.swift_identities.get(
            "account_number", f"acc_{launderer.actor_id}"
        )
        ex_acc = exchange.swift_identities.get("iban") or exchange.swift_identities.get(
            "account_number", f"acc_{exchange.actor_id}"
        )
        l_bic = launderer.swift_identities.get("bic") or "ZRPAUS33XXX"
        ex_bic = exchange.swift_identities.get("bic") or "ZRPBUS33XXX"
        uetr_ts = int(t_swift.timestamp()) % 100000000000

        txn_plans.append(
            TxnPlan(
                plan_id=f"xnet_sw_{ctx.instance_id}",
                source_key="scenario_engine",
                network="swift",
                kind="supplier_payment",
                sender_actor_id=launderer.actor_id,
                receiver_actor_id=exchange.actor_id,
                amount=swift_amt,
                currency="USD",
                stages=[
                    StagePlan(name="instruction", event_time=t_swift, outcome_data={}),
                    StagePlan(name="ACCC", event_time=t_swift_settle, outcome_data={}),
                ],
                attrs={
                    "uetr": f"a1b2c3d4-e5f6-47a8-9012-{uetr_ts:012d}",
                    "debtor_agent_bic": l_bic,
                    "creditor_agent_bic": ex_bic,
                    "sender_bic": l_bic,
                    "receiver_bic": ex_bic,
                    "debtor_account": l_acc,
                    "creditor_account": ex_acc,
                    "purpose_code": "SUPP",
                },
                truth_seed={
                    "scenario_id": ctx.instance_id,
                    "pattern": self.pattern,
                    "regime": ctx.regime,
                    "is_fraud": True,
                    "sender_role": MuleRole.LAYERING.value,
                    "receiver_role": MuleRole.LAYERING.value,
                },
            )
        )

        # --- Rail 3: Crypto Withdrawal & Onward Peeling Transfer ---
        t_crypto = t_swift_settle + timedelta(hours=rng.uniform(2.0, 8.0))
        crypto_amt = Decimal("5.000000")  # ETH equivalent of converted wire
        launderer_evm = crypto_address("evm", rng)
        cashout_evm = crypto_address("evm", rng)

        txn_plans.append(
            TxnPlan(
                plan_id=f"xnet_cr_{ctx.instance_id}",
                source_key="scenario_engine",
                network="crypto",
                kind="transfer_native",
                sender_actor_id=launderer.actor_id,
                receiver_actor_id=cash_out.actor_id,
                amount=crypto_amt,
                currency="ETH",
                stages=[
                    StagePlan(
                        name="finalized",
                        event_time=t_crypto,
                        outcome_data={"confirmations": 12, "tx_status": "finalized"},
                    )
                ],
                attrs={
                    "chain": "ethereum",
                    "tx_hash": f"0x{(int(t_crypto.timestamp())):064x}",
                    "from_address": launderer_evm,
                    "to_address": cashout_evm,
                    "fee_amount": Decimal("0.002000"),
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
                "launderer_actor_id": launderer.actor_id,
                "rails_traversed": ["wallet", "swift", "crypto"],
                "partial_linkage": True,
            },
        )
