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
| **`IntermediaryHop`** | `ripple.domain.transaction` | Value Object | `frozen=True` | Institutional waypoint along a multi-hop routing path (acquirer, scheme, correspondent bank, debtor agent, creditor agent, PSP), recording sequence, role, timestamp, and fees. |
| **`GraphNode`** | `ripple.domain.graph` | Projection | `frozen=True` | Storage-agnostic projection of entities and institutions into graph nodes (`PARTY` or `INSTITUTION`). |
| **`GraphEdge`** | `ripple.domain.graph` | Projection | `frozen=True` | Storage-agnostic projection of direct transfers (`TRANSACTION`), routing segments (`ROUTED_THROUGH`), or identity ownership (`OWNS_IDENTIFIER`). |
| **`TransactionSubgraph`** | `ripple.domain.graph` | Projection Container | `frozen=True` | Neighborhood container holding nodes and edges surrounding a center node for k-hop subgraph analysis (used by Chain and Watch). |
| **`FlagEvent`** | `ripple.domain.events` | Domain Event | `frozen=True` | Alert event payload emitted when an entity or transaction is flagged with risk score, severity, and detection reasons. |

---

### Payment Rail Mappings to the Universal Schema

Payment networks have evolved distinct protocols, clearing mechanisms, and counterparty addressing standards. The table below illustrates how the five core payment archetypes normalize into the universal schema:

| Network Archetype | Adapter ID | Primary Identifier Types | Settlement Model | Intermediary Hops | Reversible? | Typology & Fraud Risks |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Instant Payments** | `upi` | `VPA`, `PHONE`, `BANK_ACCOUNT` | `INSTANT` | None (Direct) | No (`is_reversible=False`) | Rapid fan-out mule dispersion within seconds; APP (Authorized Push Payment) scams. |
| **2. Card Networks** | `card` | `CARD_PAN_TOKEN`, `MERCHANT_ID` | `DEFERRED_NET` | Yes (`ISSUER` $\rightarrow$ `SCHEME` $\rightarrow$ `ACQUIRER`) | Yes (`is_reversible=True`, 120-day window) | Account takeover, card testing, stolen credentials, chargeback / friendly fraud. |
| **3. Cryptocurrency** | `crypto` | `CRYPTO_ADDRESS` (EVM, Bitcoin, Solana, Tron) | `BLOCK_CONFIRMATION` | None (P2P on-chain) | No (`is_reversible=False`) | Rapid peel-chains, unhosted wallet layering, mixing services, off-ramp cash-outs. |
| **4. Correspondent Banking** | `swift` | `IBAN`, `BIC`, `BANK_ACCOUNT` | `CORRESPONDENT_CHAIN` | Yes (`DEBTOR_AGENT` $\rightarrow$ `CORRESPONDENT_BANK` $\rightarrow$ `CREDITOR_AGENT`) | No (`is_reversible=False`; recalls require cooperative consent) | Cross-border trade-based laundering, sanctions evasion, multi-day delay exploitation. |
| **5. Closed-Loop Wallets** | `wallet` | `WALLET_USER_ID`, `EMAIL`, `PHONE`, `MERCHANT_ID`, `OTHER` | `INTERNAL_LEDGER` | None (Atomic ledger update) | Yes (`is_reversible=True`, 180-day simulation default) | Account takeovers, peer-to-peer layering, internal balance draining, buyer-protection abuse. |

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

### Built-in Network Adapters (Modules 4 & 5)

Ripple includes built-in adapters for major payment rails, normalizing rail-specific lifecycles into the universal domain model:

1. **UPI Instant-Payment Adapter (`upi`)**:
   - **Rail Profile**: `INSTANT_PAYMENT`, `ALWAYS_ON`, Indian Rupee (`INR`), `Asia/Kolkata` operating timezone.
   - **Supported Flows**: P2P/P2M `PAY` (push) and `COLLECT` (pull).
   - **Lifecycle States**: `SUCCESS` &rarr; `SETTLED`, `PENDING` &rarr; `PENDING_SETTLEMENT` (or `PENDING_AUTHORIZATION` for unapproved collect requests), `DEEMED` &rarr; `PENDING_SETTLEMENT` (with `deemed: true` flag), `EXPIRED` &rarr; `CANCELLED`, `FAILURE` &rarr; `FAILED`.
   - **Identity & Minimization**: Multi-identifier parties supporting VPA, IFSC bank account, and phone. Customer personal names are discarded for data minimization.
   - **Documentation**: See [docs/adapters/upi.md](file:///docs/adapters/upi.md).

2. **Card Network Adapter (`card`)**:
   - **Rail Profile**: `CARD`, `AUTH_CAPTURE`, multi-currency support, 120-day reversal window, `BUSINESS_DAYS` clearing calendar.
   - **Supported Flows**: Purchases (`00`), Cash Withdrawals (`01`), and Refunds (`20`).
   - **Lifecycle States**: Multi-event lifecycle (`authorization` &rarr; `clearing` &rarr; `settlement` &rarr; `reversal` &rarr; `chargeback`) sharing a stable `correlation_key` (`"card:" + network_reference`).
   - **PCI-DSS PAN Security**: Luhn checksum validation; HMAC-SHA256 irreversible tokenization; strict zero-PAN-leak policy across all outputs and exceptions.
   - **Documentation**: See [docs/adapters/card.md](file:///docs/adapters/card.md).

3. **Cryptocurrency Adapter (`crypto`)**:
   - **Rail Profile**: `CRYPTO`, `BLOCK_CONFIRMATION`, multi-chain (EVM, Bitcoin, Solana, Tron, custom), `ALWAYS_ON`, public ledger visibility, pseudonymous.
   - **Supported Flows**: Native transfers and token smart contract transfers (ERC-20, SPL, TRC-20).
   - **Lifecycle States**: `pending` (mempool) &rarr; `included` (confirming) &rarr; `included` (`finalized` / threshold reached &rarr; `CONFIRMED`), `failed` / reverted, `dropped`, `replaced` (RBF), `reorged`.
   - **Identity & Minimization**: Single `CRYPTO_ADDRESS` per (address family, normalized address). Case-sensitive base58 (Bitcoin legacy, Solana, Tron) is preserved; case-insensitive EVM `0x` and `bc1` bech32 are lowercased.
   - **Documentation**: See [docs/adapters/crypto.md](file:///docs/adapters/crypto.md).

4. **SWIFT Correspondent Banking Adapter (`swift`)**:
   - **Rail Profile**: `WIRE`, `CORRESPONDENT_CHAIN`, multi-currency fiat, `BUSINESS_DAYS`, private ledger, intermediaries visible.
   - **Supported Flows**: Customer credit transfers (pacs.008 / MT103 concepts), status events (gpi tracking), recall requests (FRAD, DUPL, TECH, CUST), and returns.
   - **Lifecycle States**: `payment` (`instruction` &rarr; `INITIATED`), `ACTC` &rarr; `PENDING_AUTHORIZATION`, `ACSP`/`ACSC`/`PDNG` &rarr; `PENDING_SETTLEMENT`, `ACCC` &rarr; `SETTLED`, `RJCT` &rarr; `REJECTED`, `recall_request` &rarr; `DISPUTED`, `return` &rarr; `REVERSED`.
   - **Hops & Intermediaries**: Sequenced in funds-flow order: `DEBTOR_AGENT` (seq 1) &rarr; `CORRESPONDENT_BANK` (seq 2..N) &rarr; `CREDITOR_AGENT` (seq N+1).
   - **Strict IBAN Security & Minimization**: MOD-97 checksum validation; zero-IBAN-leak guarantee in all logs, warnings, and error responses; customer personal names, physical addresses, and remittance narratives are discarded.
   - **Documentation**: See [docs/adapters/swift.md](file:///docs/adapters/swift.md).

5. **Closed-Loop Digital Wallet Adapter (`wallet`)**:
   - **Rail Profile**: `WALLET`, `INTERNAL_LEDGER`, multi-currency fiat, `ALWAYS_ON`, private ledger, reversible (`is_reversible=True`, 180-day simulation default).
   - **Supported Flows**: P2P transfers (`send` / `request`), merchant payments, external funding and withdrawals, refunds, dispute resolution snapshots, and rail reversals.
   - **Lifecycle States**: `PENDING` &rarr; `PENDING_SETTLEMENT` (or `PENDING_AUTHORIZATION` for unapproved requests), `HELD` &rarr; `PENDING_SETTLEMENT` (with hold reason metadata), `COMPLETED` &rarr; `SETTLED`, `DENIED` &rarr; `REJECTED`, `FAILED` &rarr; `FAILED`, `CANCELLED`/`EXPIRED` &rarr; `CANCELLED`. Dispute events map to `DISPUTED`, `REVERSED`, or `SETTLED`; reversals map to `REVERSED`.
   - **Identity & External Instruments**: Multi-identifier parties supporting `WALLET_USER_ID`, `EMAIL`, `PHONE`, `MERCHANT_ID`, and external payment instruments (`instr:<instrument_id>`, `OTHER`, pseudonymous).
   - **Strict Credential Hygiene & Minimization**: Credential-class fields (PAN, CVV, IBAN, bank account numbers, OTP, PIN, password) are strictly discarded with a warning naming the field only. Ordinary PII (customer names, physical addresses, notes) is silently discarded. Emails and phone numbers are stored for entity resolution but never echoed in warnings, exceptions, logs, or error responses.
   - **Documentation**: See [docs/adapters/wallet.md](file:///docs/adapters/wallet.md).

---

### Adapter Conformance Suite (`ripple.adapters.testing`)

The Adapter Conformance Suite is a reusable, data-driven test library that verifies every adapter (built-in or third-party plugin) satisfies the strict purity, determinism, time discipline, security, and error contracts demanded by Ripple's ingestion pipeline.

- **Clean Package Boundary**: Shipped directly within `ripple.adapters.testing`, relying solely on the Python standard library and Pydantic v2 (zero pytest imports in runtime code).
- **21 Conformance Checks (CF-01 through CF-21)**:
  - Surface & Registration: Contract surface (`CF-01`), dynamic registry integration (`CF-02`), safe `can_handle` execution (`CF-03`).
  - Purity & Determinism: Output determinism across fresh instances (`CF-04`), AST static purity scan (`CF-05`), runtime dynamic purity monkey-patch guards (`CF-06`).
  - Data & Temporal Discipline: Timezone-aware UTC time discipline (`CF-07`), network stamping and profile consistency (`CF-08`), lifecycle snapshot stability (`CF-09`), graph integrity (`CF-10`), settlement coherence (`CF-11`), strict Decimal money discipline (`CF-12`), JSON serialization round-tripping (`CF-13`).
  - Execution Safety: Caller input immutability (`CF-14`), multi-threaded statelessness (`CF-15`), typed error contracts (`CF-16`), batch failure isolation (`CF-17`), zero credential/PII leak scanning (`CF-18`), deterministic mutation fuzzing (`CF-19`), performance latency smoke benchmarks (`CF-20`), cross-network foreign payload rejection (`CF-21`).
- **Declarative Manifests (`conformance.json`)**: Every registered adapter declares its test cases, expected errors, lifecycle groups, and leak boundaries in a JSON manifest beside its fixtures.
- **Documentation**: See [docs/adapters/conformance.md](file:///docs/adapters/conformance.md) and [docs/adapters/writing-an-adapter.md](file:///docs/adapters/writing-an-adapter.md).

---

### Shared Normalization Infrastructure

- **Shared ISO-4217 Currency Table (`ripple.adapters.currencies`)**: Centralized lookup for 35+ major world currencies with scale, numeric codes, and `resolve_currency` validation.
- **Timestamp Normalization (`parse_datetime`)**: Parses ISO-8601 strings and integer epoch seconds into timezone-aware UTC, rejecting floats, booleans, and negative values.
---

## Multi-Network Synthetic Transaction Generator (`ripple.generator`)

The Synthetic Transaction Generator serves as Ripple's data engine. It builds an interconnected simulated world of people, merchants, corporations, crypto exchanges, and banks to generate realistic background organic activity across all five payment networks, injects hard-negative behavioral look-alikes, and overlays ten calibrated financial crime typologies.

```
+--------------------------------------------------------------------+
|                         GeneratorConfig                            |
|             (seed, actors, days, networks, fraud, drift)           |
+--------------------------------------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------------+
|                           World Model                              |
|           (Actors, Institutions, Multi-Rail Identities)            |
+--------------------------------------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------------+
|                   Simulation Engine (Priority Queue)               |
|  - Background Traffic Planners (UPI, Card, Crypto, SWIFT, Wallet)  |
|  - 8 Hard Negative Behavioral Look-Alikes                          |
|  - Scenario Coordinator (10 Injected Typologies, Drift Regimes)    |
+--------------------------------------------------------------------+
                                   |
                                   v
+--------------------------------------------------------------------+
|                     Pure Renderers (Rule R5)                       |
|           (Emits native payloads for the 5 Adapters)               |
+--------------------------------------------------------------------+
                 /                                    \
                v                                      v
+------------------------------------+  +----------------------------+
|        events.jsonl.gz             |  |      truth.jsonl.gz        |
|  (RawEvents passed to adapters;    |  |  (Separate sidecar keyed   |
|   zero ground-truth labels)        |  |   1:1 by sequence index)   |
+------------------------------------+  +----------------------------+
```

### Architectural Guarantees & Invariants

1. **Rule R1: Determinism**: Full reproducibility across runs, platforms, and architectures given identical configurations. Built on an isolated split-mix counter-based pseudo-random number generator (`RngFactory`).
2. **Rule R2: Synthetic Safety**: Non-routable identifiers throughout (`+999` telephone numbers, invalid Bitcoin Base58 checksums, valid mod-97 synthetic IBANs, and strictly **no** Luhn-valid 13–19 digit PAN strings).
3. **Rule R3: Sidecar Ground Truth**: Ground-truth labels are written to an isolated, separate `truth.jsonl.gz` sidecar file, keyed by sequence index. Raw events emitted to `events.jsonl.gz` never contain labels, scenario flags, or fraud tags.
4. **Rule R4: Calibration Honesty**: Every empirical parameter in calibration catalogs explicitly states its source. Any parameter not backed by an authoritative cited source is tagged with `provenance: "assumption"` and `citation: null`.
5. **Rule R5: Pure Renderers**: Payload renderers are pure functions with zero I/O and zero state.
6. **Rule R6: Financial Precision**: All balances, fees, and transfer amounts are calculated using Python's exact `Decimal` arithmetic. Floats are prohibited in monetary fields.

### Key Subsystems

- **Scenario Coordinator & Typologies**: Injects 10 complex multi-hop topologies (`upi_mule_chain`, `fan_in_smurfing`, `wallet_provisional_funding_abuse`, `card_testing`, `chargeback_fraud_merchant`, `crypto_peeling_layering`, `nft_wash_trading`, `account_takeover_drain`, `swift_structuring_layering`, `cross_network_laundering`) with realistic reporting delay and adversarial drift regimes (`v2_mule_template`, `v2_card_testing`).
- **Statistical Separability Report**: Generates rank-based AUC (Mann-Whitney U) metrics and warns against trivial leakage features (>0.90) or unlearnable targets (<0.55).
- **Label Resolution & Temporal Splitting**: Provides realistic retrospective labeling delays (`transaction_labels_as_of`) and temporal train/val/test splits with configurable embargo purges to prevent future data leakage.
- **REST API & CLI**: Exposes generation, previewing, and dataset verification via FastAPI endpoints and developer CLI (`python -m ripple.generator`).
- **Documentation**: See [docs/generator/README.md](file:///docs/generator/README.md), [docs/generator/scenarios.md](file:///docs/generator/scenarios.md), [docs/generator/calibration.md](file:///docs/generator/calibration.md), [docs/generator/networks.md](file:///docs/generator/networks.md), and [docs/generator/limitations.md](file:///docs/generator/limitations.md).




