# Writing a Payment Network Adapter

## Overview

Ripple is designed for extensibility. New payment networks (domestic RTGS systems, real-time clearing rails, card schemes, or closed-loop digital wallets) can be integrated as built-in adapters or external third-party plugins.

This guide provides a step-by-step walkthrough for building a conformant adapter from scratch.

---

## The Four Invariants (Non-Negotiable)

Every adapter in Ripple must satisfy four architectural invariants:

1. **Pure**: No I/O operations (no network requests, no database connections, no filesystem writes) and **no system clock reads** (`datetime.now()`, `time.time()` are strictly prohibited). Event arrival timestamps are provided externally via `RawEvent.received_at`.
2. **Deterministic**: Identity generation must be fully deterministic. Every UUID must be derived using `deterministic_uuid()`. Calling `uuid.uuid4()` is prohibited and caught by both static AST scanning and dynamic runtime guards.
3. **Never Silently Drop Data**: Unmapped rail attributes must be recorded in `Transaction.network_metadata` or explicitly declared in the class attribute `discarded_fields` (exposed via `describe()`).
4. **Typed Errors Only**: Adapters must never let standard exceptions (`KeyError`, `ValueError`, `TypeError`, `AttributeError`, Pydantic `ValidationError`) escape to the caller. Raise typed subclasses of `AdapterError` (`NormalizationError`, `UnsupportedPayloadError`). Error messages must name fields only and **never echo submitted customer values, account numbers, or credentials**.

---

## Step 1: Define the `NetworkProfile`

Create a `NetworkProfile` declaring the operational characteristics, settlement semantics, and identifier types supported by the rail:

```python
from ripple.adapters.profile import (
    LedgerVisibility,
    NetworkCategory,
    NetworkProfile,
    OperatingCalendar,
)
from ripple.domain.identity import IdentifierType
from ripple.domain.money import AssetClass
from ripple.domain.transaction import SettlementModel

FEDNOW_PROFILE = NetworkProfile(
    network_id="fednow",
    display_name="Federal Reserve FedNow Service",
    description="US domestic instant payment rail with 24/7/365 immediate settlement.",
    category=NetworkCategory.INSTANT_PAYMENT,
    settlement_model=SettlementModel.INSTANT,
    supported_identifier_types=[
        IdentifierType.ROUTING_TRANSIT_NUMBER,
        IdentifierType.BANK_ACCOUNT,
    ],
    supported_asset_classes=[AssetClass.FIAT],
    default_scale=2,
    supported_currencies=["USD"],
    typical_settlement_seconds_min=1.0,
    typical_settlement_seconds_max=20.0,
    is_reversible=False,
    typical_reversal_window_seconds=None,
    intermediaries_visible=False,
    ledger_visibility=LedgerVisibility.PRIVATE,
    is_pseudonymous=False,
    operating_calendar=OperatingCalendar.ALWAYS_ON,
    operating_timezone="America/New_York",
    typical_fee_bps_range=(0, 25),
    regions=["US"],
    schema_version=1,
)
```

---

## Step 2: Implement the `NetworkAdapter` Subclass

Subclass `NetworkAdapter` and implement `can_handle`, `normalize`, and the classmethod `from_settings`:

```python
from decimal import Decimal
from typing import Any, ClassVar

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    RawEvent,
    deterministic_uuid,
)
from ripple.adapters.errors import (
    NormalizationError,
    UnsupportedPayloadError,
)
from ripple.adapters.helpers import payload_as_dict, require, wrap_domain_errors
from ripple.domain.identity import IdentifierType, PartyIdentifier
from ripple.domain.money import AssetClass, Money
from ripple.domain.party import Party, PartyType
from ripple.domain.transaction import (
    SettlementModel,
    Transaction,
    TransactionDirection,
    TransactionStatus,
)


class FedNowAdapter(NetworkAdapter):
    """Normalizes FedNow pacs.008 instant payment instructions into Ripple."""

    profile: ClassVar[NetworkProfile] = FEDNOW_PROFILE
    adapter_version: ClassVar[str] = "1.0.0"
    description: ClassVar[str] = "FedNow Instant Payment Adapter"
    discarded_fields: ClassVar[set[str]] = {
        "debtor_name",
        "creditor_name",
        "remittance_information",
    }

    def can_handle(self, event: RawEvent) -> bool:
        """Fast preliminary check to determine if this adapter can process the event."""
        if event.network_id != self.profile.network_id:
            return False
        payload = event.payload
        if not isinstance(payload, dict):
            return False
        return "end_to_end_id" in payload and "instructed_amount" in payload

    def normalize(self, event: RawEvent) -> NormalizationResult:
        """Pure, deterministic translation into the universal domain model."""
        payload = payload_as_dict(event)  # Deep-copies payload to protect caller immutability

        # Required field extraction using typed helpers
        e2e_id = str(require(payload, "end_to_end_id", field="end_to_end_id"))
        debtor_routing = str(require(payload, "debtor_routing", field="debtor_routing"))
        creditor_routing = str(require(payload, "creditor_routing", field="creditor_routing"))
        raw_amount = require(payload, "instructed_amount", field="instructed_amount")

        if isinstance(raw_amount, float):
            raise NormalizationError(
                "Floats prohibited in monetary amounts.",
                field="instructed_amount",
                reason="Float prohibited",
            )
        try:
            amount = Decimal(str(raw_amount))
        except Exception as exc:
            raise NormalizationError(
                "Invalid decimal amount.", field="instructed_amount", reason="Invalid decimal"
            ) from exc

        if amount <= Decimal(0):
            raise UnsupportedPayloadError("Zero or negative payment amounts are unsupported.")

        # Deterministic UUID generation (NEVER use uuid4)
        tx_id = deterministic_uuid(self.profile.network_id, e2e_id)
        sender_uuid = deterministic_uuid(self.profile.network_id, "party", debtor_routing)
        receiver_uuid = deterministic_uuid(self.profile.network_id, "party", creditor_routing)

        # Wrap domain model construction to sanitize potential domain validation errors
        with wrap_domain_errors():
            sender_ident = PartyIdentifier(
                type=IdentifierType.ROUTING_TRANSIT_NUMBER,
                value=debtor_routing,
                network=self.profile.network_id,
            )
            receiver_ident = PartyIdentifier(
                type=IdentifierType.ROUTING_TRANSIT_NUMBER,
                value=creditor_routing,
                network=self.profile.network_id,
            )

            # Time discipline: use event.received_at for party creation timestamps
            sender_party = Party(
                party_id=sender_uuid,
                identifiers=[sender_ident],
                party_type=PartyType.INSTITUTION,
                home_network=self.profile.network_id,
                created_at=event.received_at,
            )
            receiver_party = Party(
                party_id=receiver_uuid,
                identifiers=[receiver_ident],
                party_type=PartyType.INSTITUTION,
                home_network=self.profile.network_id,
                created_at=event.received_at,
            )

            tx = Transaction(
                transaction_id=tx_id,
                network_transaction_ref=e2e_id,
                network=self.profile.network_id,
                sender_id=sender_uuid,
                receiver_id=receiver_uuid,
                sender_identifier=sender_ident,
                receiver_identifier=receiver_ident,
                amount=Money(amount=amount, currency="USD", scale=2, asset_class=AssetClass.FIAT),
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
            parties=[sender_party, receiver_party],
            correlation_key=f"{self.profile.network_id}:{e2e_id}",
            lifecycle_stage="settled",
            adapter_version=self.adapter_version,
        )

    @classmethod
    def from_settings(cls, settings: Any) -> "FedNowAdapter":
        """Factory method to construct adapter instance from application settings."""
        return cls()
```

---

## Step 3: Create Fixtures and Conformance Manifest

Create test fixture JSON files in a dedicated fixture directory:
- At least one `ok` case per lifecycle snapshot.
- At least two `error` cases (one `NormalizationError`, one `UnsupportedPayloadError`).
- Define `must_not_appear` lists containing sensitive test values (PANs, passwords, personal names).

Create `conformance.json` beside your fixtures:

```json
{
  "schema": 1,
  "network_id": "fednow",
  "options": {
    "network_stamp_exempt": false,
    "profile_consistency_exempt": false
  },
  "cases": [
    {
      "name": "fednow_payment_settled",
      "fixture": "payment_settled.json",
      "payload_format": "json",
      "received_at": "2026-10-15T12:00:00Z",
      "expect": "ok",
      "group": "grp_fednow_001",
      "must_appear": ["FEDNOW_E2E_1001"],
      "must_not_appear": ["Jane Doe", "4111111111111111"]
    },
    {
      "name": "error_missing_amount",
      "fixture": "error_missing_amount.json",
      "payload_format": "json",
      "received_at": "2026-10-15T12:00:00Z",
      "expect": "error",
      "error_type": "NormalizationError",
      "error_field": "instructed_amount"
    },
    {
      "name": "error_zero_amount",
      "fixture": "error_zero_amount.json",
      "payload_format": "json",
      "received_at": "2026-10-15T12:00:00Z",
      "expect": "error",
      "error_type": "UnsupportedPayloadError"
    }
  ]
}
```

---

## Step 4: Run the Conformance Suite

Wire your manifest into pytest:

```python
from pathlib import Path
from ripple.adapters.testing import load_manifest, run_conformance


def test_fednow_conformance() -> None:
    manifest_path = Path(__file__).parent / "fixtures" / "conformance.json"
    cases, options = load_manifest(manifest_path)
    adapter = FedNowAdapter()
    report = run_conformance(adapter, cases, options=options)
    report.assert_conformant()
```

Run conformance via Makefile or pytest:
```bash
make conformance
```

---

## Step 5: Register the Plugin Entry Point

To enable automatic discovery without modifying Ripple's core source code, register your adapter under the `ripple.adapters` entry-point group in your package configuration.

**In `pyproject.toml` (Poetry):**
```toml
[tool.poetry.plugins."ripple.adapters"]
fednow = "my_package.adapters.fednow:FedNowAdapter"
```

**In `pyproject.toml` (standard Flit / Hatch / Setuptools):**
```toml
[project.entry-points."ripple.adapters"]
fednow = "my_package.adapters.fednow:FedNowAdapter"
```

Enable your adapter at runtime via `.env`:
```bash
ENABLED_NETWORKS=universal,upi,card,crypto,swift,wallet,fednow
```
