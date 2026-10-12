"""Unit tests for the Transaction aggregate model and invariant validators."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from ripple.domain.errors import InvalidTransactionError
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import Money
from ripple.domain.transaction import (
    IntermediaryHop,
    IntermediaryRole,
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)


def _sample_identifiers() -> tuple[PartyIdentifier, PartyIdentifier]:
    """Helper to generate sender and receiver identifiers."""
    sender_ident = PartyIdentifier(type=IdentifierType.VPA, value="payer@oksbi", network="UPI")
    receiver_ident = PartyIdentifier(type=IdentifierType.VPA, value="payee@okhdfc", network="UPI")
    return sender_ident, receiver_ident


def test_transaction_validators_failure_paths() -> None:
    """Test every invariant validation failure path on Transaction."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident, receiver_ident = _sample_identifiers()
    amount = Money(amount=Decimal("100.00"), currency="USD", scale=2)
    now = datetime.now(UTC)

    # 1. Non-positive amount (zero amount)
    with pytest.raises(InvalidTransactionError, match="strictly positive"):
        Transaction(
            network_transaction_ref="tx-001",
            network="UPI",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=Money(amount=Decimal("0.00"), currency="USD", scale=2),
            initiated_at=now,
            status=TransactionStatus.INITIATED,
            settlement_model=SettlementModel.INSTANT,
        )

    # 2. Non-positive amount (negative amount)
    with pytest.raises(InvalidTransactionError, match="strictly positive"):
        Transaction(
            network_transaction_ref="tx-002",
            network="UPI",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=Money(amount=Decimal("-50.00"), currency="USD", scale=2),
            initiated_at=now,
            status=TransactionStatus.INITIATED,
            settlement_model=SettlementModel.INSTANT,
        )

    # 3. Sender and receiver must differ (duplicate party_id)
    with pytest.raises(InvalidTransactionError, match="Sender ID and receiver ID must differ"):
        Transaction(
            network_transaction_ref="tx-003",
            network="UPI",
            sender_id=sender_id,
            receiver_id=sender_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=now,
            status=TransactionStatus.INITIATED,
            settlement_model=SettlementModel.INSTANT,
        )

    # 4. settled_at preceding initiated_at
    past_time = now - timedelta(minutes=5)
    with pytest.raises(InvalidTransactionError, match="cannot precede initiated_at"):
        Transaction(
            network_transaction_ref="tx-004",
            network="UPI",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=now,
            settled_at=past_time,
            status=TransactionStatus.SETTLED,
            settlement_model=SettlementModel.INSTANT,
        )

    # 5. A SETTLED transaction must have settled_at
    with pytest.raises(InvalidTransactionError, match="must possess a valid settled_at"):
        Transaction(
            network_transaction_ref="tx-005",
            network="UPI",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=now,
            settled_at=None,
            status=TransactionStatus.SETTLED,
            settlement_model=SettlementModel.INSTANT,
        )

    # 6. Reject naive datetimes on initiated_at
    naive_now = datetime.now()
    with pytest.raises(InvalidTransactionError, match="must be a timezone-aware datetime"):
        Transaction(
            network_transaction_ref="tx-006",
            network="UPI",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=naive_now,
            status=TransactionStatus.INITIATED,
            settlement_model=SettlementModel.INSTANT,
        )

    # 7. Duplicate sequence numbers in intermediary hops
    hop_ident = PartyIdentifier(type=IdentifierType.BIC, value="BARCGB22", network="SWIFT")
    hop1 = IntermediaryHop(
        sequence=1, institution_identifier=hop_ident, role=IntermediaryRole.CORRESPONDENT_BANK
    )
    hop2 = IntermediaryHop(
        sequence=1, institution_identifier=hop_ident, role=IntermediaryRole.CORRESPONDENT_BANK
    )
    with pytest.raises(InvalidTransactionError, match="duplicate sequence numbers"):
        Transaction(
            network_transaction_ref="tx-007",
            network="SWIFT",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=now,
            status=TransactionStatus.INITIATED,
            settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
            intermediary_hops=[hop1, hop2],
        )


def test_settlement_latency_computation() -> None:
    """Test computed settlement_latency_seconds property on unsettled vs settled transactions."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident, receiver_ident = _sample_identifiers()
    amount = Money(amount=Decimal("250.00"), currency="USD", scale=2)
    start_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    # Unsettled transaction
    tx_pending = Transaction(
        network_transaction_ref="upi-rrn-001",
        network="UPI",
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_identifier=sender_ident,
        receiver_identifier=receiver_ident,
        amount=amount,
        initiated_at=start_time,
        settled_at=None,
        status=TransactionStatus.PENDING_SETTLEMENT,
        settlement_model=SettlementModel.INSTANT,
    )
    assert tx_pending.settlement_latency_seconds is None

    # Settled transaction after 45.5 seconds
    settle_time = start_time + timedelta(seconds=45, microseconds=500000)
    tx_settled = Transaction(
        network_transaction_ref="upi-rrn-002",
        network="UPI",
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_identifier=sender_ident,
        receiver_identifier=receiver_ident,
        amount=amount,
        initiated_at=start_time,
        settled_at=settle_time,
        status=TransactionStatus.SETTLED,
        settlement_model=SettlementModel.INSTANT,
    )
    assert tx_settled.settlement_latency_seconds == 45.5


def test_transaction_is_final_behavior() -> None:
    """Test that is_final accurately delineates non-terminal vs terminal states."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident, receiver_ident = _sample_identifiers()
    amount = Money(amount=Decimal("10.00"), currency="USD", scale=2)
    now = datetime.now(UTC)

    # Non-terminal statuses
    non_terminal_statuses = [
        TransactionStatus.INITIATED,
        TransactionStatus.PENDING_AUTHORIZATION,
        TransactionStatus.AUTHORIZED,
        TransactionStatus.PENDING_SETTLEMENT,
    ]
    for status in non_terminal_statuses:
        tx = Transaction(
            network_transaction_ref=f"tx-{status.value}",
            network="VISA",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=now,
            status=status,
            settlement_model=SettlementModel.AUTH_CAPTURE,
        )
        assert tx.is_final is False

    # Terminal statuses
    terminal_statuses = [
        TransactionStatus.SETTLED,
        TransactionStatus.CONFIRMED,
        TransactionStatus.FAILED,
        TransactionStatus.REJECTED,
        TransactionStatus.REVERSED,
        TransactionStatus.CANCELLED,
    ]
    for status in terminal_statuses:
        tx = Transaction(
            network_transaction_ref=f"tx-{status.value}",
            network="VISA",
            sender_id=sender_id,
            receiver_id=receiver_id,
            sender_identifier=sender_ident,
            receiver_identifier=receiver_ident,
            amount=amount,
            initiated_at=now,
            settled_at=(
                now if status in {TransactionStatus.SETTLED, TransactionStatus.CONFIRMED} else None
            ),
            status=status,
            settlement_model=SettlementModel.AUTH_CAPTURE,
        )
        assert tx.is_final is True


def test_no_hop_instant_transaction() -> None:
    """Test construction and properties of a UPI-style instant payment (no intermediary hops)."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident = PartyIdentifier(type=IdentifierType.VPA, value="alice@oksbi", network="UPI")
    receiver_ident = PartyIdentifier(type=IdentifierType.VPA, value="bob@okicici", network="UPI")
    t0 = datetime.now(UTC)
    t1 = t0 + timedelta(milliseconds=150)

    tx = Transaction(
        network_transaction_ref="upi_rrn_89213719283",
        network="UPI",
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_identifier=sender_ident,
        receiver_identifier=receiver_ident,
        amount=Money(amount=Decimal("1500.00"), currency="INR", scale=2),
        initiated_at=t0,
        settled_at=t1,
        status=TransactionStatus.SETTLED,
        settlement_model=SettlementModel.INSTANT,
        direction=TransactionDirection.TRANSFER,
        is_reversible=False,
        channel="mobile",
        intermediary_hops=[],
    )

    assert tx.network == "UPI"
    assert tx.settlement_model == SettlementModel.INSTANT
    assert len(tx.intermediary_hops) == 0
    assert tx.is_reversible is False
    assert tx.settlement_latency_seconds == pytest.approx(0.15, abs=0.01)
    assert tx.is_final is True


def test_multi_hop_correspondent_transaction() -> None:
    """Test construction of a SWIFT cross-border wire traversing correspondent banks with fees."""
    sender_id = uuid4()
    receiver_id = uuid4()
    sender_ident = PartyIdentifier(
        type=IdentifierType.IBAN, value="GB82WEST12345698765432", network="SWIFT"
    )
    receiver_ident = PartyIdentifier(
        type=IdentifierType.BANK_ACCOUNT, value="0011223344", network="SWIFT"
    )

    start_time = datetime(2026, 3, 1, 9, 0, 0, tzinfo=UTC)
    hop1_time = start_time + timedelta(hours=2)
    hop2_time = start_time + timedelta(hours=6)
    settle_time = start_time + timedelta(hours=24)

    intermediary_1 = IntermediaryHop(
        sequence=1,
        institution_identifier=PartyIdentifier(
            type=IdentifierType.BIC, value="CHASUS33XXX", network="SWIFT"
        ),
        role=IntermediaryRole.CORRESPONDENT_BANK,
        received_at=hop1_time,
        fee=Money(amount=Decimal("25.00"), currency="USD", scale=2),
    )
    intermediary_2 = IntermediaryHop(
        sequence=2,
        institution_identifier=PartyIdentifier(
            type=IdentifierType.BIC, value="CITIUS33XXX", network="SWIFT"
        ),
        role=IntermediaryRole.CORRESPONDENT_BANK,
        received_at=hop2_time,
        fee=Money(amount=Decimal("15.00"), currency="USD", scale=2),
    )

    tx = Transaction(
        network_transaction_ref="UETR-f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
        network="SWIFT",
        sender_id=sender_id,
        receiver_id=receiver_id,
        sender_identifier=sender_ident,
        receiver_identifier=receiver_ident,
        amount=Money(amount=Decimal("100000.00"), currency="USD", scale=2),
        fee=Money(amount=Decimal("40.00"), currency="USD", scale=2),
        initiated_at=start_time,
        settled_at=settle_time,
        status=TransactionStatus.SETTLED,
        settlement_model=SettlementModel.CORRESPONDENT_CHAIN,
        direction=TransactionDirection.TRANSFER,
        is_reversible=True,
        reversal_window_ends_at=settle_time + timedelta(days=7),
        intermediary_hops=[intermediary_1, intermediary_2],
        channel="api",
        purpose_code="BEXP",
    )

    assert tx.settlement_model == SettlementModel.CORRESPONDENT_CHAIN
    assert len(tx.intermediary_hops) == 2
    assert tx.is_reversible is True
    assert tx.settlement_latency_seconds == 86400.0
    assert tx.intermediary_hops[0].fee is not None
    assert tx.intermediary_hops[0].fee.amount == Decimal("25.00")
    assert tx.intermediary_hops[1].fee is not None
    assert tx.intermediary_hops[1].fee.amount == Decimal("15.00")
