# Closed-Loop Payment Wallet Adapter (`wallet`)

## Overview

The `wallet` adapter models closed-loop stored-value digital wallet ecosystems (e.g. PayPal, Venmo, Cash App, Mercado Pago, Alipay concepts). Value transfers occur primarily on internal closed-loop ledgers between registered wallet user accounts, supplemented by external rail interactions for account balance funding and withdrawals.

### How the Rail Works

1. **Internal Stored-Value Ledger**:
   - Accounts hold fiat balances on an operator-managed internal ledger (`INTERNAL_LEDGER`).
   - Intra-network peer-to-peer (P2P) transfers and merchant payments settle immediately via balance reallocation without external clearinghouse delays.
   - External funding (cash-in) and withdrawal (cash-out) events bridge between the closed-loop wallet and external rails (ACH, Faster Payments, debit cards, wire).
2. **Asymmetric Flow Types**:
   - **P2P (`p2p`)**: User-to-user transfers. Can be initiated directly (`flow="send"`) or via payment requests (`flow="request"`), which remain `PENDING_AUTHORIZATION` until approved. Payments distinguish between `friends_family` (non-reversible by default) and `goods_services` (covered by buyer protection).
   - **Merchant Payment (`merchant_payment`)**: Customer purchases from merchants. Carries merchant classification codes (MCC), platform fees, and currency conversions. Reversible by default under buyer protection policies.
   - **Funding (`funding`)**: Money enters the wallet from an external payment instrument (bank account, debit card, credit line). Direction is `CREDIT` to the wallet holder.
   - **Withdrawal (`withdrawal`)**: Money exits the wallet to an external destination instrument. Direction is `DEBIT` from the wallet holder.
   - **Refund (`refund`)**: Merchant returns funds to a buyer. Modeled as a **new transaction** with reversed funds flow and `parent_txn_id` recorded in metadata.
   - **Dispute (`dispute`) & Reversal (`reversal`)**: Post-settlement lifecycle snapshots of an existing transaction sharing the original `txn_id` and `correlation_key`.
3. **Data Minimization & Sensitive Data Quarantine**:
   - Closed-loop wallets often receive raw upstream banking credentials or customer PII. The adapter applies strict hygiene: credential-class fields (PANs, CVVs, IBANs, bank account numbers, passwords, OTPs) are permanently stripped with a warning naming the field only.
   - Ordinary personal names, addresses, and notes are discarded silently and reported in `discarded_fields` via `describe()`.

---

## Payload Spec v1 (JSON)

Each event describes an action on a wallet transaction or snapshot:

| Field | Type | Required | Description |
|---|---|---|---|
| `event_type` | String | Yes | Event discriminator: `p2p`, `merchant_payment`, `funding`, `withdrawal`, `refund`, `dispute`, `reversal`. |
| `txn_id` | String | Yes | Transaction reference on the wallet ledger (`network_transaction_ref`). For `dispute` and `reversal`, this is the original transaction's reference. |
| `parent_txn_id` | String | Conditional | Reference of the original transaction being refunded. Required when `event_type="refund"`. |
| `original_event_type` | String | Conditional | Original event type (`p2p`, `merchant_payment`, `funding`, `withdrawal`). Required when `event_type` is `dispute` or `reversal`. |
| `status` | String | Conditional | Transaction status: `PENDING`, `COMPLETED`, `DENIED`, `FAILED`, `HELD`, `CANCELLED`, `EXPIRED`. Required for non-dispute/reversal events; ignored for dispute/reversal. |
| `flow` | String | No | P2P transfer initiation: `send` (default) or `request`. |
| `requested_at` | String / Int | Conditional | Timestamp when a P2P payment request was created. Required when `flow="request"`. |
| `approved_at` | String / Int | No | Timestamp when a P2P payment request was authorized by the payer. |
| `sender` | Object | Conditional | Wallet account or external instrument sending funds. |
| `receiver` | Object | Conditional | Wallet account or external instrument receiving funds. |
| `account` | Object | Conditional | Wallet account for funding/withdrawal events. |
| `instrument` | Object | Conditional | External payment instrument for funding/withdrawal events. |
| `amount` | String | Yes | Transaction amount in the primary sending currency (decimal string, e.g. `"100.00"`). Floats strictly prohibited. |
| `currency` | String | Yes | ISO-4217 alpha-3 fiat currency (e.g. `"USD"`, `"EUR"`, `"GBP"`). Crypto assets prohibited. |
| `converted` | Object | No | Receiver-side conversion: `{amount: str, currency: str, fx_rate: str}`. Preserved in metadata. |
| `fee` | Object | No | Platform service fee: `{amount: str, currency: str}`. |
| `fee_payer` | String | No | Entity paying fees: `sender`, `receiver`, or `split`. Preserved in metadata. |
| `created_at` | String / Int | Yes | Event creation timestamp. For dispute/reversal snapshots, this represents the original transaction creation timestamp. |
| `completed_at` | String / Int | Conditional | Settle timestamp. Required when `status="COMPLETED"`. |
| `original_completed_at` | String / Int | Conditional | Original completion timestamp. Required when `event_type` is `dispute` or `reversal`. |
| `payment_type` | String | No | P2P purpose: `friends_family` (default) or `goods_services`. |
| `buyer_protection` | Boolean | No | Explicit override for buyer protection coverage. |
| `reversal_window_days` | Integer | No | Dispute / chargeback eligibility window in days (integer $\ge 1$). |
| `hold_reason` | String | No | Reason code/string when `status="HELD"` (e.g. `"risk_review"`, `"regulatory_hold"`). |
| `denial_reason` | String | No | Operator denial reason when `status="DENIED"`. |
| `failure_reason` | String | No | Failure cause when `status="FAILED"`. |
| `funding_source` | String | No | Rail source: `balance`, `bank`, `card`, `credit`, `other`. |
| `funding_instrument_id` | String | No | Opaque token for funding instrument (preserved in metadata). |
| `provisional_credit` | Boolean | No | Indicates provisional wallet crediting ahead of external rail finality. |
| `case_id` | String | Conditional | Dispute case reference. Required when `event_type="dispute"`. |
| `dispute_status` | String | Conditional | Dispute state: `opened`, `escalated`, `resolved_buyer`, `resolved_seller`, `withdrawn`. Required for dispute. |
| `dispute_reason` | String | Conditional | Dispute cause: `item_not_received`, `unauthorized`, `not_as_described`, `duplicate`, `other`. Required for dispute. |
| `opened_at` | String / Int | Conditional | Timestamp when dispute was opened. Required for dispute. |
| `resolved_at` | String / Int | Conditional | Dispute resolution timestamp. Required when dispute status is `resolved_*` or `withdrawn`. |
| `reversal_reason` | String | Conditional | Reversal cause: `funding_failed`, `fraud`, `operator_error`, `other`. Required for reversal. |
| `reversed_at` | String / Int | Conditional | Reversal timestamp. Required for reversal. |
| `channel` | String | No | Delivery channel (defaults to `"wallet_app"`). |
| `device_id` | String | No | Client device identifier (preserved in metadata). |
| `ip_address` | String | No | Client IP address (preserved in metadata). |
| `ip_country` | String | No | ISO alpha-2 country of client IP (mapped to `geo_country`). |
| `note` | String | No | User memo / payment narrative (silently discarded; length and presence preserved). |

---

## Field Mapping (Wallet &rarr; Universal)

| Wallet Field | Universal Domain Field | Mapping Logic |
|---|---|---|
| `txn_id` | `Transaction.network_transaction_ref` | Verbatim transaction reference |
| Derived | `Transaction.transaction_id` | `deterministic_uuid("wallet", txn_id)` |
| Derived | `NormalizationResult.correlation_key` | `"wallet:" + txn_id` (shared by dispute/reversal snapshots) |
| `status` / dispute / reversal | `Transaction.status` | Mapped via Status Mapping Table below |
| Direction | `Transaction.direction` | `p2p` &rarr; `TRANSFER`; `merchant_payment`, `withdrawal` &rarr; `DEBIT`; `funding`, `refund` &rarr; `CREDIT`. Dispute/reversal take the direction of their `original_event_type`. |
| `amount` | `Transaction.amount` | `Money(amount=amount, currency=currency, scale=2, asset_class=AssetClass.FIAT)` |
| `converted` | `Transaction.network_metadata` | Preserved as string values: `converted_amount`, `converted_currency`, `fx_rate` |
| `fee` | `Transaction.fee` | `Money(amount=fee.amount, currency=fee.currency, scale=2)` |
| `created_at` | `Transaction.initiated_at` | `created_at`, except P2P requests where `initiated_at = approved_at or requested_at` |
| `completed_at` | `Transaction.settled_at` | Set for `COMPLETED`, dispute resolutions, and reversals; `None` for pending/denied/failed |
| `buyer_protection` / `reversal_window_days` | `Transaction.is_reversible` & `reversal_window_ends_at` | Reversible when `buyer_protection=True` or `merchant_payment`. Window ends at `settled_at + reversal_window` |
| Wallet Account | `Transaction.sender_id` / `receiver_id` & `Party` | `WALLET_USER_ID` identifier; `party_id = deterministic_uuid("wallet", account_id)`. Secondary identifiers `EMAIL`, `PHONE`, `MERCHANT_ID` attached when present. |
| External Instrument | `Transaction.sender_id` / `receiver_id` & `Party` | `OTHER` identifier with value `"instr:<instrument_id>"`; `party_id = deterministic_uuid("wallet", "instrument", instrument_id)`; `is_pseudonymous=True`; `party_type=UNKNOWN`. |

---

## Status Mapping Table

| Event Type | Payload Status / Dispute Status | Universal Status | `settled_at` | Lifecycle Stage |
|---|---|---|---|---|
| Non-dispute/reversal | `PENDING` (standard) | `PENDING_SETTLEMENT` | `None` | `pending` |
| `p2p` (request) | `PENDING` (no `approved_at`) | `PENDING_AUTHORIZATION` | `None` | `requested` |
| Non-dispute/reversal | `HELD` | `PENDING_SETTLEMENT` | `None` | `held` (metadata `hold=True`) |
| Non-dispute/reversal | `COMPLETED` | `SETTLED` | `completed_at` | `completed` |
| Non-dispute/reversal | `DENIED` | `REJECTED` | `None` | `denied` |
| Non-dispute/reversal | `FAILED` | `FAILED` | `None` | `failed` |
| Non-dispute/reversal | `CANCELLED` / `EXPIRED` | `CANCELLED` | `None` | `cancelled` / `expired` |
| `dispute` | `opened` / `escalated` | `DISPUTED` | `original_completed_at` | `dispute_opened` / `dispute_escalated` |
| `dispute` | `resolved_buyer` | `REVERSED` | `resolved_at` | `dispute_resolved_buyer` |
| `dispute` | `resolved_seller` / `withdrawn` | `SETTLED` | `original_completed_at` | `dispute_resolved_seller` / `dispute_withdrawn` |
| `reversal` | Any | `REVERSED` | `reversed_at` | `reversed` |

---

## Discarded Fields & Sensitive Data Quarantine

### 1. Credential-Class Fields (Hard Security Quarantine)
Fields that represent authentication secrets, card PANs, or direct bank account numbers are permanently discarded wherever they appear at any nesting depth. The adapter emits a warning naming the field only (never echoing values):
- `pan`, `card_number`, `cvv`, `cvc`, `expiry`
- `iban`, `account_number`, `routing_number`, `sort_code`
- `ssn`, `tax_id`, `national_id`, `passport_number`
- `password`, `otp`, `pin`, `security_answer`

### 2. Ordinary PII (Data Minimization)
Personal identifiers are discarded silently and reported in `discarded_fields` via `describe()`:
- `name`, `first_name`, `last_name`
- `address`, `date_of_birth`
- `note`, `memo` (presence and string length are preserved as `note_present` and `note_length` in metadata)

### 3. Contact Identifiers (Email and Phone)
- `email` and `phone` are structurally validated and attached as `EMAIL` and `PHONE` identifiers to the wallet `Party` node for counterparty identity resolution.
- They are **never logged**, included in warnings, or surfaced in exception messages or API error bodies.

---

## Known Limitations

1. **Simplified Spec is Not a Provider API**:
   The payload schema is Ripple's canonical closed-loop wallet specification, harmonizing concepts across PayPal, Venmo, Cash App, and Mercado Pago. It does not mirror any single proprietary commercial API.
2. **Fiat Balances Only**:
   The wallet adapter only processes fiat currencies (`AssetClass.FIAT`). Cryptocurrency balances, crypto off-ramps, and Web3 wallet extensions are handled exclusively by the `crypto` adapter.
3. **Advanced Stored-Value Features Out of Scope**:
   Subscriptions, recurring billing agreements, gift cards, loyalty rewards, and multi-currency balance pools are unsupported in v1. Mass payouts appear as ordinary business-to-user P2P transfers.
4. **Entity Resolution Belongs to Mesh**:
   The same physical human owning accounts across multiple wallets (or owning both a wallet account and a bank account) generates distinct `Party` nodes at the adapter layer. Cross-network entity resolution is executed downstream by Mesh (Module 08).
5. **Instrument Tokens Not Linkable to PAN Tokens**:
   Operator-issued `instrument_id` strings are opaque tokens internal to the wallet operator. They cannot be correlated directly with `CARD_PAN_TOKEN` nodes produced by the `card` adapter.
6. **Coarse Settlement Latency Band**:
   The wallet network profile declares a settlement latency band of 1 to 259,200 seconds (1 second to 3 days). Internal peer-to-peer transfers settle within seconds, but external funding and withdrawal events depend on the timing of external banking or card rails.
7. **Simulation Default Reversal Window**:
   `typical_reversal_window_seconds` is configured to 180 days (15,552,000s). This represents a conventional platform policy default for testing and simulation, not a legal guarantee. Per-event overrides via `buyer_protection` and `reversal_window_days` take precedence.
8. **Unknown Payload Keys Preserved by Name Only**:
   Unmapped top-level JSON keys are recorded in `network_metadata.unmapped_field_names` (sorted list of strings) with a single generic warning. The corresponding values are intentionally discarded because arbitrary unknown fields from upstream systems may contain un-redacted sensitive data.
