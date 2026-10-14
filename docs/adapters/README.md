# Payment Network Adapters

Network adapters translate raw, heterogeneous rail-specific payloads into Ripple's universal fraud detection domain model (`Transaction`, `Party`, `IntermediaryHop`, `Money`, `PartyIdentifier`).

Adapters adhere to the **Four Core Adapter Invariants**:
1. **Pure**: No I/O operations and no system clock reads. Event receipt timestamps are supplied by the ingestion runtime via `RawEvent.received_at`.
2. **Deterministic**: Identifiers and keys are generated using `deterministic_uuid()`, never random UUIDs (`uuid4`).
3. **Never Silently Drop Data**: Unmapped or non-standard rail attributes are preserved in `Transaction.network_metadata` or explicitly documented in `discarded_fields` (for privacy / data minimization).
4. **Typed Errors Only**: Malformed or unprocessable payloads raise typed exceptions (`NormalizationError`, `UnsupportedPayloadError`) and never leak sensitive credentials, account numbers, or IBANs in error text.

---

## Adapter Profile Comparison Matrix

| Property | `universal` | `upi` | `card` | `crypto` | `swift` |
|---|---|---|---|---|---|
| **Network ID** | `universal` | `upi` | `card` | `crypto` | `swift` |
| **Category** | `OTHER` | `INSTANT` | `CARD` | `CRYPTO` | `WIRE` |
| **Settlement Model** | `DEFERRED_NET` | `INSTANT` | `DEFERRED_NET` | `BLOCK_CONFIRMATION` | `CORRESPONDENT_CHAIN` |
| **Identifier Types** | Any | `VPA`, `BANK_ACCOUNT`, `PHONE` | `CARD_PAN_TOKEN`, `MERCHANT_ID` | `CRYPTO_ADDRESS` | `IBAN`, `BIC`, `BANK_ACCOUNT` |
| **Asset Classes** | `FIAT`, `CRYPTO` | `FIAT` | `FIAT` | `CRYPTO` | `FIAT` |
| **Default Scale** | 2 | 2 | 2 | 18 | 2 |
| **Currencies** | Any | `INR` only | Any ISO-4217 | Any (chain native / tokens) | Any ISO-4217 |
| **Settlement Latency** | 0s – 86,400s | 1s – 30s | 86,400s – 259,200s (1–3d) | 10s – 3,600s | 300s – 259,200s (5m–3d) |
| **Is Reversible** | `False` | `False` | `True` | `False` | `False` |
| **Reversal Window** | `None` | `None` | 10,368,000s (120 days) | `None` | `None` |
| **Intermediaries Visible**| `True` | `False` | `True` (4-party) | `False` | `True` (correspondent chain) |
| **Ledger Visibility** | `PRIVATE` | `PRIVATE` | `PRIVATE` | `PUBLIC` | `PRIVATE` |
| **Is Pseudonymous** | `False` | `False` | `False` | `True` | `False` |
| **Operating Calendar** | `ALWAYS_ON` | `ALWAYS_ON` | `BUSINESS_DAYS` | `ALWAYS_ON` | `BUSINESS_DAYS` |
| **Operating Timezone** | `"UTC"` | `"Asia/Kolkata"` | `"UTC"` | `None` | `"UTC"` |
| **Fee Range (bps)** | `None` | `None` | 50 – 350 bps | `None` (gas is non-linear) | `None` (flat per-hop charges) |

> [!NOTE]
> The **Stored-Value Digital Wallet** adapter (`wallet`) arrives in **Module 06**.

---

## Detailed Rail Documentation
- [Universal Adapter Specification](file:///docs/adapters/README.md) (Standard canonical JSON format)
- [UPI Instant Payments Adapter](file:///docs/adapters/upi.md) (`upi`)
- [Card Network Adapter](file:///docs/adapters/card.md) (`card`)
- [Cryptocurrency Adapter](file:///docs/adapters/crypto.md) (`crypto`)
- [SWIFT Correspondent Banking Adapter](file:///docs/adapters/swift.md) (`swift`)
