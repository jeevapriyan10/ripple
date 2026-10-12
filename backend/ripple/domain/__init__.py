"""Universal, network-agnostic domain model for the Ripple fraud detection platform.

This package normalizes the core data structures across diverse payment networks:
- UPI-style instant payments (IMPS, Pix, FedNow, SEPA Instant)
- Card networks (Visa, Mastercard, Amex - auth/clearing/settlement cycles)
- Cryptocurrency networks (Bitcoin, Ethereum, Solana - consensus finality)
- SWIFT correspondent banking (multi-hop correspondent chains, delayed clearing)
- Closed-loop wallets (PayPal, Venmo, internal ledgers)
"""

from ripple.domain.errors import (
    CurrencyMismatchError,
    InvalidIdentifierError,
    InvalidTransactionError,
    PrecisionError,
    RippleDomainError,
)
from ripple.domain.events import (
    FlagEvent,
    FlagSeverity,
    FlagSubjectType,
)
from ripple.domain.graph import (
    EdgeType,
    GraphEdge,
    GraphNode,
    NodeType,
    TransactionSubgraph,
    party_to_identifier_edges,
    party_to_node,
    transaction_to_edges,
)
from ripple.domain.identity import (
    IdentifierType,
    PartyIdentifier,
)
from ripple.domain.money import (
    KNOWN_CURRENCIES,
    AssetClass,
    Currency,
    Money,
)
from ripple.domain.party import (
    KycLevel,
    MuleRole,
    Party,
    PartyType,
    RiskFlags,
)
from ripple.domain.transaction import (
    IntermediaryHop,
    IntermediaryRole,
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

__all__ = [
    # Errors
    "RippleDomainError",
    "CurrencyMismatchError",
    "InvalidIdentifierError",
    "InvalidTransactionError",
    "PrecisionError",
    # Money
    "AssetClass",
    "Currency",
    "Money",
    "KNOWN_CURRENCIES",
    # Identity
    "IdentifierType",
    "PartyIdentifier",
    # Party
    "PartyType",
    "KycLevel",
    "MuleRole",
    "RiskFlags",
    "Party",
    # Transaction
    "TransactionStatus",
    "TransactionDirection",
    "SettlementModel",
    "IntermediaryRole",
    "IntermediaryHop",
    "Transaction",
    # Graph
    "NodeType",
    "EdgeType",
    "GraphNode",
    "GraphEdge",
    "TransactionSubgraph",
    "party_to_node",
    "party_to_identifier_edges",
    "transaction_to_edges",
    # Events
    "FlagSubjectType",
    "FlagSeverity",
    "FlagEvent",
]
