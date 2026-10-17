# Card Network Adapter (`card`)

## Overview

Card payment networks operate on a **four-party model** (Cardholder, Merchant, Acquirer, Issuer) mediated by an intermediary payment scheme (e.g. Visa, Mastercard, RuPay, American Express).

### How the Rail Works
1. **Authorization**: At point of sale or checkout, the merchant contacts the acquirer, which routes an ISO 8583 `0100`/`0200` authorization request through the scheme to the issuing bank. The issuer evaluates available credit or balance and responds with an approval (`00`) or decline code. Funds are held but not yet moved.
2. **Clearing**: At the close of business, the merchant batches authorized transactions. The acquirer submits clearing messages (`0220`/`1240`) to the scheme for reconciliation.
3. **Settlement**: 1 to 3 business days later, net interbank settlement takes place across central bank accounts.
4. **Reversals and Disputes**:
   - **Reversal**: Sent immediately if a terminal or communication timeout prevents completion of an authorization.
   - **Chargeback**: Cardholder disputes or fraud claims initiated through the scheme, carrying specific scheme reason codes, with liability windows extending up to 120 days.

---

## Payload Spec v1 (JSON with ISO 8583 Semantics)

The adapter consumes the documented JSON specification (`Ripple card spec v1`):

| Field | Type | Required | Description |
|---|---|---|---|
| `event_type` | String | Yes | `authorization`, `clearing`, `settlement`, `reversal`, `chargeback`. |
| `network_reference` | String | Yes | Scheme trace reference or retrieval reference number. Serves as correlation base. |
| `processing_code` | String | Yes | ISO 8583 DE3 processing code: `"00"` (purchase), `"01"` (cash withdrawal), `"20"` (refund). Others raise `UnsupportedPayloadError`. |
| `pan` / `card_token` | String | Yes | At least one required. A raw PAN is validated via Luhn and tokenized immediately. |
| `bin` | String | No | Bank Identification Number (first 6 digits of card). |
| `last4` | String | No | Last 4 digits of card. |
| `amount` | String / Number | Yes | Transaction amount. Strictly positive decimal string or int. Floats prohibited. |
| `currency` | String / Number | Yes | Alpha-3 ISO-4217 code (`USD`, `EUR`, `INR`, `JPY`, etc.) or numeric ISO-4217 (`840`, `978`, `392`, etc.). |
| `transmission_time` | String | Conditional | ISO-8601 string or DE7-style `"MMDDhhmmss"` GMT string. |
| `settlement_date` | String | Conditional | `YYYY-MM-DD` string, mandatory for `settlement` events. |
| `acquirer` | Object | Yes | Acquirer entity with `id`. |
| `scheme` | String | No | Payment scheme brand (e.g. `"visa"`, `"mastercard"`). |
| `issuer` | Object | No | Issuer entity with `id` and optional `country`. |
| `merchant` | Object | Conditional | Merchant entity with `id`, `name`, `mcc`, `country`, `city`. Required for non-ATM transactions. |
| `channel` | String | No | `pos`, `ecommerce`, `atm`, `recurring`, `moto`. Defaults to `"other"` with a warning if unrecognized. |
| `response_code` | String | No | Authorization response code (e.g. `"00"` for approved). |
| `reason_code` | String | No | Scheme dispute/chargeback reason code. |
| `mdr_fee` | String / Number | No | Merchant Discount Rate or interchange fee. Mapped to `Transaction.fee`. |

---

## Field Mapping (Native &rarr; Universal)

| Native Card Field | Universal Domain Field | Mapping Logic |
|---|---|---|
| `network_reference` | `Transaction.network_transaction_ref` | Scheme trace id |
| Derived | `Transaction.transaction_id` | `deterministic_uuid("card", network_reference)` |
| Derived | `NormalizationResult.correlation_key` | `"card:" + network_reference` |
| `event_type` | `NormalizationResult.lifecycle_stage` | Verbatim event type |
| `amount` + `currency` | `Transaction.amount` | `Money(amount=Decimal(amount), currency=curr, scale=scale)` |
| `transmission_time` | `Transaction.initiated_at` | Converted to UTC. DE7 `"MMDDhhmmss"` infers closest year to `received_at`. |
| `settlement_date` | `Transaction.settled_at` | `YYYY-MM-DD` at `00:00:00 UTC` for `settlement` events. |
| `event.received_at` | `Transaction.observed_at` | Ingestion clock time (pure; adapter never reads clock). |
| Sender / Receiver | `Transaction.sender_id` & `receiver_id` | For purchases: Sender is Cardholder (`CARD_PAN_TOKEN`), Receiver is Merchant (`MERCHANT_ID`). For refunds: inverted. |
| `channel == "atm"` | Receiver `party_type` | `PartyType.FINANCIAL_INSTITUTION` (ATM cash dispenser). |
| Direction | `Transaction.direction` | `DEBIT` for purchases/withdrawals; `CREDIT` for refunds (`processing_code == "20"`). |
| Intermediary Hops | `Transaction.intermediary_hops` | Reflects direction of funds flow: Issuer &rarr; Scheme &rarr; Acquirer (reversed for refunds). |
| `mdr_fee` | `Transaction.fee` | Converted to `Money` when present. |

---

## Status Mapping Table

| Event Type | Response Code | Universal Status | `settled_at` | `is_reversible` | Reversal Window |
|---|---|---|---|---|---|
| `authorization` | `"00"` | `AUTHORIZED` | `None` | `True` | `None` |
| `authorization` | Non-`"00"` | `REJECTED` | `None` | `False` | `None` |
| `clearing` | Any | `PENDING_SETTLEMENT` | `None` | `True` | `None` |
| `settlement` | Any | `SETTLED` | `settlement_date` at 00:00 UTC | `True` | `settled_at + 120 days` |
| `reversal` | Any | `REVERSED` | `None` | `False` | `None` |
| `chargeback` | Any | `DISPUTED` | Preserved | `True` | Maintained |

---

## Strict Sensitive Data Rules (Hard Security Guarantees)

PCI-DSS compliance and card security requirements are enforced at the adapter boundary:

1. **HMAC-SHA256 Tokenization**:
   - A raw Primary Account Number (PAN) is validated using the Luhn checksum algorithm. Invalid PANs raise `NormalizationError` without echoing the PAN.
   - Valid PANs are immediately transformed into tokens:
     $$\text{token} = \text{"tok\_"} + \text{HMAC-SHA256}(K, \text{PAN})[:32]$$
   - Only `bin` (first 6 digits) and `last4` (last 4 digits) are retained in `Party.attributes`.
   - **Zero PAN Leaks**: The raw PAN never enters `Party`, `Transaction`, `network_metadata`, warnings, exception messages, repr, logs, or API responses.
2. **Prohibited Sensitive Authentication Data (SAD)**:
   - Fields matching `cvv`, `cvc`, `cvv2`, `track1`, `track2`, `pin_block`, `expiry` are discarded immediately upon arrival.
   - A diagnostic warning is emitted naming *only the field name*, never the value, alerting operators to an upstream data leak.
3. **Key Management**:
   - `CARD_PAN_TOKEN_KEY` must be at least 32 characters.
   - The HMAC secret is injected via constructor (`from_settings(settings)`). The adapter never accesses `os.environ`.
   - In `staging` or `production`, a missing or truncated key halts application readiness via `AdapterRegistrationError`.
   - In `development`, a fallback development key is used with a loud `WARNING` log.

---

## Known Limitations

1. **Simplified Specification vs. Binary ISO 8583**: Real card network traffic is encoded in binary bitmap-based ISO 8583 (1987, 1993, or 2003) or ISO 20022 messages. Ripple's adapter targets a documented, simplified JSON specification modeling ISO 8583 data elements (DE3 processing code, DE7 transmission time, DE11 STAN, DE37 RRN). In production, an upstream hardware security module (HSM) or ISO parser terminates binary links and outputs normalized JSON.
2. **Zero-Amount Account Verification Authorizations**: Real card testing often utilizes $0.00 authorizations to verify card validity. Ripple's Module 2 domain model enforces strictly positive monetary amounts ($> 0$). Zero-amount card authorizations cannot be represented and are explicitly rejected with `UnsupportedPayloadError`.
3. **Institutional Hop Super-Nodes**: In four-party card models, schemes (Visa, Mastercard) and major acquirers process billions of transactions. In the Neo4j graph model, institutional hops (`scheme:visa`, `acq:ACQ_101`) create dense "super-nodes" (nodes with millions of incident edges). Downstream graph algorithms must apply node sampling or edge filtering to prevent graph traversal performance degradation.
4. **Token Key Rotation**: Because PAN tokens are deterministically keyed with HMAC-SHA256, rotating `CARD_PAN_TOKEN_KEY` will cause subsequent transactions for the same physical card to produce a different `CARD_PAN_TOKEN`, splitting the cardholder's history across two node identifiers. Key rotation requires an offline graph migration.
5. **Operating Calendar**: The profile defines `operating_calendar: BUSINESS_DAYS` with `typical_settlement_seconds_min: 86400` (24h) and `max: 259200` (72h). This models backend clearing and settlement windows; authorizations operate 24/7/365.
