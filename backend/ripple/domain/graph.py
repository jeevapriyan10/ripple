"""Storage-agnostic graph projection models and conversion pure functions.

Ripple models fraud patterns as topological graph structures (e.g. fan-out, fan-in,
rapid-pass-through, cyclic layering). To decouple domain logic, feature engineering
(Mesh), and mule chain detectors (Chain) from underlying graph database drivers (e.g. Neo4j,
NetworkX, Memgraph), this module defines lightweight projection types.

Importantly, multi-hop routing paths (such as SWIFT correspondent chains and Card
authorization flows) are projected into explicit edge sequences:
(sender -> hop1 -> hop2 -> receiver) rather than collapsed single edges, making
intermediary routing choke-points structurally visible.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ripple.domain.party import Party, PartyType
from ripple.domain.transaction import Transaction


class NodeType(str, Enum):
    """Categorization of graph nodes.

    PARTY: Economic transactor (individual, merchant, business, exchange, or unclassified).
    INSTITUTION: Financial routing waypoint (acquirer, card scheme, correspondent bank).
    """

    PARTY = "PARTY"
    INSTITUTION = "INSTITUTION"


class EdgeType(str, Enum):
    """Categorization of directed graph edges.

    TRANSACTION: Direct financial transfer between sender and receiver (instant rails, P2P).
    OWNS_IDENTIFIER: Relationship connecting a Party entity node to a specific PartyIdentifier.
    ROUTED_THROUGH: Intermediary routing edge connecting a transactor through a waypoint.
    """

    TRANSACTION = "TRANSACTION"
    OWNS_IDENTIFIER = "OWNS_IDENTIFIER"
    ROUTED_THROUGH = "ROUTED_THROUGH"


class GraphNode(BaseModel):
    """Storage-agnostic graph node projection.

    Used by downstream graph neural networks, motif extractors, and visualizers without
    binding to Neo4j driver types or Cypher record schemas.
    """

    model_config = ConfigDict(frozen=True)

    node_id: str = Field(
        ...,
        description="Globally unique node identifier string (e.g. party UUID or institution BIC).",
    )
    node_type: NodeType = Field(
        ...,
        description="Type category of this graph node.",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Graph labels applied to the node (e.g. ['Party', 'INDIVIDUAL', 'Mule']).",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Arbitrary flattened node attributes and risk properties.",
    )


class GraphEdge(BaseModel):
    """Storage-agnostic graph edge projection.

    Represents directed relationships:
    - SENDER -> RECEIVER for direct transactions.
    - SENDER -> HOP_1 -> ... -> HOP_N -> RECEIVER for multi-hop routed transactions.
    - PARTY -> IDENTIFIER for identity ownership graphs.
    """

    model_config = ConfigDict(frozen=True)

    edge_id: str = Field(
        ...,
        description="Globally unique edge identifier string.",
    )
    source_id: str = Field(
        ...,
        description="Node identifier of the edge origin.",
    )
    target_id: str = Field(
        ...,
        description="Node identifier of the edge destination.",
    )
    edge_type: EdgeType = Field(
        ...,
        description="Structural category of the relationship.",
    )
    timestamp: datetime | None = Field(
        default=None,
        description="Timestamp of the interaction in UTC.",
    )
    properties: dict[str, Any] = Field(
        default_factory=dict,
        description="Edge weight, amount, status, or routing attributes.",
    )


class TransactionSubgraph(BaseModel):
    """Container representing a localized k-hop neighborhood around a center node.

    Consumed by Chain (mule chain reconstruction) and Watch (real-time neighborhood monitoring)
    to inspect local subgraphs without executing repeated database queries.
    """

    model_config = ConfigDict(frozen=True)

    center_node_id: str = Field(
        ...,
        description="Node ID around which this subgraph was extracted.",
    )
    nodes: list[GraphNode] = Field(
        default_factory=list,
        description="All graph nodes contained within the neighborhood.",
    )
    edges: list[GraphEdge] = Field(
        default_factory=list,
        description="All directed edges traversing nodes within the neighborhood.",
    )


def party_to_node(party: Party) -> GraphNode:
    """Project a Party domain aggregate into a storage-agnostic GraphNode.

    Financial institutions and exchanges are projected with NodeType.INSTITUTION;
    individuals, merchants, and businesses with NodeType.PARTY.
    """
    is_institution = party.party_type in {
        PartyType.FINANCIAL_INSTITUTION,
        PartyType.EXCHANGE,
    }
    node_type = NodeType.INSTITUTION if is_institution else NodeType.PARTY

    labels: list[str] = ["Party", party.party_type.value]
    if party.is_mule is True:
        labels.append("Mule")
        if party.mule_role:
            labels.append(party.mule_role.value)

    properties: dict[str, Any] = {
        "party_id": str(party.party_id),
        "party_type": party.party_type.value,
        "home_network": party.home_network,
        "country_code": party.country_code,
        "kyc_level": party.risk_flags.kyc_level.value,
        "is_sanctioned": party.risk_flags.is_sanctioned,
        "is_high_risk_jurisdiction": party.risk_flags.is_high_risk_jurisdiction,
        "is_newly_created": party.risk_flags.is_newly_created,
        "is_mule": party.is_mule,
        "mule_role": party.mule_role.value if party.mule_role else None,
        "identifiers": [ident.normalize() for ident in party.identifiers],
        **party.attributes,
    }

    return GraphNode(
        node_id=str(party.party_id),
        node_type=node_type,
        labels=labels,
        properties=properties,
    )


def party_to_identifier_edges(party: Party) -> list[GraphEdge]:
    """Generate OWNS_IDENTIFIER edges connecting a Party node to each of its identifiers."""
    edges: list[GraphEdge] = []
    party_node_id = str(party.party_id)
    for ident in party.identifiers:
        ident_canonical = ident.normalize()
        edge_id = f"{party_node_id}_owns_{ident_canonical}"
        edges.append(
            GraphEdge(
                edge_id=edge_id,
                source_id=party_node_id,
                target_id=ident_canonical,
                edge_type=EdgeType.OWNS_IDENTIFIER,
                timestamp=party.created_at,
                properties={
                    "identifier_type": ident.type.value,
                    "network": ident.network,
                    "is_pseudonymous": ident.is_pseudonymous,
                },
            )
        )
    return edges


def transaction_to_edges(transaction: Transaction) -> list[GraphEdge]:
    """Project a Transaction aggregate into one or more directed GraphEdge instances.

    - Single Direct Transfer:
      When `transaction.intermediary_hops` is empty (e.g. instant UPI, internal wallet transfer,
      direct P2P crypto), exactly 1 `EdgeType.TRANSACTION` edge is produced connecting
      `sender_id -> receiver_id`.

    - Multi-Hop Routed Transfer:
      When `N` intermediary hops exist (e.g. SWIFT correspondent banking, Card clearing paths),
      exactly `N + 1` `EdgeType.ROUTED_THROUGH` edges are produced connecting:
      sender -> hop_1 -> hop_2 -> ... -> hop_N -> receiver.
      This preserves institutional waypoint structure and fee leakage in graph models.
    """
    if not transaction.intermediary_hops:
        # Direct transfer: single TRANSACTION edge
        return [
            GraphEdge(
                edge_id=str(transaction.transaction_id),
                source_id=str(transaction.sender_id),
                target_id=str(transaction.receiver_id),
                edge_type=EdgeType.TRANSACTION,
                timestamp=transaction.initiated_at,
                properties={
                    "transaction_id": str(transaction.transaction_id),
                    "network_transaction_ref": transaction.network_transaction_ref,
                    "network": transaction.network,
                    "amount": str(transaction.amount.amount),
                    "currency": transaction.amount.currency,
                    "status": transaction.status.value,
                    "settlement_model": transaction.settlement_model.value,
                    "direction": transaction.direction.value,
                    "channel": transaction.channel,
                    "is_fraud": transaction.is_fraud,
                },
            )
        ]

    # Multi-hop routing: N intermediary hops -> N + 1 edges
    sorted_hops = sorted(transaction.intermediary_hops, key=lambda h: h.sequence)
    num_hops = len(sorted_hops)

    # Build sequence of waypoint node identifiers: sender -> hop_1 -> ... -> hop_N -> receiver
    waypoints: list[str] = (
        [str(transaction.sender_id)]
        + [hop.institution_identifier.normalize() for hop in sorted_hops]
        + [str(transaction.receiver_id)]
    )

    edges: list[GraphEdge] = []
    for i in range(num_hops + 1):
        source = waypoints[i]
        target = waypoints[i + 1]
        edge_id = f"{transaction.transaction_id}_hop_{i}"

        # Determine edge timestamp according to progress along path
        if i == 0:
            edge_ts = transaction.initiated_at
        elif i <= num_hops:
            edge_ts = sorted_hops[i - 1].received_at or transaction.initiated_at
        else:
            edge_ts = transaction.settled_at or transaction.initiated_at

        edge_props: dict[str, Any] = {
            "transaction_id": str(transaction.transaction_id),
            "network_transaction_ref": transaction.network_transaction_ref,
            "network": transaction.network,
            "amount": str(transaction.amount.amount),
            "currency": transaction.amount.currency,
            "status": transaction.status.value,
            "settlement_model": transaction.settlement_model.value,
            "hop_sequence": i,
            "total_hops": num_hops,
        }

        # Add waypoint role/fee context if edge originates from or traverses a hop
        if 0 < i <= num_hops:
            hop = sorted_hops[i - 1]
            edge_props["role"] = hop.role.value
            if hop.fee is not None:
                edge_props["fee"] = str(hop.fee.amount)

        edges.append(
            GraphEdge(
                edge_id=edge_id,
                source_id=source,
                target_id=target,
                edge_type=EdgeType.ROUTED_THROUGH,
                timestamp=edge_ts,
                properties=edge_props,
            )
        )

    return edges
