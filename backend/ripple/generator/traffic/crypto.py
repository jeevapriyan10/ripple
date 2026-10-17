"""Background traffic planner for Cryptocurrency blockchains."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ripple.adapters.crypto.chains import DEFAULT_CHAINS
from ripple.generator.engine import SimulationContext, SourcePlanner
from ripple.generator.identifiers import crypto_address, tx_hash
from ripple.generator.plan import StagePlan, TxnPlan
from ripple.generator.traffic.base import sample_amount, sample_tick_actors

# Synthetic base price table for fiat USD equivalence
SYNTHETIC_PRICES: dict[str, float] = {
    "BTC": 60000.0,
    "ETH": 3000.0,
    "SOL": 150.0,
    "TRX": 0.15,
    "USDC_SYN": 1.0,
}


class CryptoTrafficPlanner(SourcePlanner):
    """Generates normal, non-fraudulent blockchain transactions across supported chains."""

    source_key: str = "bg_crypto"

    def __init__(self) -> None:
        self._plan_seq = 0

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        if "crypto" not in ctx.config.networks:
            return []

        crypto_actors = ctx.world.actors_for_network("crypto")
        if not crypto_actors:
            return []

        calib = ctx.calibration
        rng = ctx.factory.stream("traffic", "crypto", str(int(tick_start.timestamp())))
        base_rate = float(calib.get("crypto.base_rate_per_actor_hour", 0.01))

        initiators = sample_tick_actors(
            eligible_actors=crypto_actors,
            tick_start=tick_start,
            base_rate_per_hour=base_rate,
            rng=rng,
            hourly_curve=calib.get("diurnal.hourly_curve"),
            weekday_factors=calib.get("diurnal.weekday_factors"),
        )

        exchanges = [a for a in crypto_actors if a.kind == "exchange"]
        individuals = [a for a in crypto_actors if a.kind == "individual"]

        plans: list[TxnPlan] = []
        chains = ["ethereum", "bitcoin", "solana", "tron"]
        intel_coverage = float(calib.get("crypto.intel_coverage", 0.70))

        plan_idx = 0
        for sender in initiators:
            plan_idx += 1
            plan_id = f"crypto_bg_{int(tick_start.timestamp())}_{plan_idx:04d}"

            chain_name = rng.choice(chains)
            chain_spec = DEFAULT_CHAINS[chain_name]
            required_confs = chain_spec.required_confirmations

            s_crypto = sender.crypto_identities
            s_addr = s_crypto["addresses"][chain_spec.family][0]

            r_type = rng.random()
            if r_type < 0.50 and individuals:
                txn_kind = "transfer_native"
                receiver = rng.choice([a for a in crypto_actors if a.actor_id != sender.actor_id])
                asset_symbol = chain_spec.native_symbol
                token_meta = None
                nft_meta = None

            elif r_type < 0.85 and individuals:
                txn_kind = "transfer_token"
                receiver = rng.choice([a for a in crypto_actors if a.actor_id != sender.actor_id])
                asset_symbol = "USDC_SYN"
                token_meta = {
                    "symbol": "USDC_SYN",
                    "decimals": 6,
                    "token_standard": "ERC-20" if chain_name == "ethereum" else "SPL",
                }
                nft_meta = None

            elif r_type < 0.95 and exchanges:
                txn_kind = "exchange_deposit"
                receiver = rng.choice(exchanges)
                asset_symbol = chain_spec.native_symbol
                token_meta = None
                nft_meta = None

            else:
                txn_kind = "nft_sale"
                receiver = rng.choice([a for a in crypto_actors if a.actor_id != sender.actor_id])
                asset_symbol = "NFT_COLLECTION_1"
                token_meta = None
                nft_meta = {
                    "token_id": str(rng.randint(1, 10000)),
                    "contract_address": crypto_address("evm", rng),
                    "consideration_amount": "1.50",
                    "consideration_asset": "ETH",
                }

            r_crypto = receiver.crypto_identities
            r_addr = r_crypto["addresses"][chain_spec.family][0]

            # Amount in asset units
            amt_dec = sample_amount(rng, median=1.5, sigma=1.2, currency="USD")

            # Native fee
            native_fee = Decimal("0.002") if chain_name == "ethereum" else Decimal("0.0001")

            # USD equivalence
            base_price = SYNTHETIC_PRICES.get(asset_symbol, 100.0)
            usd_val = (amt_dec * Decimal(str(base_price))).quantize(Decimal("0.01"))

            # Telemetry / intel labels
            from_label = None
            to_label = None
            if sender.kind in {"exchange", "bridge", "mixer", "defi", "gambling"} and rng.bernoulli(
                intel_coverage
            ):
                from_label = sender.kind
            if receiver.kind in {
                "exchange",
                "bridge",
                "mixer",
                "defi",
                "gambling",
            } and rng.bernoulli(intel_coverage):
                to_label = receiver.kind

            # Eagerly plan blockchain lifecycle stages
            minute_offset = rng.uniform(0.0, 45.0)
            t_first_seen = tick_start + timedelta(minutes=minute_offset)
            current_tx_hash = tx_hash(rng, prefix_0x=(chain_name == "ethereum"))

            r_outcome = rng.random()
            stages: list[StagePlan] = []

            if r_outcome < 0.01:
                # Dropped from mempool
                stages.append(
                    StagePlan(
                        name="pending",
                        event_time=t_first_seen,
                        outcome_data={"status": "pending", "confirmation_count": 0},
                    )
                )
                stages.append(
                    StagePlan(
                        name="dropped",
                        event_time=t_first_seen + timedelta(minutes=15),
                        outcome_data={"status": "dropped", "confirmation_count": 0},
                    )
                )

            elif r_outcome < 0.02:
                # Replaced by higher fee tx
                stages.append(
                    StagePlan(
                        name="pending",
                        event_time=t_first_seen,
                        outcome_data={"status": "pending", "confirmation_count": 0},
                    )
                )
                replacement_hash = tx_hash(rng, prefix_0x=(chain_name == "ethereum"))
                stages.append(
                    StagePlan(
                        name="replaced",
                        event_time=t_first_seen + timedelta(minutes=2),
                        outcome_data={
                            "status": "replaced",
                            "confirmation_count": 0,
                            "replaced_by": replacement_hash,
                        },
                    )
                )

            elif r_outcome < 0.03:
                # Execution reverted on-chain
                t_inc = t_first_seen + timedelta(minutes=1)
                stages.append(
                    StagePlan(
                        name="reverted",
                        event_time=t_inc,
                        outcome_data={
                            "status": "reverted",
                            "block_number": 15000000,
                            "confirmation_count": 1,
                            "fee_amount": native_fee,
                        },
                    )
                )

            else:
                # Normal confirmation -> finalization progression
                stages.append(
                    StagePlan(
                        name="pending",
                        event_time=t_first_seen,
                        outcome_data={"status": "pending", "confirmation_count": 0},
                    )
                )

                # Snapshot 1: included
                t_inc = t_first_seen + timedelta(minutes=1)
                stages.append(
                    StagePlan(
                        name="included",
                        event_time=t_inc,
                        outcome_data={
                            "status": "included",
                            "block_number": 15000001,
                            "confirmation_count": max(1, required_confs // 4),
                            "fee_amount": native_fee,
                            "usd_value": usd_val,
                        },
                    )
                )

                # Snapshot 2: finalized
                t_fin = t_inc + timedelta(minutes=12)
                stages.append(
                    StagePlan(
                        name="finalized",
                        event_time=t_fin,
                        outcome_data={
                            "status": "finalized",
                            "block_number": 15000001,
                            "confirmation_count": required_confs + 5,
                            "fee_amount": native_fee,
                            "usd_value": usd_val,
                        },
                    )
                )

            attrs: dict[str, Any] = {
                "chain": chain_name,
                "tx_hash": current_tx_hash,
                "from_address": s_addr,
                "to_address": r_addr,
                "asset": asset_symbol,
                "native_asset": chain_spec.native_symbol,
                "from_label": from_label,
                "to_label": to_label,
                "token_metadata": token_meta,
                "nft_metadata": nft_meta,
            }

            plan = TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="crypto",
                kind=txn_kind,
                sender_actor_id=sender.actor_id,
                receiver_actor_id=receiver.actor_id,
                amount=amt_dec,
                currency="USD",
                stages=stages,
                attrs=attrs,
                truth_seed={"scenario_id": None, "is_fraud": False, "hard_negative": False},
            )
            plans.append(plan)

        return plans
