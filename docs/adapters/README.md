# Payment Network Adapters

Network adapters translate raw, heterogeneous rail-specific payloads into Ripple's universal fraud detection domain model (`Transaction`, `Party`, `IntermediaryHop`, `Money`, `PartyIdentifier`).

Adapters adhere to the **Four Core Adapter Invariants**:
1. **Pure**: No I/O operations and no system clock reads. Event receipt timestamps are supplied by the ingestion runtime via `RawEvent.received_at`.
2. **Deterministic**: Identifiers and keys are generated using `deterministic_uuid()`, never random UUIDs (`uuid4`).
3. **Never Silently Drop Data**: Unmapped or non-standard rail attributes are preserved in `Transaction.network_metadata` or explicitly documented in `discarded_fields` (for privacy / data minimization).
4. **Typed Errors Only**: Malformed or unprocessable payloads raise typed exceptions (`NormalizationError`, `UnsupportedPayloadError`) and never leak sensitive credentials, account numbers, or IBANs in error text.

---

## Adapter Profile Comparison Matrix

| Property | `universal` | `upi` | `card` | `crypto` | `swift` | `wallet` |
|---|---|---|---|---|---|---|
| **Network ID** | `universal` | `upi` | `card` | `crypto` | `swift` | `wallet` |
| **Category** | `OTHER` | `INSTANT` | `CARD` | `CRYPTO` | `WIRE` | `WALLET` |
| **Settlement Model** | `DEFERRED_NET` | `INSTANT` | `DEFERRED_NET` | `BLOCK_CONFIRMATION` | `CORRESPONDENT_CHAIN` | `INTERNAL_LEDGER` |
| **Identifier Types** | Any | `VPA`, `BANK_ACCOUNT`, `PHONE` | `CARD_PAN_TOKEN`, `MERCHANT_ID` | `CRYPTO_ADDRESS` | `IBAN`, `BIC`, `BANK_ACCOUNT` | `WALLET_USER_ID`, `EMAIL`, `PHONE`, `MERCHANT_ID`, `OTHER` |
| **Asset Classes** | `FIAT`, `CRYPTO` | `FIAT` | `FIAT` | `CRYPTO` | `FIAT` | `FIAT` |
| **Default Scale** | 2 | 2 | 2 | 18 | 2 | 2 |
| **Currencies** | Any | `INR` only | Any ISO-4217 | Any (chain native / tokens) | Any ISO-4217 | Any ISO-4217 fiat |
| **Settlement Latency** | 0s – 86,400s | 1s – 30s | 86,400s – 259,200s (1–3d) | 10s – 3,600s | 300s – 259,200s (5m–3d) | 1s – 259,200s (instant to 3d) |
| **Is Reversible** | `False` | `False` | `True` | `False` | `False` | `True` |
| **Reversal Window** | `None` | `None` | 10,368,000s (120 days) | `None` | `None` | 15,552,000s (180 days default) |
| **Intermediaries Visible**| `True` | `False` | `True` (4-party) | `False` | `True` (correspondent chain) | `False` |
| **Ledger Visibility** | `PRIVATE` | `PRIVATE` | `PRIVATE` | `PUBLIC` | `PRIVATE` | `PRIVATE` |
| **Is Pseudonymous** | `False` | `False` | `False` | `True` | `False` | `False` |
| **Operating Calendar** | `ALWAYS_ON` | `ALWAYS_ON` | `BUSINESS_DAYS` | `ALWAYS_ON` | `BUSINESS_DAYS` | `ALWAYS_ON` |
| **Operating Timezone** | `"UTC"` | `"Asia/Kolkata"` | `"UTC"` | `None` | `"UTC"` | `None` |
| **Fee Range (bps)** | `None` | `None` | 50 – 350 bps | `None` (gas is non-linear) | `None` (flat per-hop charges) | 0 – 500 bps |

---

## Detailed Rail Documentation
- [Universal Adapter Specification](file:///docs/adapters/README.md) (Standard canonical JSON format)
- [UPI Instant Payments Adapter](file:///docs/adapters/upi.md) (`upi`)
- [Card Network Adapter](file:///docs/adapters/card.md) (`card`)
- [Cryptocurrency Adapter](file:///docs/adapters/crypto.md) (`crypto`)
- [SWIFT Correspondent Banking Adapter](file:///docs/adapters/swift.md) (`swift`)
- [Closed-Loop Digital Wallet Adapter](file:///docs/adapters/wallet.md) (`wallet`)

## Conformance & Extensibility
- [Adapter Conformance Suite Guide](file:///docs/adapters/conformance.md) (Test harness, CF-01–CF-21 checks, findings log)
- [Writing an Adapter Guide](file:///docs/adapters/writing-an-adapter.md) (Step-by-step developer tutorial for built-ins and plugins)
