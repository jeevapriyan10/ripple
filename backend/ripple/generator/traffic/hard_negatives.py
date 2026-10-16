"""Hard negative traffic generator: legitimate behaviors that resemble fraud typologies."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from ripple.generator.engine import SimulationContext, SourcePlanner
from ripple.generator.plan import StagePlan, TxnPlan


class HardNegativePlanner(SourcePlanner):
    """Generates realistic legitimate traffic that mimics graph/velocity fraud signatures.

    Adheres strictly to Rule R1: each payment network uses its own isolated RNG substream
    so that toggling one network never perturbs any other network's hard negative events.
    """

    source_key: str = "bg_hard_negatives"

    def plan_tick(self, tick_start: datetime, ctx: SimulationContext) -> list[TxnPlan]:
        if not ctx.config.hard_negatives.enabled:
            return []

        rate = ctx.config.hard_negatives.rate
        plans: list[TxnPlan] = []
        enabled_networks = set(ctx.config.networks)
        ts_str = str(int(tick_start.timestamp()))

        # 1. UPI Hard Negatives (isolated stream)
        if "upi" in enabled_networks:
            rng_upi = ctx.factory.stream("traffic", "hard_negatives", "upi", ts_str)
            if rng_upi.bernoulli(min(1.0, max(0.05, rate * 2.0))):
                pat = rng_upi.choice(["payroll_fanout", "merchant_flash_sale", "split_bill"])
                if pat == "payroll_fanout":
                    plans.extend(self._plan_payroll_fanout_upi(tick_start, ctx, rng_upi))
                elif pat == "merchant_flash_sale":
                    plans.extend(self._plan_merchant_flash_sale_upi(tick_start, ctx, rng_upi))
                elif pat == "split_bill":
                    plans.extend(self._plan_split_bill_upi(tick_start, ctx, rng_upi))

        # 2. Card Hard Negatives (isolated stream)
        if "card" in enabled_networks:
            rng_card = ctx.factory.stream("traffic", "hard_negatives", "card", ts_str)
            if rng_card.bernoulli(min(1.0, max(0.05, rate * 2.0))):
                pat = rng_card.choice(
                    ["merchant_flash_sale", "high_chargeback_merchant", "traveler_cross_border"]
                )
                if pat == "merchant_flash_sale":
                    plans.extend(self._plan_merchant_flash_sale_card(tick_start, ctx, rng_card))
                elif pat == "high_chargeback_merchant":
                    plans.extend(self._plan_high_chargeback_merchant(tick_start, ctx, rng_card))
                elif pat == "traveler_cross_border":
                    plans.extend(self._plan_traveler_cross_border(tick_start, ctx, rng_card))

        # 3. Crypto Hard Negatives (isolated stream)
        if "crypto" in enabled_networks:
            rng_crypto = ctx.factory.stream("traffic", "hard_negatives", "crypto", ts_str)
            if rng_crypto.bernoulli(min(1.0, max(0.05, rate * 2.0))):
                plans.extend(self._plan_exchange_hot_wallet_flow(tick_start, ctx, rng_crypto))

        # 4. SWIFT Hard Negatives (isolated stream)
        if "swift" in enabled_networks:
            rng_swift = ctx.factory.stream("traffic", "hard_negatives", "swift", ts_str)
            if rng_swift.bernoulli(min(1.0, max(0.05, rate * 2.0))):
                plans.extend(self._plan_psp_settlement_forwarding(tick_start, ctx, rng_swift))

        # 5. Wallet Hard Negatives (isolated stream)
        if "wallet" in enabled_networks:
            rng_wal = ctx.factory.stream("traffic", "hard_negatives", "wallet", ts_str)
            if rng_wal.bernoulli(min(1.0, max(0.05, rate * 2.0))):
                pat = rng_wal.choice(["freelancer_fan_in", "split_bill"])
                if pat == "freelancer_fan_in":
                    plans.extend(self._plan_freelancer_fan_in(tick_start, ctx, rng_wal))
                elif pat == "split_bill":
                    plans.extend(self._plan_split_bill_wallet(tick_start, ctx, rng_wal))

        return plans

    # --- Implementations ---

    def _plan_payroll_fanout_upi(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        plans: list[TxnPlan] = []
        businesses = [
            a for a in ctx.world.actors_for_network("upi") if a.kind in {"business", "bank"}
        ]
        individuals = [a for a in ctx.world.actors_for_network("upi") if a.kind == "individual"]
        if not businesses or len(individuals) < 4:
            return []

        employer = rng.choice(businesses)
        num_employees = rng.randint(4, 8)
        employees = rng.sample_without_replacement(
            individuals, min(num_employees, len(individuals))
        )

        emp_vpa = employer.upi_identities.get("vpa", "payroll@rplbank1")
        t_base = tick_start + timedelta(minutes=rng.uniform(5.0, 30.0))
        for i, emp in enumerate(employees):
            t_ev = t_base + timedelta(seconds=i * rng.uniform(2.0, 8.0))
            amt = Decimal(str(rng.randint(25000, 65000)))
            plan_id = f"hn_pay_{int(tick_start.timestamp())}_{i:04d}"
            plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="upi",
                    kind="p2p_pay",
                    sender_actor_id=employer.actor_id,
                    receiver_actor_id=emp.actor_id,
                    amount=amt,
                    currency="INR",
                    stages=[
                        StagePlan(
                            name="PAY",
                            event_time=t_ev,
                            outcome_data={"status": "SUCCESS", "response_code": "00"},
                        )
                    ],
                    attrs={
                        "txn_type": "PAY",
                        "payer_vpa": emp_vpa,
                        "payee_vpa": emp.upi_identities.get("vpa", "emp@rplbank1"),
                        "channel": "internet_banking",
                        "payer_bank_code": "RPLX",
                        "payee_bank_code": "RPLX",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "hard_negative": True,
                        "lookalike_of": "upi_mule_chain",
                    },
                )
            )
        return plans

    def _plan_merchant_flash_sale_upi(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        merchants = [a for a in ctx.world.actors_for_network("upi") if a.kind == "merchant"]
        individuals = [a for a in ctx.world.actors_for_network("upi") if a.kind == "individual"]
        if not merchants or len(individuals) < 5:
            return []

        merchant = rng.choice(merchants)
        buyers = rng.sample_without_replacement(individuals, min(5, len(individuals)))
        plans: list[TxnPlan] = []
        t_base = tick_start + timedelta(minutes=rng.uniform(2.0, 20.0))

        for i, buyer in enumerate(buyers):
            t_ev = t_base + timedelta(seconds=i * rng.uniform(5.0, 25.0))
            plan_id = f"hn_flup_{int(tick_start.timestamp())}_{i:04d}"
            plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="upi",
                    kind="merchant_qr",
                    sender_actor_id=buyer.actor_id,
                    receiver_actor_id=merchant.actor_id,
                    amount=Decimal("999.00"),
                    currency="INR",
                    stages=[
                        StagePlan(
                            name="PAY",
                            event_time=t_ev,
                            outcome_data={"status": "SUCCESS", "response_code": "00"},
                        )
                    ],
                    attrs={
                        "txn_type": "PAY",
                        "payer_vpa": buyer.upi_identities.get("vpa", "payer@rplbank1"),
                        "payee_vpa": merchant.upi_identities.get("vpa", "merch@rplbank1"),
                        "channel": "mobile_app",
                        "mcc": "5311",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "hard_negative": True,
                        "lookalike_of": "fan_in_smurfing",
                    },
                )
            )
        return plans

    def _plan_merchant_flash_sale_card(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        merchants = [a for a in ctx.world.actors_for_network("card") if a.kind == "merchant"]
        individuals = [a for a in ctx.world.actors_for_network("card") if a.kind == "individual"]
        if not merchants or len(individuals) < 5:
            return []

        merchant = rng.choice(merchants)
        buyers = rng.sample_without_replacement(individuals, min(5, len(individuals)))
        plans: list[TxnPlan] = []
        t_base = tick_start + timedelta(minutes=rng.uniform(2.0, 20.0))

        for i, buyer in enumerate(buyers):
            t_ev = t_base + timedelta(seconds=i * rng.uniform(5.0, 25.0))
            token = buyer.card_identities["tokens"][0]
            bin_val = buyer.card_identities["bins"][0]
            last4_val = buyer.card_identities["last4s"][0]
            plan_id = f"hn_flcd_{int(tick_start.timestamp())}_{i:04d}"
            plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="card",
                    kind="pos_purchase",
                    sender_actor_id=buyer.actor_id,
                    receiver_actor_id=merchant.actor_id,
                    amount=Decimal("49.99"),
                    currency="USD",
                    stages=[
                        StagePlan(
                            name="authorization",
                            event_time=t_ev,
                            outcome_data={"response_code": "00", "approved": True},
                        )
                    ],
                    attrs={
                        "auth_time": t_ev,
                        "card_token": token,
                        "card_bin": bin_val,
                        "card_last4": last4_val,
                        "pos_entry_mode": "01",
                        "card_present": False,
                        "merchant_id": f"mid_{merchant.actor_id.lower()}",
                        "merchant_mcc": "5311",
                        "acquirer_institution_id": merchant.institution_id,
                        "issuer_institution_id": buyer.institution_id,
                    },
                    truth_seed={
                        "is_fraud": False,
                        "hard_negative": True,
                        "lookalike_of": "fan_in_smurfing",
                    },
                )
            )
        return plans

    def _plan_split_bill_upi(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        individuals = [a for a in ctx.world.actors_for_network("upi") if a.kind == "individual"]
        if len(individuals) < 4:
            return []

        organizer = rng.choice(individuals)
        friends = [a for a in individuals if a.actor_id != organizer.actor_id]
        chosen_friends = rng.sample_without_replacement(friends, min(3, len(friends)))

        plans: list[TxnPlan] = []
        t_base = tick_start + timedelta(minutes=rng.uniform(10.0, 40.0))

        for i, friend in enumerate(chosen_friends):
            t_ev = t_base + timedelta(minutes=i * rng.uniform(1.0, 5.0))
            plan_id = f"hn_spup_{int(tick_start.timestamp())}_{i:04d}"
            plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="upi",
                    kind="p2p_pay",
                    sender_actor_id=friend.actor_id,
                    receiver_actor_id=organizer.actor_id,
                    amount=Decimal("350.00"),
                    currency="INR",
                    stages=[
                        StagePlan(
                            name="PAY",
                            event_time=t_ev,
                            outcome_data={"status": "SUCCESS", "response_code": "00"},
                        )
                    ],
                    attrs={
                        "txn_type": "PAY",
                        "payer_vpa": friend.upi_identities.get("vpa", "f@rplbank1"),
                        "payee_vpa": organizer.upi_identities.get("vpa", "org@rplbank1"),
                        "channel": "mobile_app",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "hard_negative": True,
                        "lookalike_of": "fan_in_smurfing",
                    },
                )
            )
        return plans

    def _plan_split_bill_wallet(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        individuals = [a for a in ctx.world.actors_for_network("wallet") if a.kind == "individual"]
        if len(individuals) < 4:
            return []

        organizer = rng.choice(individuals)
        friends = [a for a in individuals if a.actor_id != organizer.actor_id]
        chosen_friends = rng.sample_without_replacement(friends, min(3, len(friends)))

        plans: list[TxnPlan] = []
        t_base = tick_start + timedelta(minutes=rng.uniform(10.0, 40.0))

        for i, friend in enumerate(chosen_friends):
            t_ev = t_base + timedelta(minutes=i * rng.uniform(1.0, 5.0))
            plan_id = f"hn_spwl_{int(tick_start.timestamp())}_{i:04d}"
            plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="wallet",
                    kind="p2p_send",
                    sender_actor_id=friend.actor_id,
                    receiver_actor_id=organizer.actor_id,
                    amount=Decimal("25.00"),
                    currency="USD",
                    stages=[
                        StagePlan(
                            name="p2p",
                            event_time=t_ev,
                            outcome_data={"status": "COMPLETED"},
                        )
                    ],
                    attrs={
                        "flow": "send",
                        "sender_wallet_id": f"wal_{friend.actor_id.lower()}",
                        "receiver_wallet_id": f"wal_{organizer.actor_id.lower()}",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "hard_negative": True,
                        "lookalike_of": "fan_in_smurfing",
                    },
                )
            )
        return plans

    def _plan_psp_settlement_forwarding(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        psps = [a for a in ctx.world.actors_for_network("swift") if a.kind in {"psp", "business"}]
        merchants = [a for a in ctx.world.actors_for_network("swift") if a.kind == "merchant"]
        if not psps or not merchants:
            return []

        psp = rng.choice(psps)
        merchant = rng.choice(merchants)
        t_base = tick_start + timedelta(minutes=rng.uniform(5.0, 25.0))
        amt = Decimal("49500.00")
        t_ev = t_base + timedelta(minutes=rng.uniform(5.0, 15.0))

        debtor_acc = psp.swift_identities.get("iban") or psp.swift_identities.get(
            "account_number", "1234567890"
        )
        creditor_acc = merchant.swift_identities.get("iban") or merchant.swift_identities.get(
            "account_number", "9876543210"
        )
        debtor_bic = psp.swift_identities.get("bic") or "ZRPAUS33XXX"
        creditor_bic = merchant.swift_identities.get("bic") or "ZRPBUS33XXX"

        plan_id = f"hn_psp_{int(tick_start.timestamp())}_0001"
        return [
            TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="swift",
                kind="supplier_payment",
                sender_actor_id=psp.actor_id,
                receiver_actor_id=merchant.actor_id,
                amount=amt,
                currency="USD",
                stages=[
                    StagePlan(name="instruction", event_time=t_ev, outcome_data={}),
                    StagePlan(name="ACCC", event_time=t_ev + timedelta(hours=2), outcome_data={}),
                ],
                attrs={
                    "uetr": "f47ac10b-58cc-4372-a567-0e02b2c3d401",
                    "debtor_agent_bic": debtor_bic,
                    "creditor_agent_bic": creditor_bic,
                    "sender_bic": debtor_bic,
                    "receiver_bic": creditor_bic,
                    "debtor_account": debtor_acc,
                    "creditor_account": creditor_acc,
                    "purpose_code": "SUPP",
                },
                truth_seed={
                    "is_fraud": False,
                    "hard_negative": True,
                    "lookalike_of": "upi_mule_chain",
                },
            )
        ]

    def _plan_exchange_hot_wallet_flow(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        exchanges = [
            a
            for a in ctx.world.actors_for_network("crypto")
            if a.kind == "exchange" and a.crypto_identities.get("addresses", {}).get("evm")
        ]
        individuals = [
            a
            for a in ctx.world.actors_for_network("crypto")
            if a.kind == "individual" and a.crypto_identities.get("addresses", {}).get("evm")
        ]
        if not exchanges or not individuals:
            return []

        exchange = rng.choice(exchanges)
        user = rng.choice(individuals)
        t_base = tick_start + timedelta(minutes=rng.uniform(5.0, 30.0))

        ex_addr = exchange.crypto_identities["addresses"]["evm"][0]
        u_addr = user.crypto_identities["addresses"]["evm"][0]

        plan_id = f"hn_swp_{int(tick_start.timestamp())}_0001"
        return [
            TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="crypto",
                kind="exchange_deposit",
                sender_actor_id=user.actor_id,
                receiver_actor_id=exchange.actor_id,
                amount=Decimal("1.250000"),
                currency="ETH",
                stages=[
                    StagePlan(
                        name="finalized",
                        event_time=t_base,
                        outcome_data={"confirmations": 12, "tx_status": "finalized"},
                    )
                ],
                attrs={
                    "chain": "ethereum",
                    "tx_hash": f"0x{int(tick_start.timestamp()):064x}",
                    "from_address": u_addr,
                    "to_address": ex_addr,
                    "to_label": "exchange",
                    "fee_amount": Decimal("0.002100"),
                    "native_asset": "ETH",
                },
                truth_seed={
                    "is_fraud": False,
                    "hard_negative": True,
                    "lookalike_of": "crypto_peeling_layering",
                },
            )
        ]

    def _plan_high_chargeback_merchant(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        merchants = [a for a in ctx.world.actors_for_network("card") if a.kind == "merchant"]
        individuals = [a for a in ctx.world.actors_for_network("card") if a.kind == "individual"]
        if not merchants or not individuals:
            return []

        merchant = rng.choice(merchants)
        buyer = rng.choice(individuals)
        t_base = tick_start + timedelta(minutes=rng.uniform(5.0, 30.0))

        token = buyer.card_identities["tokens"][0]
        bin_val = buyer.card_identities["bins"][0]
        last4_val = buyer.card_identities["last4s"][0]
        stages = [
            StagePlan(
                name="authorization",
                event_time=t_base,
                outcome_data={"response_code": "00", "approved": True},
            ),
            StagePlan(
                name="clearing",
                event_time=t_base + timedelta(days=1),
                outcome_data={},
            ),
            StagePlan(
                name="settlement",
                event_time=t_base + timedelta(days=2),
                outcome_data={},
            ),
            StagePlan(
                name="chargeback",
                event_time=t_base + timedelta(days=rng.uniform(15.0, 45.0)),
                outcome_data={"reason_code": "4837"},
            ),
        ]

        plan_id = f"hn_cb_{int(tick_start.timestamp())}_0001"
        return [
            TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="card",
                kind="ecommerce_purchase",
                sender_actor_id=buyer.actor_id,
                receiver_actor_id=merchant.actor_id,
                amount=Decimal("125.00"),
                currency="USD",
                stages=stages,
                attrs={
                    "auth_time": t_base,
                    "card_token": token,
                    "card_bin": bin_val,
                    "card_last4": last4_val,
                    "merchant_id": f"mid_{merchant.actor_id.lower()}",
                    "merchant_mcc": "5999",
                    "acquirer_institution_id": merchant.institution_id,
                    "issuer_institution_id": buyer.institution_id,
                },
                truth_seed={
                    "is_fraud": False,
                    "hard_negative": True,
                    "lookalike_of": "chargeback_fraud_merchant",
                },
            )
        ]

    def _plan_traveler_cross_border(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        merchants = [a for a in ctx.world.actors_for_network("card") if a.kind == "merchant"]
        individuals = [a for a in ctx.world.actors_for_network("card") if a.kind == "individual"]
        if not merchants or not individuals:
            return []

        traveler = rng.choice(individuals)
        foreign_merchants = [m for m in merchants if m.country != traveler.country]
        if not foreign_merchants:
            foreign_merchants = merchants
        merchant = rng.choice(foreign_merchants)

        t_base = tick_start + timedelta(minutes=rng.uniform(5.0, 30.0))
        token = traveler.card_identities["tokens"][0]
        bin_val = traveler.card_identities["bins"][0]
        last4_val = traveler.card_identities["last4s"][0]
        plan_id = f"hn_trav_{int(tick_start.timestamp())}_0001"

        return [
            TxnPlan(
                plan_id=plan_id,
                source_key=self.source_key,
                network="card",
                kind="cross_border",
                sender_actor_id=traveler.actor_id,
                receiver_actor_id=merchant.actor_id,
                amount=Decimal("85.50"),
                currency="EUR",
                stages=[
                    StagePlan(
                        name="authorization",
                        event_time=t_base,
                        outcome_data={"response_code": "00", "approved": True},
                    )
                ],
                attrs={
                    "auth_time": t_base,
                    "card_token": token,
                    "card_bin": bin_val,
                    "card_last4": last4_val,
                    "pos_entry_mode": "05",
                    "card_present": True,
                    "merchant_id": f"mid_{merchant.actor_id.lower()}",
                    "merchant_mcc": "5812",
                    "acquirer_institution_id": merchant.institution_id,
                    "issuer_institution_id": traveler.institution_id,
                },
                truth_seed={
                    "is_fraud": False,
                    "hard_negative": True,
                    "lookalike_of": "account_takeover_drain",
                },
            )
        ]

    def _plan_freelancer_fan_in(
        self, tick_start: datetime, ctx: SimulationContext, rng: Any
    ) -> list[TxnPlan]:
        individuals = [a for a in ctx.world.actors_for_network("wallet") if a.kind == "individual"]
        businesses = [
            a for a in ctx.world.actors_for_network("wallet") if a.kind in {"business", "merchant"}
        ]
        if len(individuals) < 2 or not businesses:
            return []

        freelancer = rng.choice(individuals)
        clients = rng.sample_without_replacement(businesses, min(3, len(businesses)))
        plans: list[TxnPlan] = []
        t_base = tick_start + timedelta(minutes=rng.uniform(5.0, 25.0))

        for i, client in enumerate(clients):
            t_ev = t_base + timedelta(minutes=i * rng.uniform(5.0, 15.0))
            plan_id = f"hn_free_{int(tick_start.timestamp())}_{i:04d}"
            plans.append(
                TxnPlan(
                    plan_id=plan_id,
                    source_key=self.source_key,
                    network="wallet",
                    kind="p2p_send",
                    sender_actor_id=client.actor_id,
                    receiver_actor_id=freelancer.actor_id,
                    amount=Decimal(str(rng.randint(300, 1200))),
                    currency="USD",
                    stages=[
                        StagePlan(
                            name="p2p",
                            event_time=t_ev,
                            outcome_data={"status": "COMPLETED"},
                        )
                    ],
                    attrs={
                        "flow": "send",
                        "sender_wallet_id": f"wal_{client.actor_id.lower()}",
                        "receiver_wallet_id": f"wal_{freelancer.actor_id.lower()}",
                        "payment_type": "goods_services",
                    },
                    truth_seed={
                        "is_fraud": False,
                        "hard_negative": True,
                        "lookalike_of": "fan_in_smurfing",
                    },
                )
            )
        return plans
