"""Universal, rail-agnostic payment transaction domain model.

The Transaction aggregate represents an EDGE in the Ripple graph. Modeling payment edges
is challenging because real-world payment networks operate on drastically different lifecycles:
- UPI: Instant single-message settlement (sub-second finality, no hops, push payment).
- Cards (Visa/Mastercard): Dual-message model (authorization holds funds, clearing/settlement
  occurs 24-48 hours later through acquirer -> scheme -> issuer hops, reversible via chargeback).
- Cryptocurrencies (Bitcoin/Ethereum): Probabilistic/deterministic consensus, block depth
  confirmations, irreversible, unhosted/self-custodied pseudonymous transfers.
- Correspondent Banking (SWIFT): Serial multi-hop routing across bilateral Nostro/Vostro
  accounts, delayed settlement (days), opaque fees deducted by intermediary correspondent banks.
- Closed-Loop Wallets: Atomic balance updates within a proprietary centralized ledger.

This module unifies all rails into a single schema while maintaining structural fidelity.
"""

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ripple.domain.errors import InvalidTransactionError
from ripple.domain.identity import PartyIdentifier
from ripple.domain.money import Money


class TransactionStatus(str, Enum):
    """Lifecycle status union encompassing state machines across all payment networks.

    INITIATED: Payment intent created by the payer; not yet authorized.
    PENDING_AUTHORIZATION: Forwarded to authorizing authority or broadcast to blockchain mempool.
    AUTHORIZED: Payer's credit line or funds reserved (Card dual-message phase 1).
    PENDING_SETTLEMENT: Authorized and queued in clearing cycle (ACH batch, card, SWIFT in transit).
    SETTLED: Funds irrevocably transferred and credited to destination ledger (Instant UPI credit).
    CONFIRMED: Cryptographic consensus reached with sufficient confirmation depth on-chain.
    FAILED: Execution halted due to network timeout, insufficient balance, or protocol abort.
    REJECTED: Declined by fraud detection system, compliance rule, or issuer decline code.
    REVERSED: Transaction undone via dispute, merchant refund, SWIFT recall, or clawback.
    DISPUTED: Contested by cardholder or payer; undergoing chargeback or arbitration.
    CANCELLED: Aborted by sender prior to execution or authorization.
    """

    INITIATED = "INITIATED"
    PENDING_AUTHORIZATION = "PENDING_AUTHORIZATION"
    AUTHORIZED = "AUTHORIZED"
    PENDING_SETTLEMENT = "PENDING_SETTLEMENT"
    SETTLED = "SETTLED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    REVERSED = "REVERSED"
    DISPUTED = "DISPUTED"
    CANCELLED = "CANCELLED"


class TransactionDirection(str, Enum):
    """Directionality of fund movement from the perspective of the subject account.

    DEBIT: Outflow decreasing balance or drawing credit.
    CREDIT: Inflow increasing balance.
    TRANSFER: End-to-end payment movement between a distinct sender and receiver.
    """

    DEBIT = "DEBIT"
    CREDIT = "CREDIT"
    TRANSFER = "TRANSFER"


class SettlementModel(str, Enum):
    """The fundamental architectural mechanism by which a payment rail moves value.

    INSTANT: Real-time gross settlement (RTGS) or instant credit push (UPI, Pix, FedNow).
    DEFERRED_NET: Periodic batch netting where net balances settle at cutoff times (ACH, BACS).
    AUTH_CAPTURE: Dual-message two-phase protocol where authorization precedes clearing (Cards).
    BLOCK_CONFIRMATION: Distributed consensus finality across decentralized nodes (BTC, ETH).
    CORRESPONDENT_CHAIN: Bilateral serial ledger entries across Nostro/Vostro accounts (SWIFT).
    INTERNAL_LEDGER: Atomic database mutation within a closed platform (PayPal, Venmo internal).
    """

    INSTANT = "INSTANT"
    DEFERRED_NET = "DEFERRED_NET"
    AUTH_CAPTURE = "AUTH_CAPTURE"
    BLOCK_CONFIRMATION = "BLOCK_CONFIRMATION"
    CORRESPONDENT_CHAIN = "CORRESPONDENT_CHAIN"
    INTERNAL_LEDGER = "INTERNAL_LEDGER"


class IntermediaryRole(str, Enum):
    """Functional role of an institutional hop along a payment routing path.

    ACQUIRER: Merchant's acquiring bank or payment processor in card networks.
    ISSUER: Payer's card-issuing bank holding the account credit/debit facility.
    SCHEME: Card network operator switch (Visa, Mastercard, RuPay, Amex).
    CORRESPONDENT_BANK: Intermediate clearing bank in cross-border correspondent chains.
    PSP: Payment Service Provider or switch initiating or aggregating the transaction.
    VALIDATOR: Blockchain consensus node, miner, or sequencer confirming the transaction.
    EXCHANGE: Custodial off-ramp / on-ramp or OTC liquidity provider bridging assets.
    OTHER: Auxiliary intermediate router or gateway.
    """

    ACQUIRER = "ACQUIRER"
    ISSUER = "ISSUER"
    SCHEME = "SCHEME"
    CORRESPONDENT_BANK = "CORRESPONDENT_BANK"
    PSP = "PSP"
    VALIDATOR = "VALIDATOR"
    EXCHANGE = "EXCHANGE"
    OTHER = "OTHER"


class IntermediaryHop(BaseModel):
    """Value object representing an institutional waypoint in a multi-hop routing path.

    Crucial for modeling:
    - SWIFT wires: Transfer from London to Mumbai may traverse NY and Frankfurt correspondents.
    - Card networks: Payer -> Merchant POS -> Acquirer -> Visa Scheme -> Issuer Bank.
    Each hop adds latency and may levy independent intermediary fees.
    """

    model_config = ConfigDict(frozen=True)

    sequence: int = Field(
        ...,
        ge=1,
        description="1-indexed sequence order of this waypoint along the payment path.",
    )
    institution_identifier: PartyIdentifier = Field(
        ...,
        description="Identifier (BIC, Merchant ID, or Bank Code) of the routing institution.",
    )
    role: IntermediaryRole = Field(
        ...,
        description="Functional role played by this institution in clearing/routing.",
    )
    received_at: datetime | None = Field(
        default=None,
        description="Timestamp when this intermediary processed or received the payment, in UTC.",
    )
    fee: Money | None = Field(
        default=None,
        description="Intermediary fee levied by this waypoint (e.g. SWIFT correspondent fee).",
    )

    @model_validator(mode="after")
    def _validate_timestamps(self) -> Self:
        if self.received_at is not None:
            if (
                self.received_at.tzinfo is None
                or self.received_at.tzinfo.utcoffset(self.received_at) is None
            ):
                raise InvalidTransactionError(
                    "IntermediaryHop received_at must be timezone-aware (UTC)."
                )
        return self


def _ensure_utc(dt: datetime | None, field_name: str) -> datetime | None:
    """Validate that a datetime is timezone-aware and convert to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise InvalidTransactionError(
            f"Field '{field_name}' must be a timezone-aware datetime (UTC)."
        )
    return dt.astimezone(UTC)


class Transaction(BaseModel):
    """Universal payment transaction aggregate representing an EDGE in the Ripple graph.

    Normalizes data across:
    - UPI (instant P2P/P2M transfers)
    - Card networks (authorization, clearing, chargeback cycles)
    - Blockchains (high precision, consensus confirmation)
    - SWIFT correspondent banking (multi-hop correspondent chains, opaque delays)
    - Closed-loop wallets (internal ledger transfers)
    """

    model_config = ConfigDict(frozen=False)

    # Identity
    transaction_id: UUID = Field(
        default_factory=uuid4,
        description="Internal stable UUID identifying this transaction in Ripple.",
    )
    network_transaction_ref: str = Field(
        ...,
        description="Native rail transaction reference (e.g. UPI RRN, SWIFT UETR, txhash).",
    )
    network: str = Field(
        ...,
        description="Payment rail (e.g. 'UPI', 'VISA', 'SWIFT', 'ETHEREUM', 'PAYPAL').",
    )

    # Counterparties
    sender_id: UUID = Field(
        ...,
        description="Party node UUID of the payer/originator.",
    )
    receiver_id: UUID = Field(
        ...,
        description="Party node UUID of the payee/beneficiary.",
    )
    sender_identifier: PartyIdentifier = Field(
        ...,
        description="Specific counterparty identifier used by sender in this transaction.",
    )
    receiver_identifier: PartyIdentifier = Field(
        ...,
        description="Specific counterparty identifier used by receiver in this transaction.",
    )

    # Value
    amount: Money = Field(
        ...,
        description="Principal monetary amount transferred.",
    )
    fee: Money | None = Field(
        default=None,
        description="Network or interchange transaction fee paid.",
    )

    # Timing
    initiated_at: datetime = Field(
        ...,
        description="Timestamp when the payer initiated the transaction, in UTC.",
    )
    settled_at: datetime | None = Field(
        default=None,
        description="Timestamp when value irrevocably moved or cleared, in UTC.",
    )
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when Ripple ingested this event, in UTC.",
    )

    # Lifecycle
    status: TransactionStatus = Field(
        ...,
        description="Current state in the unified payment state machine.",
    )
    settlement_model: SettlementModel = Field(
        ...,
        description="Settlement architecture characterizing fund movement on this rail.",
    )
    direction: TransactionDirection = Field(
        default=TransactionDirection.TRANSFER,
        description="Direction of value movement (DEBIT, CREDIT, TRANSFER).",
    )
    is_reversible: bool = Field(
        default=False,
        description="True for rails permitting clawbacks/chargebacks (Cards, ACH).",
    )
    reversal_window_ends_at: datetime | None = Field(
        default=None,
        description="Timestamp marking expiration of dispute/chargeback window, in UTC.",
    )

    # Routing
    intermediary_hops: list[IntermediaryHop] = Field(
        default_factory=list,
        description="List of intermediary institutional waypoints traversed in multi-hop paths.",
    )

    # Context
    channel: str = Field(
        default="api",
        description="Origination channel (e.g. 'mobile', 'web', 'pos', 'atm', 'api').",
    )
    purpose_code: str | None = Field(
        default=None,
        description="Standard purpose identifier (e.g. ISO 20022 'SALA', 'BEXP', 'GIFT').",
    )
    device_id: str | None = Field(
        default=None,
        description="Client hardware or device fingerprint hash.",
    )
    ip_address: str | None = Field(
        default=None,
        description="Client IP address where payment was authorized.",
    )
    geo_country: str | None = Field(
        default=None,
        description="ISO-3166 alpha-2 country code of physical transaction location.",
    )

    # Rail-specific extensibility
    network_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Network-specific key-value pairs that do not pollute universal schema.",
    )

    # Ground truth labels
    is_fraud: bool | None = Field(
        default=None,
        description="Ground-truth indicator whether this transaction was confirmed fraudulent.",
    )
    fraud_pattern: str | None = Field(
        default=None,
        description="Identified fraud typology (e.g. 'APP_SCAM', 'MULE_FAN_OUT').",
    )

    @model_validator(mode="after")
    def _validate_invariants(self) -> Self:
        # Validate timezone awareness on all datetimes
        self.initiated_at = _ensure_utc(self.initiated_at, "initiated_at")  # type: ignore[assignment]
        self.settled_at = _ensure_utc(self.settled_at, "settled_at")
        self.observed_at = _ensure_utc(self.observed_at, "observed_at")  # type: ignore[assignment]
        self.reversal_window_ends_at = _ensure_utc(
            self.reversal_window_ends_at, "reversal_window_ends_at"
        )

        # 1. Amount must be positive
        if self.amount.amount <= Decimal(0):
            raise InvalidTransactionError(
                f"Transaction amount must be strictly positive, got {self.amount.amount}."
            )

        # 2. Sender and receiver must differ
        if self.sender_id == self.receiver_id:
            raise InvalidTransactionError(
                f"Sender ID and receiver ID must differ. Self-transfers disallowed: "
                f"{self.sender_id}."
            )

        # 3. Settled_at must not precede initiated_at
        if self.settled_at is not None and self.settled_at < self.initiated_at:
            raise InvalidTransactionError(
                f"settled_at ({self.settled_at.isoformat()}) cannot precede "
                f"initiated_at ({self.initiated_at.isoformat()})."
            )

        # 4. A SETTLED transaction must have settled_at
        if self.status == TransactionStatus.SETTLED and self.settled_at is None:
            raise InvalidTransactionError(
                "A transaction with status SETTLED must possess a valid settled_at timestamp."
            )

        # 5. Check intermediary hops sequence sanity
        if self.intermediary_hops:
            sequences = [hop.sequence for hop in self.intermediary_hops]
            if len(sequences) != len(set(sequences)):
                raise InvalidTransactionError(
                    "Intermediary hops contain duplicate sequence numbers."
                )

        return self

    @property
    def settlement_latency_seconds(self) -> float | None:
        """Computed latency in seconds between payer initiation and final settlement.

        Returns None if transaction is not yet settled or failed prior to settlement.
        """
        if self.settled_at is None:
            return None
        return (self.settled_at - self.initiated_at).total_seconds()

    @property
    def is_final(self) -> bool:
        """Indicates whether this transaction has reached an irreversible terminal state.

        Terminal states include SETTLED, CONFIRMED, FAILED, REJECTED, REVERSED, and CANCELLED.
        """
        return self.status in {
            TransactionStatus.SETTLED,
            TransactionStatus.CONFIRMED,
            TransactionStatus.FAILED,
            TransactionStatus.REJECTED,
            TransactionStatus.REVERSED,
            TransactionStatus.CANCELLED,
        }
