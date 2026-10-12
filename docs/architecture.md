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
