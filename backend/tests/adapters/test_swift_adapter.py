"""Unit tests for SWIFT Correspondent Banking network adapter (backend/ripple/adapters/swift/)."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from ripple.adapters.contract import PayloadFormat, RawEvent
from ripple.adapters.errors import NormalizationError, UnsupportedPayloadError
from ripple.adapters.swift import SWIFT_PROFILE, SwiftAdapter
from ripple.domain.graph import transaction_to_edges
from ripple.domain.identity import IdentifierType
from ripple.domain.transaction import (
    IntermediaryRole,
    SettlementModel,
    TransactionDirection,
    TransactionStatus,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "swift"


def load_fixture(name: str) -> dict[str, Any]:
    with open(FIXTURES_DIR / name, encoding="utf-8") as f:
        # pyrefly: ignore [no-any-return-explicit]
        return json.load(f)


def make_raw_event(
    payload: Any,
    received_at: datetime = datetime(2026, 10, 14, 10, 0, 0, tzinfo=UTC),
) -> RawEvent:
    return RawEvent(
        network_id="swift",
        payload=payload,
        payload_format=PayloadFormat.JSON,
        received_at=received_at,
    )


def test_swift_adapter_profile_and_describe() -> None:
    """Assert SwiftAdapter profile matches specification and describe() declares fields."""
    adapter = SwiftAdapter()
    assert adapter.profile == SWIFT_PROFILE
    assert adapter.profile.network_id == "swift"
    assert adapter.profile.settlement_model == SettlementModel.CORRESPONDENT_CHAIN
    assert adapter.profile.intermediaries_visible is True
    assert adapter.profile.is_reversible is False

    desc = adapter.describe()
    assert "discarded_fields" in desc
    assert "debtor.name" in desc["discarded_fields"]
    assert "debtor.address" in desc["discarded_fields"]
    assert "remittance_info" in desc["discarded_fields"]
    assert "supported_recall_reasons" in desc


def test_swift_payment_instruction_maps_to_initiated() -> None:
    """Assert payment event without status maps to INITIATED with lifecycle_stage 'instruction'."""
    adapter = SwiftAdapter()
    ev = make_raw_event(load_fixture("payment_instruction.json"))
    res = adapter.normalize(ev)

    tx = res.transaction
    assert tx.status == TransactionStatus.INITIATED
    assert res.lifecycle_stage == "instruction"
    assert tx.settled_at is None
    assert tx.initiated_at == datetime(2026, 10, 14, 9, 0, 0, tzinfo=UTC)
    assert tx.amount.currency == "EUR"
    assert tx.amount.amount == Decimal("50000.00")
    assert tx.amount.scale == 2
    assert tx.settlement_model == SettlementModel.CORRESPONDENT_CHAIN
    assert tx.direction == TransactionDirection.TRANSFER
    assert tx.purpose_code == "CORT"


def test_swift_full_gpi_progression_actc_acsp_accc() -> None:
    """Assert gpi progression ACTC -> ACSP -> ACCC preserves correlation_key and transaction_id."""
    adapter = SwiftAdapter()

    # 1. ACTC (Accepted Technical Validation)
    res_actc = adapter.normalize(make_raw_event(load_fixture("status_actc.json")))
    assert res_actc.transaction.status == TransactionStatus.PENDING_AUTHORIZATION
    assert res_actc.lifecycle_stage == "status_actc"
    assert res_actc.transaction.settled_at is None

    # 2. ACSP (Accepted Settlement in Process)
    res_acsp = adapter.normalize(make_raw_event(load_fixture("status_acsp.json")))
    assert res_acsp.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_acsp.lifecycle_stage == "status_acsp"
    assert res_acsp.transaction.settled_at is None

    # 3. ACCC (Accepted Completed Settlement)
    res_accc = adapter.normalize(make_raw_event(load_fixture("status_accc.json")))
    assert res_accc.transaction.status == TransactionStatus.SETTLED
    assert res_accc.lifecycle_stage == "status_accc"
    assert res_accc.transaction.settled_at == datetime(2026, 10, 14, 11, 0, 0, tzinfo=UTC)

    # Identical correlation_key and transaction_id across all three events
    assert res_actc.correlation_key == res_acsp.correlation_key == res_accc.correlation_key
    assert (
        res_actc.transaction.transaction_id
        == res_acsp.transaction.transaction_id
        == res_accc.transaction.transaction_id
    )
    assert res_actc.correlation_key == "swift:eb92b1a0-1234-4567-89ab-cdef01234567"


def test_swift_hop_order_and_transaction_to_edges_decomposition() -> None:
    """Assert hops are ordered DEBTOR_AGENT -> CORRESPONDENT -> CREDITOR_AGENT (N+1 edges)."""
    adapter = SwiftAdapter()
    res = adapter.normalize(make_raw_event(load_fixture("payment_instruction.json")))
    tx = res.transaction

    # Hop ordering: seq 1 (debtor agent) -> seq 2 (intermediary) -> seq 3 (creditor agent)
    assert len(tx.intermediary_hops) == 3
    h1, h2, h3 = tx.intermediary_hops

    assert h1.sequence == 1
    assert h1.role == IntermediaryRole.DEBTOR_AGENT
    assert h1.institution_identifier.value == "COBADEFF"

    assert h2.sequence == 2
    assert h2.role == IntermediaryRole.CORRESPONDENT_BANK
    assert h2.institution_identifier.value == "CHASUS33"

    assert h3.sequence == 3
    assert h3.role == IntermediaryRole.CREDITOR_AGENT
    assert h3.institution_identifier.value == "WESTGB2L"

    # Multi-hop graph decomposition via transaction_to_edges yields N+1 (4) sequential edges
    edges = transaction_to_edges(tx)
    assert len(edges) == 4
    # Edge 1: Sender -> Debtor Agent
    assert edges[0].source_id == str(tx.sender_id)
    assert edges[0].target_id == "COBADEFF"
    # Edge 2: Debtor Agent -> Intermediary
    assert edges[1].source_id == "COBADEFF"
    assert edges[1].target_id == "CHASUS33"
    assert edges[1].properties["role"] == IntermediaryRole.DEBTOR_AGENT.value
    # Edge 3: Intermediary -> Creditor Agent
    assert edges[2].source_id == "CHASUS33"
    assert edges[2].target_id == "WESTGB2L"
    assert edges[2].properties["role"] == IntermediaryRole.CORRESPONDENT_BANK.value
    # Edge 4: Creditor Agent -> Receiver
    assert edges[3].source_id == "WESTGB2L"
    assert edges[3].target_id == str(tx.receiver_id)
    assert edges[3].properties["role"] == IntermediaryRole.CREDITOR_AGENT.value


def test_swift_per_hop_charges_summed_same_currency_and_mixed_currency_warning() -> None:
    """Assert per-hop charges are summed if same currency, or None with warning if mixed."""
    adapter = SwiftAdapter()

    # Case 1: Same currency (EUR: 15.00 + 25.00 + 10.00 = 50.00)
    res_same = adapter.normalize(make_raw_event(load_fixture("payment_instruction.json")))
    assert res_same.transaction.fee is not None
    assert res_same.transaction.fee.currency == "EUR"
    assert res_same.transaction.fee.amount == Decimal("50.00")
    assert res_same.transaction.fee.scale == 2

    # Case 2: Mixed currencies among charges
    payload_mixed = load_fixture("payment_instruction.json")
    payload_mixed["intermediaries"][0]["charge"] = {"amount": "30.00", "currency": "USD"}
    res_mixed = adapter.normalize(make_raw_event(payload_mixed))

    assert res_mixed.transaction.fee is None
    assert any("charges span multiple currencies" in w for w in res_mixed.warnings)
    # Per-hop charges are still preserved individually
    # pyrefly: ignore [missing-attribute]
    assert res_mixed.transaction.intermediary_hops[0].fee.currency == "EUR"
    # pyrefly: ignore [missing-attribute]
    assert res_mixed.transaction.intermediary_hops[1].fee.currency == "USD"
    # pyrefly: ignore [missing-attribute]
    assert res_mixed.transaction.intermediary_hops[2].fee.currency == "EUR"


def test_swift_fx_case_and_metadata() -> None:
    """Assert FX metadata (instructed amount, exchange_rate, charge_bearer) is preserved."""
    adapter = SwiftAdapter()
    res = adapter.normalize(make_raw_event(load_fixture("payment_instruction.json")))
    meta = res.transaction.network_metadata

    assert meta["exchange_rate"] == "1.10"
    assert meta["charge_bearer"] == "SHA"
    assert meta["instructed_amount"] == {"amount": "55000.00", "currency": "USD"}
    assert meta["value_date"] == "2026-10-14"
    assert meta["message_type"] == "pacs.008"


def test_swift_zero_scale_jpy_and_three_scale_kwd() -> None:
    """Assert ISO currencies with 0 decimals (JPY) and 3 decimals (KWD) scale accurately."""
    adapter = SwiftAdapter()
    base_payload = load_fixture("status_accc.json")

    # JPY scale 0
    payload_jpy = dict(base_payload, settlement_amount={"amount": "1000000", "currency": "JPY"})
    res_jpy = adapter.normalize(make_raw_event(payload_jpy))
    assert res_jpy.transaction.amount.currency == "JPY"
    assert res_jpy.transaction.amount.scale == 0
    assert res_jpy.transaction.amount.amount == Decimal("1000000")

    # KWD scale 3
    payload_kwd = dict(base_payload, settlement_amount={"amount": "150.375", "currency": "KWD"})
    res_kwd = adapter.normalize(make_raw_event(payload_kwd))
    assert res_kwd.transaction.amount.currency == "KWD"
    assert res_kwd.transaction.amount.scale == 3
    assert res_kwd.transaction.amount.amount == Decimal("150.375")


def test_swift_value_date_fallback_on_accc_and_warnings() -> None:
    """Assert value_date is never used as settled_at except in ACCC fallback, which warns."""
    adapter = SwiftAdapter()

    # 1. In payment_instruction, value_date exists but settled_at is None
    res_inst = adapter.normalize(make_raw_event(load_fixture("payment_instruction.json")))
    assert res_inst.transaction.settled_at is None

    # 2. In ACCC without status_timestamp, value_date at 00:00 UTC is used as fallback with warning
    payload_fallback = load_fixture("status_accc.json")
    payload_fallback.pop("status_timestamp", None)
    payload_fallback["value_date"] = "2026-10-14"

    res_fb = adapter.normalize(make_raw_event(payload_fallback))
    assert res_fb.transaction.status == TransactionStatus.SETTLED
    assert res_fb.transaction.settled_at == datetime(2026, 10, 14, 0, 0, 0, tzinfo=UTC)
    assert any(
        "settlement time approximated using value_date" in w.lower() for w in res_fb.warnings
    )


def test_swift_multi_day_settlement_latency() -> None:
    """Assert multi-day correspondent clearing computes settlement_latency_seconds correctly."""
    adapter = SwiftAdapter()
    payload = load_fixture("status_accc.json")
    payload["creation_datetime"] = "2026-10-12T09:00:00Z"
    payload["status_timestamp"] = "2026-10-14T09:00:00Z"  # 48 hours later = 172800 seconds

    res = adapter.normalize(make_raw_event(payload))
    assert res.transaction.settlement_latency_seconds == 172800.0


def test_swift_rjct_pdng_acsc_recall_and_return_statuses() -> None:
    """Assert RJCT, PDNG, ACSC, recall_request (FRAD), and return events map correctly."""
    adapter = SwiftAdapter()
    base_payload = load_fixture("status_actc.json")

    # RJCT with status_reason_code
    payload_rjct = dict(base_payload, status="RJCT", status_reason_code="AC01")
    res_rjct = adapter.normalize(make_raw_event(payload_rjct))
    assert res_rjct.transaction.status == TransactionStatus.REJECTED
    assert res_rjct.lifecycle_stage == "status_rjct"
    assert res_rjct.transaction.network_metadata["status_reason_code"] == "AC01"

    # PDNG
    payload_pdng = dict(base_payload, status="PDNG")
    res_pdng = adapter.normalize(make_raw_event(payload_pdng))
    assert res_pdng.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_pdng.lifecycle_stage == "status_pdng"
    assert res_pdng.transaction.network_metadata["pending"] is True

    # ACSC (debtor_settled)
    payload_acsc = dict(base_payload, status="ACSC")
    res_acsc = adapter.normalize(make_raw_event(payload_acsc))
    assert res_acsc.transaction.status == TransactionStatus.PENDING_SETTLEMENT
    assert res_acsc.lifecycle_stage == "status_acsc"
    assert res_acsc.transaction.network_metadata["debtor_settled"] is True

    # Recall FRAD -> DISPUTED
    res_recall = adapter.normalize(make_raw_event(load_fixture("recall_frad.json")))
    assert res_recall.transaction.status == TransactionStatus.DISPUTED
    assert res_recall.lifecycle_stage == "recall_requested"
    assert res_recall.transaction.network_metadata["recall_reason"] == "FRAD"
    assert res_recall.transaction.network_metadata["recall_reason_label"] == "fraudulent_origin"

    # Return -> REVERSED
    res_return = adapter.normalize(make_raw_event(load_fixture("return_payment.json")))
    assert res_return.transaction.status == TransactionStatus.REVERSED
    assert res_return.lifecycle_stage == "returned"
    assert res_return.transaction.settled_at == datetime(2026, 10, 14, 16, 0, 0, tzinfo=UTC)
    assert res_return.transaction.network_metadata["return_reason"] == "AC04"


def test_swift_missing_uetr_fallback_correlation_key() -> None:
    """Assert missing UETR falls back to key: swift:ref:sender_bic:sender_reference:value_date."""
    adapter = SwiftAdapter()
    payload = load_fixture("payment_instruction.json")
    payload.pop("uetr", None)
    payload["sender_reference"] = "REF-2026-X99"
    payload["value_date"] = "2026-10-14"

    res = adapter.normalize(make_raw_event(payload))
    expected_key = "swift:ref:COBADEFF:REF-2026-X99:2026-10-14"
    assert res.correlation_key == expected_key
    assert res.transaction.network_transaction_ref == "REF-2026-X99"


def test_swift_invalid_uetr_and_invalid_bic_rejections() -> None:
    """Assert invalid UETR or BIC raises NormalizationError without echoing values."""
    adapter = SwiftAdapter()
    base_payload = load_fixture("payment_instruction.json")

    # Invalid UETR
    payload_bad_uetr = dict(base_payload, uetr="not-a-valid-uuid-format")
    with pytest.raises(NormalizationError) as exc_uetr:
        adapter.normalize(make_raw_event(payload_bad_uetr))
    assert exc_uetr.value.field == "uetr"

    # Invalid BIC (too short)
    payload_bad_bic = dict(base_payload, debtor_agent={"bic": "BADBIC"})
    with pytest.raises(NormalizationError) as exc_bic:
        adapter.normalize(make_raw_event(payload_bad_bic))
    # pyrefly: ignore [not-iterable]
    assert "debtor_agent.bic" in exc_bic.value.field
    assert "BADBIC" not in str(exc_bic.value)


def test_swift_non_iban_account_with_agent_bic() -> None:
    """Assert non-IBAN account number combines with agent BIC into BANK_ACCOUNT identifier."""
    adapter = SwiftAdapter()
    payload = load_fixture("payment_instruction.json")
    payload["debtor"]["account"] = {"number": "9876543210"}
    payload["debtor"]["country"] = "DE"

    res = adapter.normalize(make_raw_event(payload))
    sender_ident = res.transaction.sender_identifier
    assert sender_ident.type == IdentifierType.BANK_ACCOUNT
    assert sender_ident.value == "COBADEFF:9876543210"
    assert sender_ident.network == "swift"


def test_swift_pacs009_mt202_and_raw_xml_rejected() -> None:
    """Assert FI-to-FI message types and raw XML are rejected as unsupported."""
    adapter = SwiftAdapter()
    base_payload = load_fixture("payment_instruction.json")

    # pacs.009
    payload_pacs009 = dict(base_payload, message_type="pacs.009")
    with pytest.raises(UnsupportedPayloadError) as exc_pacs:
        adapter.normalize(make_raw_event(payload_pacs009))
    assert "FI-to-FI transfers" in str(exc_pacs.value)

    # MT202
    payload_mt202 = dict(base_payload, message_type="MT202")
    with pytest.raises(UnsupportedPayloadError) as exc_mt:
        adapter.normalize(make_raw_event(payload_mt202))
    assert "FI-to-FI transfers" in str(exc_mt.value)

    # Raw XML string
    raw_xml = "<?xml version='1.0'?><Document><pacs.008>...</pacs.008></Document>"
    with pytest.raises(UnsupportedPayloadError) as exc_xml:
        adapter.normalize(make_raw_event(raw_xml))
    assert "Raw XML" in str(exc_xml.value)


def test_swift_same_debtor_and_creditor_rejected() -> None:
    """Assert debtor and creditor with identical primary identifier are rejected."""
    adapter = SwiftAdapter()
    payload = load_fixture("payment_instruction.json")
    payload["creditor"]["account"] = payload["debtor"]["account"]

    with pytest.raises(UnsupportedPayloadError) as exc_same:
        adapter.normalize(make_raw_event(payload))
    assert "cannot share the same primary account identifier" in str(exc_same.value)


def test_swift_data_minimization_discarded_fields_absent() -> None:
    """Assert customer personal names, addresses, and remittance are absent from output."""
    adapter = SwiftAdapter()
    ev = make_raw_event(load_fixture("payment_instruction.json"))
    res = adapter.normalize(ev)

    serialized = res.model_dump_json()

    # Discarded personal and address data must NOT appear anywhere in output
    assert "ACME Industrial Corp" not in serialized
    assert "Musterstrasse 1" not in serialized
    assert "John Doe" not in serialized
    assert "10 Downing Street" not in serialized
    assert "Consulting Services" not in serialized

    # Metadata retains only remittance presence flag and length
    assert res.transaction.network_metadata["remittance_info_present"] is True
    assert res.transaction.network_metadata["remittance_info_length"] > 0


def test_swift_iban_leak_security_test() -> None:
    """Security test: assert invalid IBAN never leaks into exception text or reasons."""
    adapter = SwiftAdapter()
    payload = load_fixture("payment_instruction.json")

    # Fabricate a checksum-invalid IBAN
    test_iban = "DE89370400440532013009"  # Checksum corrupt
    payload["debtor"]["account"]["iban"] = test_iban

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    exc_str = str(exc_info.value)
    exc_reason = exc_info.value.reason
    assert "failed ISO 13616 structure or MOD-97 checksum" in exc_str
    # The raw IBAN must NOT appear in the exception string, message, or reason
    assert test_iban not in exc_str
    assert test_iban not in exc_reason


def test_swift_domain_validation_failure_surfaces_as_normalization_error() -> None:
    """Assert domain failure (settled_at < initiated_at) surfaces as NormalizationError."""
    adapter = SwiftAdapter()
    payload = load_fixture("status_accc.json")
    # Set creation_datetime after status_timestamp
    payload["creation_datetime"] = "2026-10-14T12:00:00Z"
    payload["status_timestamp"] = "2026-10-14T09:00:00Z"  # earlier than creation

    with pytest.raises(NormalizationError) as exc_info:
        adapter.normalize(make_raw_event(payload))

    assert exc_info.value.field == "settled_at"
    assert "cannot precede" in exc_info.value.reason


def test_swift_determinism_on_repeated_calls() -> None:
    """Assert normalizing the identical raw SWIFT event twice yields identical output."""
    adapter = SwiftAdapter()
    ev = make_raw_event(load_fixture("payment_instruction.json"))

    res1 = adapter.normalize(ev)
    res2 = adapter.normalize(ev)

    assert res1.transaction.transaction_id == res2.transaction.transaction_id
    assert res1.correlation_key == res2.correlation_key
    assert res1.transaction.amount == res2.transaction.amount
    assert len(res1.parties) == len(res2.parties)
    for p1, p2 in zip(res1.parties, res2.parties, strict=True):
        assert p1.party_id == p2.party_id
