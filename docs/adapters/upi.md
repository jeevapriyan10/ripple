# UPI Instant-Payment Network Adapter (`upi`)

## Overview

Unified Payments Interface (UPI) is an instant real-time payment system developed by the National Payments Corporation of India (NPCI) and regulated by the Reserve Bank of India (RBI). It facilitates inter-bank peer-to-peer (P2P) and person-to-merchant (P2M) transactions on mobile devices.

### How the Rail Works
1. **Addressing**: Transactors route payments via Virtual Payment Addresses (VPAs, e.g. `alice@oksbi`), phone numbers, or account numbers with Indian Financial System Codes (IFSC).
2. **Transaction Types**:
   - **`PAY` (Push)**: The payer initiates an immediate debit from their bank account to transfer funds to the payee.
   - **`COLLECT` (Pull)**: The payee sends a payment request to the payer. The request enters a pending approval state (`COLLECT_REQUESTED`). If approved by the payer, the money moves from payer to payee (`SETTLED`). If unapproved after an expiry duration, it expires (`CANCELLED`).
3. **Settlement**: From the consumer's perspective, funds settle immediately (`INSTANT`). In the backend, NPCI settles interbank balances on RBI's RTGS system in periodic netted settlement cycles.

---

## Payload Spec v1 (JSON)

The adapter consumes the documented JSON specification (`Ripple UPI-style spec v1`):

| Field | Type | Required | Description |
|---|---|---|---|
| `txn_id` | String | Yes | Unique rail transaction reference (e.g. `UPI_TXN_20261013001`). Maps to `network_transaction_ref`. |
| `rrn` | String | No | Retrieval Reference Number (12-digit NPCI clearing reference). |
| `txn_type` | String | Yes | `PAY` or `COLLECT`. Any other type (e.g. `MANDATE`) raises `UnsupportedPayloadError`. |
| `status` | String | Yes | `SUCCESS`, `FAILURE`, `PENDING`, `DEEMED`, `EXPIRED`, `REVERSED`. |
| `response_code` | String | No | Rail response code (e.g. `00`, `U16`, `U30`, `ZM`). |
| `payer` | Object | Yes | Payer entity: `vpa`, `account` (`ifsc`, `number`), `phone`. |
| `payee` | Object | Yes | Payee entity: `vpa`, `account` (`ifsc`, `number`), `phone`, `merchant` (`mcc`, `merchant_id`). |
| `amount` | String / Number | Yes | Transaction amount. Floats strictly prohibited; must be string or integer. |
| `currency` | String | Yes | Currency code. Must strictly be `INR`. |
| `initiated_at` | ISO-8601 String | Conditional | Timestamp of payer action. For `PAY`, required. For `COLLECT`, present upon approval. |
| `collect_requested_at`| ISO-8601 String | Conditional | Required for `COLLECT` payment requests. |
| `completed_at` | ISO-8601 String | Conditional | Required when `status` is `SUCCESS`. |
| `channel` | String | No | Channel identifier (`mobile_app`, `ussd`, `ivr`, `web`). Defaults to `mobile_app`. |
| `device_id` | String | No | Payer client hardware or app installation identifier. |
| `ip_address` | String | No | Originating IPv4 or IPv6 address. |
| `purpose_code` | String | No | 4-digit RBI/NPCI purpose category code. |
| `remarks` | String | No | Free-text payment note. Truncated to 100 characters in `network_metadata`. |

---

## Field Mapping (Native &rarr; Universal)

| Native UPI Field | Universal Domain Field | Mapping Logic |
|---|---|---|
| `txn_id` | `Transaction.network_transaction_ref` | Verbatim string |
| Derived | `Transaction.transaction_id` | `deterministic_uuid("upi", txn_id)` |
| Derived | `NormalizationResult.correlation_key` | `"upi:" + txn_id` |
| `amount` + `currency` | `Transaction.amount` | `Money(amount=Decimal(amount), currency="INR", scale=2)` |
| `initiated_at` | `Transaction.initiated_at` | Parsed to UTC. For `COLLECT` without `initiated_at`, falls back to `collect_requested_at`. |
| `completed_at` | `Transaction.settled_at` | Set when status is `SUCCESS`, else `None`. |
| `event.received_at` | `Transaction.observed_at` | Ingestion clock time (pure; adapter never reads system clock). |
| Direction | `Transaction.direction` | Always `TransactionDirection.TRANSFER` (payer &rarr; payee). |
| `channel` | `Transaction.channel` | Defaults to `"mobile_app"`. |
| `device_id` | `Transaction.device_id` | Verbatim string if present. |
| `ip_address` | `Transaction.ip_address` | Validated IP address if present. |
| `payer` | `Transaction.sender_id` & `Party` | Primary identifier preference: `VPA` > `BANK_ACCOUNT` (`"IFSC:number"`) > `PHONE`. |
| `payee` | `Transaction.receiver_id` & `Party` | Same identifier preference. If `merchant` data present, `party_type=MERCHANT`. |
| `response_code` | `network_metadata["response_code"]` | Verbatim code and known label (or `"unknown"`). |
| `remarks` | `network_metadata["remarks"]` | Truncated to 100 characters. |
| `rrn` | `network_metadata["rrn"]` | Verbatim string if present. |
| `purpose_code` | `network_metadata["purpose_code"]` | Verbatim string if present. |

---

## Status Mapping Table

| Native Status | Universal `TransactionStatus` | `settled_at` | `lifecycle_stage` | Notes |
|---|---|---|---|---|
| `SUCCESS` | `SETTLED` | `completed_at` (required) | `completed` | Transaction fully settled. |
| `FAILURE` | `FAILED` | `None` | `failed` | Payment declined or failed. |
| `PENDING` (PAY) | `PENDING_SETTLEMENT` | `None` | `pending` | Payment in transit. |
| `PENDING` (COLLECT without `initiated_at`) | `PENDING_AUTHORIZATION` | `None` | `collect_requested` | Awaiting payer approval. |
| `PENDING` (COLLECT with `initiated_at`) | `PENDING_SETTLEMENT` | `None` | `pending` | Approved, awaiting settlement. |
| `DEEMED` | `PENDING_SETTLEMENT` | `None` | `deemed` | Deemed success; `network_metadata.deemed = True`. |
| `EXPIRED` | `CANCELLED` | `None` | `expired` | Collect request timed out. |
| `REVERSED` | `REVERSED` | `None` | `reversed` | Transaction recalled or reversed. |

### Response Code Dictionary
Only codes known with certainty are mapped:
- `00`: `success`
- `U16`: `risk_threshold_exceeded`
- `U30`: `debit_failed`
- `ZM`: `invalid_pin`
- Any other code: `unknown` (preserved verbatim in `network_metadata.response_code`)

---

## Intentionally Discarded Fields (Data Minimization)

To adhere to privacy regulations and data minimization standards, personal customer names (`payer.name` and `payee.name`) are intentionally discarded and never propagated into `Party`, `Transaction`, or `network_metadata`.
- Discarded fields are declared on `UpiAdapter.discarded_fields` and exposed via `adapter.describe()`.
- They do NOT generate runtime warnings per event.

---

## Known Limitations

1. **Simplified Specification vs. Wire Format**: Real NPCI wire communications use ISO 20022 XML formats wrapped in proprietary digital signatures over private Virtual Private Networks (VPNs). Ripple's adapter targets a documented, simplified JSON payload schema reflecting public rail semantics. In a production deployment, a thin gateway parser would translate NPCI XML into this JSON schema before adapter ingestion.
2. **Entity Resolution (Party Multi-Node Representation)**: Because transactors may initiate payments using different primary identifiers (e.g. Alice transacting via `alice@oksbi` in one event, and `+919876543210` in another), they will initially register as distinct `Party` aggregates in the graph. Resolving multiple identifiers to a single human persona is the responsibility of Module 8 (Mesh Entity Resolution).
3. **Customer-Visible Settlement vs. Interbank Clearing**: The adapter profile specifies `settlement_model: INSTANT` and `typical_settlement_seconds_min: 1`, `max: 30`. This models consumer-visible settlement. Actual interbank settlement is netted through RBI in batched RTGS clearing cycles.
4. **Non-INR Corridors**: UPI cross-border remittances (e.g. UPI-PayNow Singapore linkage) utilize foreign currency corridors at the point of sale, but settlement on the core Indian rail executes in INR. Payloads with non-INR currencies are rejected.
