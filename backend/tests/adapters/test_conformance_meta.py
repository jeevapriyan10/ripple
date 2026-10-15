"""Meta-tests for the Adapter Conformance Suite (Part B3).

Verifies the test harness by asserting:
1. A reference compliant adapter passes all 21 conformance checks.
2. Ten deliberately defective adapter variants fail the exact specified checks:
   - (1) uses uuid4 -> CF-04, CF-05, CF-06
   - (2) echoes payload value in NormalizationError -> CF-16, CF-18
   - (3) raises bare KeyError -> CF-16, CF-19
   - (4) mutates input payload -> CF-14
   - (5) stateful counter in output -> CF-04, CF-15
   - (6) float in monetary/metadata path -> CF-12
   - (7) emits naive datetime -> CF-07
   - (8) logs raw payload containing sensitive term -> CF-18
   - (9) unstable transaction_id across group snapshots -> CF-09
   - (10) accepts foreign payloads -> CF-21
"""

from __future__ import annotations

import copy
import logging
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    PayloadFormat,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.errors import (
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.helpers import wrap_domain_errors
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.adapters.testing import (
    ConformanceCase,
    ConformanceOptions,
    run_conformance,
)
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import AssetClass, Money
from ripple.domain.party import Party, PartyType
from ripple.domain.transaction import (
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)

logger = logging.getLogger(__name__)

# --- Base Reference Profile ---
REFERENCE_PROFILE = NetworkProfile(
    network_id="reference",
    display_name="Reference Network",
    description="Compliant reference adapter for conformance meta-testing.",
    category=NetworkCategory.INSTANT_PAYMENT,
    settlement_model=SettlementModel.INSTANT,
    supported_identifier_types=[IdentifierType.WALLET_USER_ID],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=["USD"],
    typical_settlement_seconds_min=1.0,
    typical_settlement_seconds_max=2.0,
    is_reversible=False,
    typical_reversal_window_seconds=None,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone=None,
    typical_fee_bps_range=None,
    regions=[],
    schema_version=1,
)


class ReferenceAdapter(NetworkAdapter):
    """Pure, fully conformant test reference adapter."""

    profile: ClassVar[NetworkProfile] = REFERENCE_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "Reference Adapter"

    def can_handle(self, event: RawEvent) -> bool:
        if event.network_id != "reference" or not isinstance(event.payload, dict):
            return False
        return "txn_id" in event.payload and "amount" in event.payload

    def normalize(self, event: RawEvent) -> NormalizationResult:
        payload = copy.deepcopy(event.payload)
        if not isinstance(payload, dict):
            raise NormalizationError("Payload must be dict.", field="payload", reason="Not dict")

        if "txn_id" not in payload:
            raise NormalizationError("Missing txn_id.", field="txn_id", reason="Missing txn_id")
        if "amount" not in payload:
            raise NormalizationError("Missing amount.", field="amount", reason="Missing amount")
        if "sender_id" not in payload:
            raise NormalizationError(
                "Missing sender_id.", field="sender_id", reason="Missing sender_id"
            )
        if "receiver_id" not in payload:
            raise NormalizationError(
                "Missing receiver_id.", field="receiver_id", reason="Missing receiver_id"
            )

        raw_amt = payload["amount"]
        if isinstance(raw_amt, float):
            raise NormalizationError(
                "Amount cannot be float.", field="amount", reason="Float prohibited"
            )
        try:
            amt = Decimal(str(raw_amt))
        except Exception as exc:
            raise NormalizationError(
                "Invalid amount format.", field="amount", reason="Invalid decimal"
            ) from exc

        if amt == Decimal(0):
            raise UnsupportedPayloadError("Zero amount is unsupported citing Module 2.")
        if amt < Decimal(0):
            raise UnsupportedPayloadError("Negative amount is unsupported.")

        txn_id = str(payload["txn_id"]).strip()
        sender_id = str(payload["sender_id"]).strip()
        receiver_id = str(payload["receiver_id"]).strip()

        if not txn_id:
            raise NormalizationError(
                "Missing or empty txn_id.", field="txn_id", reason="Empty txn_id"
            )
        if not sender_id:
            raise NormalizationError(
                "Missing or empty sender_id.", field="sender_id", reason="Empty sender_id"
            )
        if not receiver_id:
            raise NormalizationError(
                "Missing or empty receiver_id.", field="receiver_id", reason="Empty receiver_id"
            )
        if sender_id == receiver_id:
            raise UnsupportedPayloadError("Self-transfers not allowed citing Module 2.")

        raw_status = str(payload.get("status", "COMPLETED"))
        if raw_status == "COMPLETED":
            st = TransactionStatus.SETTLED
            settled_at = event.received_at
        else:
            st = TransactionStatus.INITIATED
            settled_at = None

        tx_id = deterministic_uuid("reference", txn_id)
        sender_uuid = deterministic_uuid("reference", "user", sender_id)
        receiver_uuid = deterministic_uuid("reference", "user", receiver_id)

        with wrap_domain_errors():
            sender_ident = PartyIdentifier(
                type=IdentifierType.WALLET_USER_ID,
                value=sender_id,
                network="reference",
            )
            receiver_ident = PartyIdentifier(
                type=IdentifierType.WALLET_USER_ID,
                value=receiver_id,
                network="reference",
            )

            sender_party = Party(
                party_id=sender_uuid,
                identifiers=[sender_ident],
                party_type=PartyType.INDIVIDUAL,
                home_network="reference",
                created_at=event.received_at,
            )
            receiver_party = Party(
                party_id=receiver_uuid,
                identifiers=[receiver_ident],
                party_type=PartyType.INDIVIDUAL,
                home_network="reference",
                created_at=event.received_at,
            )

            tx = Transaction(
                transaction_id=tx_id,
                network_transaction_ref=txn_id,
                network="reference",
                sender_id=sender_uuid,
                receiver_id=receiver_uuid,
                sender_identifier=sender_ident,
                receiver_identifier=receiver_ident,
                amount=Money(amount=amt, currency="USD", scale=2),
                initiated_at=event.received_at,
                settled_at=settled_at,
                observed_at=event.received_at,
                status=st,
                settlement_model=SettlementModel.INSTANT,
                direction=TransactionDirection.TRANSFER,
                is_reversible=False,
                intermediary_hops=[],
            )

        return NormalizationResult(
            transaction=tx,
            parties=[sender_party, receiver_party],
            correlation_key=f"reference:{txn_id}",
            lifecycle_stage="completed" if settled_at else "initiated",
            adapter_version=self.adapter_version,
        )


def make_test_cases() -> tuple[list[ConformanceCase], list[ConformanceCase]]:
    """Create test conformance cases for the reference adapter."""
    t0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    cases = [
        # Group 1: 2 lifecycle snapshots
        ConformanceCase(
            name="ref_case_1",
            payload={
                "txn_id": "TX_1001",
                "amount": "100.00",
                "sender_id": "alice",
                "receiver_id": "bob",
                "status": "INITIATED",
            },
            payload_format=PayloadFormat.JSON,
            received_at=t0,
            expect="ok",
            group="grp_ref_001",
            must_appear=["TX_1001"],
            must_not_appear=["sensitive_secret", "4111111111111111"],
        ),
        ConformanceCase(
            name="ref_case_2",
            payload={
                "txn_id": "TX_1001",
                "amount": "100.00",
                "sender_id": "alice",
                "receiver_id": "bob",
                "status": "COMPLETED",
            },
            payload_format=PayloadFormat.JSON,
            received_at=t0,
            expect="ok",
            group="grp_ref_001",
            must_appear=["TX_1001"],
            must_not_appear=["sensitive_secret", "4111111111111111"],
        ),
        # Group 2: distinct transaction
        ConformanceCase(
            name="ref_case_3",
            payload={
                "txn_id": "TX_2002",
                "amount": "50.00",
                "sender_id": "carol",
                "receiver_id": "dan",
                "status": "COMPLETED",
            },
            payload_format=PayloadFormat.JSON,
            received_at=t0,
            expect="ok",
            group="grp_ref_002",
            must_appear=["TX_2002"],
            must_not_appear=["sensitive_secret"],
        ),
        # Error case 1: missing amount
        ConformanceCase(
            name="ref_error_missing_amount",
            payload={"txn_id": "TX_ERR_1", "sender_id": "alice", "receiver_id": "bob"},
            payload_format=PayloadFormat.JSON,
            received_at=t0,
            expect="error",
            error_type="NormalizationError",
            error_field="amount",
            must_not_appear=["sensitive_secret"],
        ),
        # Error case 2: zero amount
        ConformanceCase(
            name="ref_error_zero_amount",
            payload={
                "txn_id": "TX_ERR_2",
                "amount": "0.00",
                "sender_id": "alice",
                "receiver_id": "bob",
            },
            payload_format=PayloadFormat.JSON,
            received_at=t0,
            expect="error",
            error_type="UnsupportedPayloadError",
            must_not_appear=["sensitive_secret"],
        ),
    ]

    # Foreign cases from another rail
    foreign = [
        ConformanceCase(
            name="foreign_upi",
            payload={"vpa": "user@okaxis", "amount": "100.00"},
            payload_format=PayloadFormat.JSON,
            received_at=t0,
            expect="ok",
        )
    ]
    return cases, foreign


# --- 10 Defective Variants ---


class DefectiveUuid4Adapter(ReferenceAdapter):
    """(1) Uses uuid4 for transaction_id -> fails CF-04, CF-05, CF-06."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        res = super().normalize(event)
        # Using uuid.uuid4() breaks determinism, AST static purity, and dynamic purity guard
        res.transaction.transaction_id = uuid.uuid4()
        return res


class DefectiveEchoValueAdapter(ReferenceAdapter):
    """(2) Echoes payload value in NormalizationError -> fails CF-16, CF-18."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        if isinstance(event.payload, dict) and "amount" not in event.payload:
            # Leaks sensitive payload value in exception message
            leaked_val = "sensitive_secret"
            raise NormalizationError(
                f"Missing amount with sensitive value {leaked_val}",
                field="amount",
                reason=f"Leaked {leaked_val}",
            )
        return super().normalize(event)


class DefectiveKeyErrorAdapter(ReferenceAdapter):
    """(3) Raises bare KeyError on missing field -> fails CF-16, CF-19."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        if isinstance(event.payload, dict) and "amount" not in event.payload:
            # Bare KeyError escapes AdapterError family
            raise KeyError("amount")
        return super().normalize(event)


class DefectiveMutatingAdapter(ReferenceAdapter):
    """(4) Mutates input payload in-place -> fails CF-14."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        if isinstance(event.payload, dict):
            event.payload["injected_mutation"] = True
        return super().normalize(event)


class DefectiveStatefulCounterAdapter(ReferenceAdapter):
    """(5) Keeps a stateful counter in output -> fails CF-04, CF-15."""

    def __init__(self) -> None:
        self.counter = 0

    def normalize(self, event: RawEvent) -> NormalizationResult:
        self.counter += 1
        res = super().normalize(event)
        res.transaction.network_metadata["counter"] = str(self.counter)
        return res


class DefectiveFloatMathAdapter(ReferenceAdapter):
    """(6) Uses float arithmetic for amounts -> fails CF-12."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        res = super().normalize(event)
        # Injects a float into metadata
        res.transaction.network_metadata["float_val"] = 123.45  # type: ignore[assignment]
        return res


class DefectiveNaiveDatetimeAdapter(ReferenceAdapter):
    """(7) Emits naive datetimes without tzinfo -> fails CF-07."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        res = super().normalize(event)
        # Naive datetime
        res.transaction.initiated_at = datetime(2026, 10, 15, 12, 0, 0)
        return res


class DefectiveLoggingAdapter(ReferenceAdapter):
    """(8) Logs raw payload containing sensitive data -> fails CF-18."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        logging.getLogger().info("Processing raw payload with sensitive_secret: %s", event.payload)
        return super().normalize(event)


class DefectiveUnstableIdAdapter(ReferenceAdapter):
    """(9) Uses different transaction_id across snapshots of one group -> fails CF-09."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        res = super().normalize(event)
        # Derive transaction_id including status, breaking group stability
        st_val = (
            str(event.payload.get("status", "default")) if isinstance(event.payload, dict) else ""
        )
        res.transaction.transaction_id = deterministic_uuid(
            "reference", str(res.transaction.network_transaction_ref), st_val
        )
        return res


class DefectivePermissiveAdapter(ReferenceAdapter):
    """(10) Accepts any payload from any network without error -> fails CF-21."""

    def normalize(self, event: RawEvent) -> NormalizationResult:
        try:
            return super().normalize(event)
        except Exception:
            # Swallows rejection of foreign payloads and returns a synthetic result
            tx_id = deterministic_uuid("reference", "synthetic")
            p_id = deterministic_uuid("reference", "synthetic_p")
            p = Party(
                party_id=p_id,
                identifiers=[
                    PartyIdentifier(
                        type=IdentifierType.WALLET_USER_ID, value="p", network="reference"
                    )
                ],
                home_network="reference",
                created_at=event.received_at,
            )
            tx = Transaction(
                transaction_id=tx_id,
                network_transaction_ref="synthetic",
                network="reference",
                sender_id=p_id,
                receiver_id=p_id,
                sender_identifier=p.identifiers[0],
                receiver_identifier=p.identifiers[0],
                amount=Money(amount=Decimal("10.00"), currency="USD", scale=2),
                initiated_at=event.received_at,
                settled_at=event.received_at,
                observed_at=event.received_at,
                status=TransactionStatus.SETTLED,
                settlement_model=SettlementModel.INSTANT,
                direction=TransactionDirection.TRANSFER,
                is_reversible=False,
                intermediary_hops=[],
            )
            return NormalizationResult(
                transaction=tx,
                parties=[p],
                correlation_key="reference:synthetic",
                lifecycle_stage="completed",
                adapter_version=self.adapter_version,
            )


# --- Meta-Test Execution ---


def test_reference_adapter_passes_all_checks() -> None:
    """Assert reference adapter passes every check in the conformance suite."""
    cases, foreign = make_test_cases()
    adapter = ReferenceAdapter()
    report = run_conformance(adapter, cases, options=ConformanceOptions(), foreign_cases=foreign)
    report.assert_conformant()
    assert report.passed


def _get_failure_check_ids(report: Any) -> set[str]:
    return {f.check_id for f in report.failures}


def test_variant_1_uuid4_fails_cf04_cf05_cf06() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveUuid4Adapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-04" in failed_checks, "Expected CF-04 failure for uuid4"
    assert "CF-05" in failed_checks, "Expected CF-05 failure for uuid4"
    assert "CF-06" in failed_checks, "Expected CF-06 failure for uuid4"


def test_variant_2_echo_value_fails_cf16_cf18() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveEchoValueAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-16" in failed_checks, "Expected CF-16 failure for value leak in exception"
    assert "CF-18" in failed_checks, "Expected CF-18 failure for value leak in exception"


def test_variant_3_bare_keyerror_fails_cf16_cf19() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveKeyErrorAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-16" in failed_checks, "Expected CF-16 failure for bare KeyError"
    assert "CF-19" in failed_checks, "Expected CF-19 failure for bare KeyError"


def test_variant_4_mutating_payload_fails_cf14() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveMutatingAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-14" in failed_checks, "Expected CF-14 failure for mutating payload"


def test_variant_5_stateful_counter_fails_cf04_cf15() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveStatefulCounterAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-04" in failed_checks, "Expected CF-04 failure for stateful counter"
    assert "CF-15" in failed_checks, "Expected CF-15 failure for stateful counter"


def test_variant_6_float_amount_fails_cf12() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveFloatMathAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-12" in failed_checks, "Expected CF-12 failure for float arithmetic"


def test_variant_7_naive_datetime_fails_cf07() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveNaiveDatetimeAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-07" in failed_checks, "Expected CF-07 failure for naive datetimes"


def test_variant_8_logging_payload_fails_cf18() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveLoggingAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-18" in failed_checks, "Expected CF-18 failure for logging payload"


def test_variant_9_unstable_id_fails_cf09() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectiveUnstableIdAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-09" in failed_checks, "Expected CF-09 failure for unstable transaction_id"


def test_variant_10_permissive_foreign_fails_cf21() -> None:
    cases, foreign = make_test_cases()
    report = run_conformance(DefectivePermissiveAdapter(), cases, foreign_cases=foreign)
    failed_checks = _get_failure_check_ids(report)
    assert "CF-21" in failed_checks, "Expected CF-21 failure for permissive foreign acceptance"
