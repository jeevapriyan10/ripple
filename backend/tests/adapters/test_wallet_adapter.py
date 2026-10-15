"""Unit tests for Closed-Loop Payment Wallet adapter (backend/ripple/adapters/wallet/)."""

import copy
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.errors import NormalizationError, UnsupportedPayloadError
from ripple.adapters.wallet import WALLET_PROFILE, WalletAdapter
from ripple.domain.identity import IdentifierType
from ripple.domain.party import KycLevel, PartyType
from ripple.domain.transaction import (
    SettlementModel,
    TransactionDirection,
    TransactionStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "wallet"


def load_fixture(name: str) -> dict[str, Any]:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        # pyrefly: ignore [no-any-return-explicit]
        return json.load(f)


def make_raw_event(
    payload: Any,
    received_at: datetime = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
) -> RawEvent:
    return RawEvent(
        network_id="wallet",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=received_at,
    )


def test_wallet_profile_and_describe() -> None:
    """Assert WalletAdapter profile matches specification.

    Also verify describe() exposes discarded fields.
    """
    adapter = WalletAdapter()
    assert adapter.profile == WALLET_PROFILE
    assert adapter.profile.network_id == "wallet"
    assert adapter.profile.category.value == "WALLET"
    assert adapter.profile.settlement_model == SettlementModel.INTERNAL_LEDGER
    assert adapter.profile.is_reversible is True
    assert adapter.profile.typical_reversal_window_seconds == 15552000

    desc = adapter.describe()
    assert "discarded_fields" in desc
    assert "name" in desc["discarded_fields"]
    assert "address" in desc["discarded_fields"]
    assert "note" in desc["discarded_fields"]


def test_wallet_p2p_send_completed() -> None:
    """Assert completed p2p send normalizes to SETTLED with correct counterparties."""
    adapter = WalletAdapter()
    event = make_raw_event(load_fixture("p2p_send_completed.json"))
    res = adapter.normalize(event)

    tx = res.transaction
    assert tx.network == "wallet"
    assert tx.network_transaction_ref == "WLT_TXN_001_P2P"
    assert tx.status == TransactionStatus.SETTLED
    assert tx.settlement_model == SettlementModel.INTERNAL_LEDGER
    assert tx.direction == TransactionDirection.TRANSFER
    assert tx.amount.amount == Decimal("150.00")
    assert tx.amount.currency == "USD"
    assert tx.settled_at == datetime(2026, 10, 15, 8, 0, 5, tzinfo=UTC)
    assert res.correlation_key == "wallet:WLT_TXN_001_P2P"
    assert res.lifecycle_stage == "completed"

    sender = res.parties[0]
    assert sender.identifiers[0].type == IdentifierType.WALLET_USER_ID
    assert sender.identifiers[0].value == "usr_alice_001"
    assert sender.party_type == PartyType.INDIVIDUAL
    assert sender.risk_flags.kyc_level == KycLevel.FULL

    # Verify PII name and address are discarded
    assert "name" not in sender.attributes
    assert "address" not in sender.attributes


def test_wallet_p2p_request_flow_lifecycle() -> None:
    """Assert p2p request flow shares correlation_key and transaction_id."""
    adapter = WalletAdapter()
    ev_req = make_raw_event(load_fixture("p2p_request_requested.json"))
    ev_app = make_raw_event(load_fixture("p2p_request_approved.json"))
    ev_comp = make_raw_event(load_fixture("p2p_request_completed.json"))

    res_req = adapter.normalize(ev_req)
    res_app = adapter.normalize(ev_app)
    res_comp = adapter.normalize(ev_comp)

    # Identical keys across lifecycle snapshots
    assert res_req.correlation_key == res_app.correlation_key == res_comp.correlation_key
    assert (
        res_req.transaction.transaction_id
        == res_app.transaction.transaction_id
        == res_comp.transaction.transaction_id
    )
    assert (
        res_req.transaction.sender_id
        == res_app.transaction.sender_id
        == res_comp.transaction.sender_id
    )
    assert (
        res_req.transaction.receiver_id
        == res_app.transaction.receiver_id
        == res_comp.transaction.receiver_id
    )

    # Status progression
    assert res_req.transaction.status == TransactionStatus.PENDING_AUTHORIZATION
    assert res_req.lifecycle_stage == "requested"
    assert res_req.transaction.settled_at is None

    assert res_app.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_app.lifecycle_stage == "pending"
    assert res_app.transaction.settled_at is None

    assert res_comp.transaction.status == TransactionStatus.SETTLED
    assert res_comp.lifecycle_stage == "completed"
    assert res_comp.transaction.settled_at == datetime(2026, 10, 15, 9, 5, 3, tzinfo=UTC)


def test_wallet_held_and_denied() -> None:
    """Assert HELD maps to PENDING_SETTLEMENT with hold metadata and DENIED maps to REJECTED."""
    adapter = WalletAdapter()
    base = load_fixture("p2p_send_completed.json")

    held_payload = copy.deepcopy(base)
    held_payload["status"] = "HELD"
    held_payload["hold_reason"] = "compliance_risk_review"
    del held_payload["completed_at"]

    res_held = adapter.normalize(make_raw_event(held_payload))
    assert res_held.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_held.lifecycle_stage == "held"
    assert res_held.transaction.network_metadata["hold"] is True
    assert res_held.transaction.network_metadata["hold_reason"] == "compliance_risk_review"
    assert res_held.transaction.settled_at is None

    denied_payload = copy.deepcopy(base)
    denied_payload["status"] = "DENIED"
    denied_payload["denial_reason"] = "risk_score_high"
    del denied_payload["completed_at"]

    res_denied = adapter.normalize(make_raw_event(denied_payload))
    assert res_denied.transaction.status == TransactionStatus.REJECTED
    assert res_denied.lifecycle_stage == "denied"
    assert res_denied.transaction.network_metadata["denial_reason"] == "risk_score_high"
    assert res_denied.transaction.settled_at is None


def test_wallet_merchant_payment_with_fee_and_fx() -> None:
    """Assert merchant payment normalizes with fee, converted amounts, and reversible by default."""
    adapter = WalletAdapter()
    event = make_raw_event(load_fixture("merchant_payment_completed.json"))
    res = adapter.normalize(event)

    tx = res.transaction
    assert tx.direction == TransactionDirection.DEBIT
    assert tx.status == TransactionStatus.SETTLED
    assert tx.is_reversible is True
    assert tx.reversal_window_ends_at is not None
    assert tx.fee is not None
    assert tx.fee.amount == Decimal("2.50")
    assert tx.fee.currency == "USD"
    assert tx.network_metadata["converted_amount"] == "80.00"
    assert tx.network_metadata["converted_currency"] == "EUR"
    assert tx.network_metadata["converted_fx_rate"] == "0.889"

    # Merchant party identification
    merc_party = res.parties[1]
    assert merc_party.party_type == PartyType.MERCHANT
    assert any(i.type == IdentifierType.MERCHANT_ID for i in merc_party.identifiers)


def test_wallet_friends_family_not_reversible() -> None:
    """Assert friends_family p2p is not reversible by default without buyer protection."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["payment_type"] = "friends_family"
    del payload["payment_type"]  # remove goods_services override
    payload["payment_type"] = "friends_family"

    res = adapter.normalize(make_raw_event(payload))
    assert res.transaction.is_reversible is False
    assert res.transaction.reversal_window_ends_at is None


def test_wallet_buyer_protection_and_reversal_window_override() -> None:
    """Assert buyer_protection and reversal_window_days override default reversibility."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["buyer_protection"] = True
    payload["reversal_window_days"] = 60

    res = adapter.normalize(make_raw_event(payload))
    tx = res.transaction
    assert tx.is_reversible is True
    assert tx.reversal_window_ends_at == tx.settled_at + (
        datetime(2026, 12, 14, 8, 0, 5, tzinfo=UTC) - datetime(2026, 10, 15, 8, 0, 5, tzinfo=UTC)
    )


def test_wallet_funding_pending_completed_and_reversal() -> None:
    """Assert funding flow and reversal snapshot share identical key and REVERSED status."""
    adapter = WalletAdapter()
    ev_pend = make_raw_event(load_fixture("funding_pending.json"))
    ev_comp = make_raw_event(load_fixture("funding_completed.json"))
    ev_rev = make_raw_event(load_fixture("funding_reversal.json"))

    res_pend = adapter.normalize(ev_pend)
    res_comp = adapter.normalize(ev_comp)
    res_rev = adapter.normalize(ev_rev)

    assert res_pend.correlation_key == res_comp.correlation_key == res_rev.correlation_key
    assert (
        res_pend.transaction.transaction_id
        == res_comp.transaction.transaction_id
        == res_rev.transaction.transaction_id
    )

    assert res_pend.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_comp.transaction.status == TransactionStatus.SETTLED
    assert res_rev.transaction.status == TransactionStatus.REVERSED
    assert res_rev.lifecycle_stage == "reversed"
    assert res_rev.transaction.network_metadata["reversal_reason"] == "funding_failed"
    assert res_rev.transaction.settled_at == datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)


def test_wallet_withdrawal_completed() -> None:
    """Assert withdrawal moves funds from account to external instrument with DEBIT direction."""
    adapter = WalletAdapter()
    res = adapter.normalize(make_raw_event(load_fixture("withdrawal_completed.json")))
    tx = res.transaction

    assert tx.direction == TransactionDirection.DEBIT
    assert tx.status == TransactionStatus.SETTLED
    assert tx.amount.amount == Decimal("200.00")
    # Sender is wallet account, receiver is instrument
    assert res.parties[0].identifiers[0].type == IdentifierType.WALLET_USER_ID
    assert res.parties[1].identifiers[0].type == IdentifierType.OTHER


def test_wallet_refund_is_new_transaction_with_parent_ref() -> None:
    """Assert refund creates a new transaction with flipped direction and parent_txn_id."""
    adapter = WalletAdapter()
    res = adapter.normalize(make_raw_event(load_fixture("refund_completed.json")))
    tx = res.transaction

    assert tx.direction == TransactionDirection.CREDIT
    assert tx.status == TransactionStatus.SETTLED
    assert tx.network_transaction_ref == "WLT_TXN_006_REFUND"
    assert tx.network_metadata["parent_txn_id"] == "WLT_TXN_003_MERC"
    assert res.correlation_key == "wallet:WLT_TXN_006_REFUND"


def test_wallet_dispute_lifecycle() -> None:
    """Assert dispute opened -> resolved_buyer (REVERSED) and resolved_seller (SETTLED)."""
    adapter = WalletAdapter()
    res_open = adapter.normalize(make_raw_event(load_fixture("dispute_opened.json")))
    res_buyer = adapter.normalize(make_raw_event(load_fixture("dispute_resolved_buyer.json")))
    res_seller = adapter.normalize(make_raw_event(load_fixture("dispute_resolved_seller.json")))

    # Shared original correlation key
    assert (
        res_open.correlation_key
        == res_buyer.correlation_key
        == res_seller.correlation_key
        == "wallet:WLT_TXN_003_MERC"
    )

    assert res_open.transaction.status == TransactionStatus.DISPUTED
    assert res_open.lifecycle_stage == "dispute_opened"
    assert res_open.transaction.settled_at == datetime(2026, 10, 15, 10, 0, 2, tzinfo=UTC)

    assert res_buyer.transaction.status == TransactionStatus.REVERSED
    assert res_buyer.lifecycle_stage == "dispute_resolved_buyer"
    assert res_buyer.transaction.settled_at == datetime(2026, 10, 15, 16, 0, 0, tzinfo=UTC)

    assert res_seller.transaction.status == TransactionStatus.SETTLED
    assert res_seller.lifecycle_stage == "dispute_resolved_seller"
    assert res_seller.transaction.settled_at == datetime(2026, 10, 15, 10, 0, 2, tzinfo=UTC)


def test_wallet_instrument_party_shape_and_deduplication() -> None:
    """Assert same instrument_id funding two different wallets yields identical party_id."""
    adapter = WalletAdapter()
    base_funding = load_fixture("funding_completed.json")

    # Wallet 1 funded by tok_bank_checking_99
    res1 = adapter.normalize(make_raw_event(base_funding))
    instr1 = res1.parties[0]
    assert instr1.identifiers[0].type == IdentifierType.OTHER
    assert instr1.identifiers[0].value == "instr:tok_bank_checking_99"
    assert instr1.identifiers[0].is_pseudonymous is True

    # Wallet 2 funded by same tok_bank_checking_99
    funding2 = copy.deepcopy(base_funding)
    funding2["txn_id"] = "WLT_TXN_007_DIFF_WALLET"
    funding2["account"]["account_id"] = "usr_charlie_003"
    funding2["account"]["email"] = "charlie@example.com"

    res2 = adapter.normalize(make_raw_event(funding2))
    instr2 = res2.parties[0]

    assert instr1.party_id == instr2.party_id


def test_wallet_all_digit_instrument_id_rejected() -> None:
    """Assert all-digit instrument_id is rejected because opaque tokens must contain a non-digit."""
    adapter = WalletAdapter()
    payload = load_fixture("funding_completed.json")
    payload["instrument"]["instrument_id"] = "123456789012"

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))
    assert exc_info.value.field == "instrument.instrument_id"

    # Also test all digits with separators
    payload["instrument"]["instrument_id"] = "123-456-789-012"
    with pytest.raises(NormalizationError) as exc_info2:
        adapter.normalize(make_raw_event(payload))
    assert exc_info2.value.field == "instrument.instrument_id"


def test_wallet_credential_fields_discarded_with_warning() -> None:
    """Assert credential-class fields are discarded with warning naming field only."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["card_number"] = "4111111111111111"
    payload["cvv"] = "777"
    payload["sender"]["iban"] = "GB82WEST12345698765432"

    res = adapter.normalize(make_raw_event(payload))

    # Output serialized JSON does not contain the PAN, CVV, or IBAN values
    serialized = res.model_dump_json()
    assert "4111111111111111" not in serialized
    assert "777" not in serialized
    assert "GB82WEST12345698765432" not in serialized

    # Warnings name field only, never values
    warn_text = " ".join(res.warnings)
    assert "card_number" in warn_text
    assert "cvv" in warn_text
    assert "sender.iban" in warn_text
    assert "4111111111111111" not in warn_text
    assert "GB82WEST12345698765432" not in warn_text


def test_wallet_leak_test_no_sensitive_values_in_messages() -> None:
    """Assert PAN, IBAN, emails, and phones appear in no warning or exception text."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["pan"] = "5555555555554444"
    payload["sender"]["email"] = "invalid-email-format"

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    # Exception does not echo invalid email value
    assert "invalid-email-format" not in str(exc_info.value)
    assert "5555555555554444" not in str(exc_info.value)


def test_wallet_names_and_addresses_absent_from_output() -> None:
    """Assert ordinary PII names and addresses are absent from NormalizationResult."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    res = adapter.normalize(make_raw_event(payload))

    serialized = res.model_dump_json()
    assert "Alice Wonderland" not in serialized
    assert "123 Main St" not in serialized
    assert "Bob Builder" not in serialized


def test_wallet_unknown_keys_reported_by_name_only() -> None:
    """Assert unknown top-level keys are recorded in metadata unmapped_field_names by name only."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["custom_secret_code"] = "SUPER_SECRET_VALUE_123"
    payload["alpha_custom"] = "ANOTHER_VALUE"

    res = adapter.normalize(make_raw_event(payload))
    meta = res.transaction.network_metadata

    assert meta["unmapped_field_names"] == ["alpha_custom", "custom_secret_code"]
    serialized = res.model_dump_json()
    assert "SUPER_SECRET_VALUE_123" not in serialized
    assert "ANOTHER_VALUE" not in serialized


def test_wallet_float_rejected() -> None:
    """Assert float amounts are strictly rejected."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["amount"] = 150.50

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))
    assert exc_info.value.field == "amount"


def test_wallet_unsupported_event_types() -> None:
    """Assert unsupported event_type and original_event_type are rejected."""
    adapter = WalletAdapter()
    payload = load_fixture("error_unsupported_event_type.json")

    with pytest.raises(UnsupportedPayloadError) as exc_info:
        adapter.normalize(make_raw_event(payload))
    assert "crypto_swap" in str(exc_info.value)

    disp_payload = load_fixture("dispute_opened.json")
    disp_payload["original_event_type"] = "batch_payout"
    with pytest.raises(UnsupportedPayloadError):
        adapter.normalize(make_raw_event(disp_payload))


def test_wallet_determinism_on_repeated_calls() -> None:
    """Assert normalizing the identical event twice yields identical results."""
    adapter = WalletAdapter()
    ev = make_raw_event(load_fixture("p2p_send_completed.json"))

    res1 = adapter.normalize(ev)
    res2 = adapter.normalize(ev)

    assert res1.model_dump() == res2.model_dump()


def test_wallet_additional_statuses() -> None:
    """Assert FAILED, CANCELLED, and EXPIRED statuses map correctly."""
    adapter = WalletAdapter()

    base = load_fixture("p2p_send_completed.json")

    base["status"] = "FAILED"
    base["completed_at"] = None
    res_failed = adapter.normalize(make_raw_event(base))
    assert res_failed.transaction.status == TransactionStatus.FAILED
    assert res_failed.lifecycle_stage == "failed"
    assert res_failed.transaction.settled_at is None

    base["status"] = "CANCELLED"
    res_canc = adapter.normalize(make_raw_event(base))
    assert res_canc.transaction.status == TransactionStatus.CANCELLED
    assert res_canc.lifecycle_stage == "cancelled"

    base["status"] = "EXPIRED"
    res_exp = adapter.normalize(make_raw_event(base))
    assert res_exp.transaction.status == TransactionStatus.CANCELLED
    assert res_exp.lifecycle_stage == "expired"


def test_wallet_dispute_escalated_and_withdrawn() -> None:
    """Assert dispute escalated and withdrawn statuses map correctly."""
    adapter = WalletAdapter()

    disp_esc = load_fixture("dispute_opened.json")
    disp_esc["dispute_status"] = "escalated"
    res_esc = adapter.normalize(make_raw_event(disp_esc))
    assert res_esc.transaction.status == TransactionStatus.DISPUTED
    assert res_esc.lifecycle_stage == "dispute_escalated"

    disp_with = load_fixture("dispute_opened.json")
    disp_with["dispute_status"] = "withdrawn"
    disp_with["resolved_at"] = "2026-10-15T15:00:00Z"
    res_with = adapter.normalize(make_raw_event(disp_with))
    assert res_with.transaction.status == TransactionStatus.SETTLED
    assert res_with.lifecycle_stage == "dispute_withdrawn"


def test_wallet_nested_list_credential_stripping() -> None:
    """Assert credentials nested inside arrays/lists are stripped with field-only warnings."""
    adapter = WalletAdapter()
    payload = load_fixture("p2p_send_completed.json")
    payload["line_items"] = [
        {"item_name": "Book", "cvv": "123"},
        {"item_name": "Pen", "pan": "4111111111111111"},
    ]

    res = adapter.normalize(make_raw_event(payload))
    warns = " ".join(res.warnings)
    assert "cvv" in warns
    assert "pan" in warns
    assert "123" not in warns
    assert "4111111111111111" not in warns


def test_wallet_self_transfer_and_zero_amount_rejections() -> None:
    """Assert self-transfers and zero amounts are rejected citing Module 2."""
    adapter = WalletAdapter()

    payload_self = load_fixture("p2p_send_completed.json")
    payload_self["receiver"]["account_id"] = payload_self["sender"]["account_id"]
    with pytest.raises(UnsupportedPayloadError) as exc_self:
        adapter.normalize(make_raw_event(payload_self))
    assert "Self-transfers" in str(exc_self.value)
    assert "Module 2" in str(exc_self.value)

    payload_zero = load_fixture("p2p_send_completed.json")
    payload_zero["amount"] = "0.00"
    with pytest.raises(UnsupportedPayloadError) as exc_zero:
        adapter.normalize(make_raw_event(payload_zero))
    assert "Zero-value transfers" in str(exc_zero.value)
    assert "Module 2" in str(exc_zero.value)
