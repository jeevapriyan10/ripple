"""NFT Wash Trading scenario typology adhering to Rules R1-R6.

Real-World Pattern:
A syndicated ring of 3-6 colluding crypto addresses transfers the same non-fungible
token (NFT) in circular succession (6-20 times), incrementally stepping up the reported
purchase price (consideration) on each sale to fabricate artificial trading volume
and manipulate valuation.

Expected Graph Signature:
Directed cyclical ring / Hamiltonian-style loop among a tightly coupled cluster of addresses
with monotonically increasing transaction consideration.

Hard Negatives Resembled:
- active collector community trading high-velocity collectibles.
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
class NftWashTradingScenario(Scenario):
    """Circular wash trading of an NFT to manipulate market price."""

    scenario_type: str = "nft_wash_trading"
    pattern: str = "wash_trading"
    required_networks: list[str] = ["crypto"]

    def plan(self, ctx: ScenarioContext) -> ScenarioPlan:
        rng = ctx.substream
        calib = ctx.calibration
        world = ctx.world
        start_time = ctx.start_time

        min_ring = int(calib.get("scenarios.nft_wash_trading.ring_size_min", 3))
        max_ring = int(calib.get("scenarios.nft_wash_trading.ring_size_max", 6))
        ring_size = rng.randint(min_ring, max_ring)

        min_cycles = int(calib.get("scenarios.nft_wash_trading.cycle_count_min", 6))
        max_cycles = int(calib.get("scenarios.nft_wash_trading.cycle_count_max", 16))
        total_trades = rng.randint(min_cycles, max_cycles)

        price_step_pct = float(calib.get("scenarios.nft_wash_trading.price_escalation_pct", 0.15))

        crypto_actors = world.actors_for_network("crypto")
        if len(crypto_actors) < 3:
            return ScenarioPlan(txn_plans=[], roles={}, summary={"skipped": True})

        ring_actors = rng.sample_without_replacement(
            crypto_actors, min(ring_size, len(crypto_actors))
        )
        ring_size = len(ring_actors)
        roles: dict[str, MuleRole | None] = {a.actor_id: MuleRole.LAYERING for a in ring_actors}

        ring_addrs = [
            a.crypto_identities.get("addresses", {}).get("evm", [crypto_address("evm", rng)])[0]
            for a in ring_actors
        ]

        contract_addr = crypto_address("evm", rng)
        token_id = f"nft_{int(start_time.timestamp()) % 1000000}"

        txn_plans: list[TxnPlan] = []
        curr_time = start_time
        current_price = Decimal("2.500000")  # Initial 2.50 ETH

        for t in range(total_trades):
            idx_from = t % ring_size
            idx_to = (t + 1) % ring_size

            t_sale = curr_time + timedelta(hours=t * rng.uniform(2.0, 8.0))
            current_price = (current_price * Decimal(str(1.0 + price_step_pct))).quantize(
                Decimal("0.000001")
            )
            plan_id = f"cr_wash_{ctx.instance_id}_{t:02d}"

            txn_plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key="scenario_engine",
                    network="crypto",
                    kind="nft_sale",
                    sender_actor_id=ring_actors[idx_from].actor_id,
                    receiver_actor_id=ring_actors[idx_to].actor_id,
                    amount=Decimal("1"),  # 1 NFT
                    currency="ETH",
                    stages=[
                        StagePlan(
                            name="finalized",
                            event_time=t_sale,
                            outcome_data={"confirmations": 12, "tx_status": "finalized"},
                        )
                    ],
                    attrs={
                        "chain": "ethereum",
                        "tx_hash": f"0x{(int(t_sale.timestamp()) + t):064x}",
                        "from_address": ring_addrs[idx_from],
                        "to_address": ring_addrs[idx_to],
                        "contract_address": contract_addr,
                        "token_id": token_id,
                        "consideration_amount": current_price,
                        "consideration_asset": "ETH",
                        "fee_amount": Decimal("0.001500"),
                        "native_asset": "ETH",
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

        return ScenarioPlan(
            txn_plans=txn_plans,
            roles=roles,
            summary={
                "ring_size": ring_size,
                "total_trades": total_trades,
                "initial_price": Decimal("2.500000"),
                "final_price": current_price,
                "ring_closed": total_trades >= ring_size,
            },
        )
