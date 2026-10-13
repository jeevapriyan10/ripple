# Ripple Architecture

Ripple is a network-agnostic, real-time payment-fraud detection platform. It normalizes transactions and counterparty identities across disparate domestic and cross-border payment rails into a unified graph domain model.

---

## Domain Model

The `ripple.domain` package encapsulates the universal, network-agnostic data models, value objects, and graph projection primitives. Every model is built on Pydantic v2 with strict validation invariants, zero external database or service dependencies, and zero floating-point representation in monetary paths.

### Core Entities

| Entity | Module | Stereotype | Mutability | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`Money`** | `ripple.domain.money` | Value Object | `frozen=True` | Exact monetary quantity powered by `Decimal` with asset scale (0–18). Prohibits floats and prevents un-normalized cross-currency arithmetic. |
| **`Currency`** | `ripple.domain.money` | Value Object | `frozen=True` | Currency specification distinguishing fiat (ISO-4217, typically scale 2) from crypto assets (tickers, high scale). |
| **`PartyIdentifier`** | `ripple.domain.identity` | Value Object | `frozen=True` | Rail-specific counterparty identity token (VPA, IBAN, BIC, Crypto Address, etc.) with format validation and canonical normalization. |
| **`Party`** | `ripple.domain.party` | Aggregate Root (Graph Node) | Mutable | Economic entity owning one or more identifiers across rails. Retains risk flags, KYC tiers, and ground-truth mule labels (`is_mule`, `mule_role`). |
| **`RiskFlags`** | `ripple.domain.party` | Value Object | `frozen=True` | Intrinsic static compliance attributes (account incubation age, KYC tier, sanction hit, high-risk jurisdiction). |
| **`Transaction`** | `ripple.domain.transaction` | Aggregate Root (Graph Edge) | Mutable | Universal payment transaction capturing identity, parties, amount, timestamps (UTC), lifecycle status, settlement model, and routing waypoints. |
| **`IntermediaryHop`** | `ripple.domain.transaction` | Value Object | `frozen=True` | Institutional waypoint along a multi-hop routing path (acquirer, scheme, correspondent bank, PSP), recording sequence, role, timestamp, and fees. |
| **`GraphNode`** | `ripple.domain.graph` | Projection | `frozen=True` | Storage-agnostic projection of entities and institutions into graph nodes (`PARTY` or `INSTITUTION`). |
| **`GraphEdge`** | `ripple.domain.graph` | Projection | `frozen=True` | Storage-agnostic projection of direct transfers (`TRANSACTION`), routing segments (`ROUTED_THROUGH`), or identity ownership (`OWNS_IDENTIFIER`). |
| **`TransactionSubgraph`** | `ripple.domain.graph` | Projection Container | `frozen=True` | Neighborhood container holding nodes and edges surrounding a center node for k-hop subgraph analysis (used by Chain and Watch). |
| **`FlagEvent`** | `ripple.domain.events` | Domain Event | `frozen=True` | Alert event payload emitted when an entity or transaction is flagged with risk score, severity, and detection reasons. |

---

### Payment Rail Mappings to the Universal Schema

Payment networks have evolved distinct protocols, clearing mechanisms, and counterparty addressing standards. The table below illustrates how the five core payment archetypes normalize into the universal schema:

| Network Archetype | Example Rails | Primary Identifier Types | Settlement Model | Intermediary Hops | Reversible? | Typology & Fraud Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Instant Payments** | UPI, Pix, FedNow, SEPA Instant | `VPA`, `PHONE`, `BANK_ACCOUNT` | `INSTANT` | None (Direct) | No (`is_reversible=False`) | Rapid fan-out mule dispersion within seconds; APP (Authorized Push Payment) scams. |
| **2. Card Networks** | Visa, Mastercard, Amex, RuPay | `CARD_PAN_TOKEN`, `MERCHANT_ID` | `AUTH_CAPTURE` | Yes (`ACQUIRER` $\rightarrow$ `SCHEME` $\rightarrow$ `ISSUER`) | Yes (`is_reversible=True`, 60–120 day window) | Account takeover, card testing, stolen credentials, chargeback / friendly fraud. |
| **3. Cryptocurrency** | Bitcoin, Ethereum, Solana, Polygon | `CRYPTO_ADDRESS` (pseudonymous) | `BLOCK_CONFIRMATION` | None (P2P on-chain / `VALIDATOR`) | No (`is_reversible=False`) | Rapid peel-chains, unhosted wallet layering, mixing services, off-ramp cash-outs. |
| **4. Correspondent Banking** | SWIFT cross-border wires (MT103 / ISO 20022) | `IBAN`, `BIC`, `BANK_ACCOUNT` | `CORRESPONDENT_CHAIN` | Yes (1 to 4 `CORRESPONDENT_BANK` waypoints) | Conditional (`is_reversible=True` via recall before credit) | Cross-border trade-based laundering, sanctions evasion, multi-day delay exploitation. |
| **5. Closed-Loop Wallets** | PayPal, Venmo, Alipay, GrabPay | `WALLET_USER_ID`, `EMAIL`, `PHONE` | `INTERNAL_LEDGER` | None (Atomic ledger update) | Yes (`is_reversible=True` via platform dispute) | Account takeovers, peer-to-peer layering, internal balance draining. |

---

### Architectural Highlights

1. **Strict Monetary Precision (`Decimal` Only)**:
   - Floats are strictly prohibited in monetary paths to eliminate binary rounding errors.
   - Fiat rails operate at scale 2 (e.g. cents/paise), while crypto rails support up to 18 decimal places (e.g. Ethereum Wei).
   - Minor unit round-tripping (`to_minor_units()` $\leftrightarrow$ `from_minor_units()`) is mathematically lossless.

2. **Temporal Traceability and Latency Measurement**:
   - Every transaction maintains three distinct timezone-aware UTC timestamps:
     - `initiated_at`: Moment the payer authorized/initiated payment.
     - `settled_at`: Moment value irrevocably settled (or consensus was confirmed).
     - `observed_at`: Ingestion time into Ripple for measuring detection pipeline latency.
   - Computed property `settlement_latency_seconds` measures real clearing speed (milliseconds for UPI vs days for SWIFT).

3. **Multi-Hop Graph Decomposition**:
   - Unlike naive models that collapse all payments into a single sender-to-receiver edge, `transaction_to_edges()` decomposes multi-hop paths (SWIFT wires, card schemes) into $N + 1$ sequential `ROUTED_THROUGH` edges:
     $$\text{Sender} \longrightarrow \text{Hop}_1 \longrightarrow \text{Hop}_2 \longrightarrow \dots \longrightarrow \text{Hop}_N \longrightarrow \text{Receiver}$$
   - This exposes intermediary routing choke-points, correspondent fee slippage, and jurisdictional hops directly to downstream graph analytics.

---

## Adapter Layer

The `ripple.adapters` package defines the plug-in architecture that decouples payment-rail ingestion from Ripple's core fraud-detection pipelines. Because financial networks utilize radically different messaging standards (e.g. ISO 8583 bitmaps for cards, ISO 20022 / MT103 XML for SWIFT wires, JSON payloads for UPI, RPC blocks for crypto), each network requires a translation layer to project native events into Ripple's universal domain model.

```
+-----------------------------------------------------------------------+
|                       Incoming Rail Message                           |
|       (UPI JSON, Card ISO8583, SWIFT ISO20022, Crypto Block)          |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
|                         RawEvent Envelope                             |
|  - network_id: str                   - received_at: datetime (UTC)   |
|  - payload: dict | bytes | str       - payload_format: PayloadFormat |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
|                    NetworkAdapter (pure, deterministic)               |
|  - can_handle(event) -> bool                                          |
|  - normalize(event) -> NormalizationResult                            |
+-----------------------------------------------------------------------+
                                   |
                                   v
+-----------------------------------------------------------------------+
|                       NormalizationResult                             |
|  - transaction: Transaction          - correlation_key: str           |
|  - parties: list[Party] (deduped)    - lifecycle_stage: str           |
|  - warnings: list[str]               - adapter_version: str           |
+-----------------------------------------------------------------------+
```

### The Adapter Contract

The adapter interface is defined as an abstract base class `NetworkAdapter` in `ripple.adapters.contract`:

1. **`NetworkProfile`**: A frozen, declarative Pydantic v2 model describing static facts and operational invariants of the payment rail (settlement model, typical latency bands, reversibility windows, operating calendar, visibility, and supported identifier types). Subclasses MUST define a class-level `profile` attribute; this is validated at class-definition time via `__init_subclass__`.
2. **`RawEvent`**: The immutable ingestion container carrying the raw native payload, its serialization format (`JSON`, `XML`, `ISO8583`, `FIXED_WIDTH`, `OTHER`), and the ingestion timestamp `received_at` (UTC).
3. **`NormalizationResult`**: The frozen output container containing the universal `Transaction` aggregate, all deduplicated `Party` counterparty aggregates (including sender and receiver), the stable `correlation_key`, the `lifecycle_stage`, and the adapter release version.
4. **`BatchResult`**: The output of `normalize_batch()`, isolating failures per event so that a single corrupt payload never terminates the ingestion stream for surrounding valid transactions.
5. **`from_settings(cls, settings) -> Self`**: A non-abstract classmethod factory hook on `NetworkAdapter`. Defaults to `return cls()`. Allows configurable adapters (such as `CardAdapter`) to receive typed application settings (e.g. HMAC keys) cleanly without reading global environment variables or breaking adapter purity.
6. **`deterministic_uuid(network_id, *parts)`**: Generates stable UUIDv5 identifiers from a fixed Ripple namespace constant.

### The Four Adapter Invariant Rules

Every adapter implementation MUST adhere to four strict design rules:

1. **Pure (No I/O or Clocks)**:
   - Adapters must never perform network calls, database queries, file reads, or random/clock reads.
   - The ingestion timestamp MUST be taken directly from `event.received_at`. This ensures that historical backfills, replay pipelines, and live streams yield identical timestamps.
2. **Deterministic (UUIDv5 Only)**:
   - Given the identical raw input, the adapter must output identical models and identifiers.
   - `party_id` and `transaction_id` MUST be derived with `deterministic_uuid(network_id, *native_keys)`. Never use `uuid4()`. This guarantees idempotent graph ingestion and prevents duplicate node/edge insertion.
3. **Never Silently Drop Data**:
   - Any rail-native metadata that cannot be mapped directly into the universal domain fields must be preserved in `transaction.network_metadata` or `party.attributes`.
4. **Raise Typed Errors**:
   - If an event is unrecognizable or unsupported, raise `UnsupportedPayloadError`.
   - If a payload is recognized but fails validation or misses required fields, raise `NormalizationError` specifying the offending `field` and `reason`.
   - Never return partial garbage, empty placeholder entities, or None.

### Multi-Event Lifecycles and Correlation Keys

Payment transactions are rarely single-message events. In card networks, an **Authorization** message (reserving credit line) is followed 24–48 hours later by a **Capture/Clearing** message, and potentially weeks later by a **Chargeback/Dispute**. In cryptocurrency networks, a transaction is first broadcast to the mempool, included in a block, and subsequently verified across multiple confirmation depths.

The `correlation_key` bridges these distinct lifecycle events:
- Format: `"{network_id}:{rail_transaction_ref}"` (e.g. `"visa:auth_987654321"`, `"swift:UETR-eb92b1a0-..."`).
- Every event belonging to the same financial transaction produces the identical `correlation_key`.
- Downstream graph workers use the correlation key to locate the existing `Transaction` edge in Neo4j, update its lifecycle `status` (e.g. from `AUTHORIZED` to `SETTLED`), append any new `IntermediaryHop` records, and record updated timestamps without creating duplicate edges.

### Developing and Registering a Plugin Adapter

Network adapters can be developed as standalone Python packages and plugged into Ripple without modifying the core codebase.

#### 1. Implement the Adapter Subclass

```python
# my_network_adapter/adapter.py
from typing import ClassVar
from ripple.adapters import (
    NetworkAdapter,
    NetworkCategory,
    NetworkProfile,
    NormalizationResult,
    OperatingCalendar,
    LedgerVisibility,
    RawEvent,
    deterministic_uuid,
)
from ripple.domain import IdentifierType, AssetClass, SettlementModel

class FedNowAdapter(NetworkAdapter):
    profile: ClassVar[NetworkProfile] = NetworkProfile(
        network_id="fednow",
        display_name="FedNow Service",
        description="US Federal Reserve instant gross settlement rail.",
        category=NetworkCategory.INSTANT_PAYMENT,
        settlement_model=SettlementModel.INSTANT,
        supported_identifier_types=[IdentifierType.BANK_ACCOUNT, IdentifierType.PHONE],
        supported_asset_classes=[AssetClass.FIAT],
        default_scale=2,
        supported_currencies=["USD"],
        typical_settlement_seconds_min=0.2,
        typical_settlement_seconds_max=3.0,
        is_reversible=False,
        typical_reversal_window_seconds=None,
        intermediaries_visible=False,
        ledger_visibility=LedgerVisibility.PRIVATE,
        is_pseudonymous=False,
        operating_calendar=OperatingCalendar.ALWAYS_ON,
        operating_timezone="America/New_York",
        regions=["US"],
        schema_version=1,
    )
    adapter_version: ClassVar[str] = "1.0.0"

    def can_handle(self, event: RawEvent) -> bool:
        return event.network_id == "fednow"

    def normalize(self, event: RawEvent) -> NormalizationResult:
        # Transformation logic adhering to the four rules
        ...
```

#### 2. Register Entry Points in `pyproject.toml`

Expose the adapter class under the `ripple.adapters` entry point group:

```toml
# pyproject.toml
[project]
name = "ripple-fednow-adapter"
version = "1.0.0"
dependencies = [
    "ripple>=0.1.0",
]

[project.entry-points."ripple.adapters"]
fednow = "my_network_adapter.adapter:FedNowAdapter"
```

*Or when using Poetry:*

```toml
[tool.poetry.plugins."ripple.adapters"]
fednow = "my_network_adapter.adapter:FedNowAdapter"
```

#### 3. Automatic Discovery and Filtering

When Ripple starts up:
1. `load_enabled_adapters()` automatically queries entry points via `importlib.metadata.entry_points(group="ripple.adapters")`.
2. Each discovered plugin is validated, instantiated, and registered into `AdapterRegistry`.
3. If an external plugin fails to import or violates the contract, `discover_plugins()` logs a descriptive warning and skips it without crashing the application.
4. Active networks can be constrained using the `ENABLED_NETWORKS` configuration setting:
   ```bash
   ENABLED_NETWORKS=universal,fednow
   ```

### Built-in Network Adapters (Module 4)

Ripple includes built-in adapters for major payment rails, normalizing rail-specific lifecycles into the universal domain model:

1. **UPI Instant-Payment Adapter (`upi`)**:
   - **Rail Profile**: `INSTANT_PAYMENT`, `ALWAYS_ON`, Indian Rupee (`INR`), `Asia/Kolkata` operating timezone.
   - **Supported Flows**: P2P/P2M `PAY` (push) and `COLLECT` (pull).
   - **Lifecycle States**: `SUCCESS` &rarr; `SETTLED`, `PENDING` &rarr; `PENDING_SETTLEMENT` (or `PENDING_AUTHORIZATION` for unapproved collect requests), `DEEMED` &rarr; `PENDING_SETTLEMENT` (with `deemed: true` flag), `EXPIRED` &rarr; `CANCELLED`, `FAILURE` &rarr; `FAILED`.
   - **Identity & Minimization**: Multi-identifier parties supporting VPA, IFSC bank account, and phone. Customer personal names are discarded for data minimization.
   - **Documentation**: See [docs/adapters/upi.md](file:///e:/ongoing/ripple/docs/adapters/upi.md).

2. **Card Network Adapter (`card`)**:
   - **Rail Profile**: `CARD`, `AUTH_CAPTURE`, multi-currency support, 120-day reversal window, `BUSINESS_DAYS` clearing calendar.
   - **Supported Flows**: Purchases (`00`), Cash Withdrawals (`01`), and Refunds (`20`).
   - **Lifecycle States**: Multi-event lifecycle (`authorization` &rarr; `clearing` &rarr; `settlement` &rarr; `reversal` &rarr; `chargeback`) sharing a stable `correlation_key` (`"card:" + network_reference`).
   - **PCI-DSS PAN Security**: Luhn checksum validation; HMAC-SHA256 irreversible tokenization; strict zero-PAN-leak policy across all outputs and exceptions.
   - **Documentation**: See [docs/adapters/card.md](file:///e:/ongoing/ripple/docs/adapters/card.md).


