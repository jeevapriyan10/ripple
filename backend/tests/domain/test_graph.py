"""Unit tests for storage-agnostic graph projection types and pure conversion functions."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from ripple.domain.graph import (
    EdgeType,
    NodeType,
    TransactionSubgraph,
    party_to_identifier_edges,
    party_to_node,
    transaction_to_edges,
)
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import Money
from ripple.domain.party import KycLevel, MuleRole, Party, PartyType, RiskFlags
from ripple.domain.transaction import (
    IntermediaryHop,
    IntermediaryRole,
    SettlementModel,
    Transaction,
    TransactionStatus,
)


def test_party_to_node_projection() -> None:
    """Test projecting Party aggregates into GraphNode with correct type, labels, and properties."""
    # Individual party with mule labels
    individual_party = Party(
        party_type=PartyType.INDIVIDUAL,
        home_network="UPI",
        country_code="IN",
        risk_flags=RiskFlags(kyc_level=KycLevel.BASIC, is_newly_created=True),
        is_mule=True,
        mule_role=MuleRole.FIRST_HOP,
    )

    node = party_to_node(individual_party)
    assert node.node_id == str(individual_party.party_id)
    assert node.node_type == NodeType.PARTY
    assert "Party" in node.labels
    assert "INDIVIDUAL" in node.labels
    assert "Mule" in node.labels
    assert "FIRST_HOP" in node.labels
    assert node.properties["home_network"] == "UPI"
    assert node.properties["is_mule"] is True
    assert node.properties["mule_role"] == "FIRST_HOP"

    # Financial institution party
    inst_party = Party(
        party_type=PartyType.FINANCIAL_INSTITUTION,
        home_network="SWIFT",
        country_code="US",
    )
    inst_node = party_to_node(inst_party)
    assert inst_node.node_type == NodeType.INSTITUTION
    assert "FINANCIAL_INSTITUTION" in inst_node.labels


def test_transaction_to_edges_direct_transfer() -> None:
    """Test that a transaction without hops yields exactly 1 direct TRANSACTION edge."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident = PartyIdentifier(type=IdentifierType.VPA, value="alice@oksbi", network="UPI")
    receiver_ident = PartyIdentifier(type=IdentifierType.VPA, value="bob@okhdfc", network="UPI")
    amount = Money(amount=Decimal("500.00"), currency="INR", scale=2)
    now = datetime.now(UTC)

    tx = Transaction(
        network_transaction_ref="upi-direct-001",
        network="UPI",
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_identifier=sender_ident,
        receiver_identifier=receiver_ident,
        amount=amount,
        initiated_at=now,
        settled_at=now,
        status=TransactionStatus.SETTLED,
        settlement_model=SettlementModel.INSTANT,
        intermediary_hops=[],
    )

    edges = transaction_to_edges(tx)
    assert len(edges) == 1

    edge = edges[0]
    assert edge.edge_id == str(tx.transaction_id)
    assert edge.source_id == str(sender_id)
    assert edge.target_id == str(receiver_id)
    assert edge.edge_type == EdgeType.TRANSACTION
    assert edge.timestamp == now
    assert edge.properties["amount"] == "500.00"
    assert edge.properties["currency"] == "INR"


def test_transaction_to_edges_multi_hop_routing() -> None:
    """Assert transaction_to_edges yields N + 1 edges for N hops in topological order."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident = PartyIdentifier(
        type=IdentifierType.IBAN, value="GB82WEST12345698765432", network="SWIFT"
    )
    receiver_ident = PartyIdentifier(
        type=IdentifierType.IBAN, value="DE89370400440532013000", network="SWIFT"
    )
    amount = Money(amount=Decimal("50000.00"), currency="EUR", scale=2)
    now = datetime.now(UTC)

    # 3 intermediary hops: Sender Bank -> Intermediary 1 -> Intermediary 2 -> Receiver Bank
    hop1_ident = PartyIdentifier(type=IdentifierType.BIC, value="BARCGB22", network="SWIFT")
    hop2_ident = PartyIdentifier(type=IdentifierType.BIC, value="CHASUS33", network="SWIFT")
    hop3_ident = PartyIdentifier(type=IdentifierType.BIC, value="DEUTDEDD", network="SWIFT")

    hop1 = IntermediaryHop(
        sequence=1, institution_identifier=hop1_ident, role=IntermediaryRole.CORRESPONDENT_BANK
    )
    hop2 = IntermediaryHop(
        sequence=2, institution_identifier=hop2_ident, role=IntermediaryRole.CORRESPONDENT_BANK
    )
    hop3 = IntermediaryHop(
        sequence=3, institution_identifier=hop3_ident, role=IntermediaryRole.CORRESPONDENT_BANK
    )

    tx = Transaction(
        network_transaction_ref="swift-wire-multi-hop",
        network="SWIFT",
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_identifier=sender_ident,
        receiver_identifier=receiver_ident,
        amount=amount,
        initiated_at=now,
        settled_at=now,
        status=TransactionStatus.SETTLED,
        settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
        intermediary_hops=[hop2, hop1, hop3],  # intentionally unordered in input list
    )

    edges = transaction_to_edges(tx)

    # With N = 3 intermediary hops, exactly N + 1 = 4 edges must be generated
    assert len(edges) == 4

    # Verify topological order: sender -> hop1 -> hop2 -> hop3 -> receiver
    expected_hop1_id = hop1_ident.normalize()
    expected_hop2_id = hop2_ident.normalize()
    expected_hop3_id = hop3_ident.normalize()

    # Edge 0: sender -> hop1
    assert edges[0].source_id == str(sender_id)
    assert edges[0].target_id == expected_hop1_id
    assert edges[0].edge_type == EdgeType.ROUTED_THROUGH
    assert edges[0].properties["hop_sequence"] == 0

    # Edge 1: hop1 -> hop2
    assert edges[1].source_id == expected_hop1_id
    assert edges[1].target_id == expected_hop2_id
    assert edges[1].edge_type == EdgeType.ROUTED_THROUGH
    assert edges[1].properties["hop_sequence"] == 1

    # Edge 2: hop2 -> hop3
    assert edges[2].source_id == expected_hop2_id
    assert edges[2].target_id == expected_hop3_id
    assert edges[2].edge_type == EdgeType.ROUTED_THROUGH
    assert edges[2].properties["hop_sequence"] == 2

    # Edge 3: hop3 -> receiver
    assert edges[3].source_id == expected_hop3_id
    assert edges[3].target_id == str(receiver_id)
    assert edges[3].edge_type == EdgeType.ROUTED_THROUGH
    assert edges[3].properties["hop_sequence"] == 3


def test_transaction_subgraph_and_party_identifier_edges() -> None:
    """Test TransactionSubgraph container and party_to_identifier_edges mapping."""
    party = Party(party_type=PartyType.INDIVIDUAL, home_network="UPI")
    ident = PartyIdentifier(type=IdentifierType.VPA, value="test@upi", network="UPI")
    party.add_identifier(ident)

    ident_edges = party_to_identifier_edges(party)
    assert len(ident_edges) == 1
    assert ident_edges[0].edge_type == EdgeType.OWNS_IDENTIFIER
    assert ident_edges[0].source_id == str(party.party_id)
    assert ident_edges[0].target_id == ident.normalize()

    node = party_to_node(party)
    subgraph = TransactionSubgraph(
        center_node_id=str(party.party_id),
        nodes=[node],
        edges=ident_edges,
    )
    assert subgraph.center_node_id == str(party.party_id)
    assert len(subgraph.nodes) == 1
    assert len(subgraph.edges) == 1
