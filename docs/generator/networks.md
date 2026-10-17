# Multi-Network Background Traffic and Lifecycles

This document details the background traffic models, state machines, and realistic payload rendering for all five financial rails simulated by the Ripple synthetic generator: **UPI**, **Card**, **Crypto**, **SWIFT**, and **Wallet**.

---

## 1. UPI (Unified Payments Interface)

- **Network ID**: `upi`
- **Currency**: `INR` (strictly restricted to Indian jurisdiction actors and cross-border remittance corridors)
- **Transaction Types**:
  - `p2p_pay`: Direct peer-to-peer push payment between individual VPAs (`username@rplbank1`).
  - `merchant_qr`: Point-of-sale or online merchant payment carrying merchant category code (`mcc`).
  - `bill_pay`: Utility and enterprise billing payments directed to businesses and PSP aggregators.
  - `collect_request`: Payer receives a payment request that is approved/completed or expires (`EXPIRED`).
- **Lifecycles & State Transitions**:
  - Instant direct success (`SUCCESS`, response code `00`).
  - Asynchronous pending resolution (`PENDING` followed by `SUCCESS` sharing the same `txn_id`).
  - Rejection / Failure with typed response codes: `00` (success), `U16` (transaction limit exceeded), `U30` (timeout), `ZM` (invalid PIN/auth), or unrecognized response codes to exercise adapter resilience.
  - Rare deemed transactions and follow-up reversals (`REVERSED`).
- **Purity & Integrity**:
  - Directions strictly model funds moving from payer to payee.
  - VPAs and bank accounts obey non-identifying synthetic formatting (`rplbank1`, `RPLX0` IFSC codes).

---

## 2. Card Payments (ISO 8583 / AS 2805 Emulation)

- **Network ID**: `card`
- **Supported Channels**: POS in-store (`pos_purchase`), Ecommerce (`ecommerce_purchase`), Recurring (`recurring`), ATM (`atm_withdrawal`), and Cross-Border (`cross_border`).
- **Lifecycle Progression**:
  1. **Authorization**: Real-time auth check returning response codes (`00` approved, `05` do not honor, `51` insufficient funds, `61` exceeds withdrawal limit).
  2. **Clearing**: Batch submission processed on the next business day (T+1) with capture amounts that may reflect tips or partial captures.
  3. **Settlement**: Interbank settlement finalizing funds transfer 1–2 business days later (T+2).
  4. **Exceptions & Disputes**: Same-day auth reversals, refunds issued days later with separate network references (processing code `20`), and rare legitimate chargebacks 10–90 days post-settlement with scheme reason codes (e.g. `4837`).
- **Safety**:
  - Never emits PANs or Luhn-valid 13–19 digit credit card numbers.
  - Uses opaque deterministic tokens (`tok_` + 32 hex chars) with synthetic BINs and last-4 digits.

---

## 3. Cryptographic Ledger & Blockchains

- **Network ID**: `crypto`
- **Supported Blockchains**: `ethereum` (EVM), `bitcoin` (UTXO), `solana`, and `tron`. Required confirmations are loaded dynamically from `ripple.adapters.crypto.chains`.
- **Transaction Kinds**:
  - `transfer_native`: Native asset transfer (ETH, BTC, SOL, TRX).
  - `transfer_token`: Fictional stable token transfers with 6 decimal places and synthetic contract addresses.
  - `exchange_deposit` & `exchange_withdrawal`: Sweeps to and from centralized exchanges.
  - `nft_sale`: Token transfer carrying non-fungible asset identifiers, unit quantity, and consideration metadata.
- **State Progression**:
  - Mempool `pending` (`first_seen_at`) → `included` with snapshot confirmations (`confirmations_k`) → `finalized` (when confirmations reach or exceed chain requirements).
  - Reversible conditions: reverted transactions, dropped transactions, replaced transactions (`replaced_by` pointer), and rare ledger reorgs.
  - Fictional USD market price tables and non-exhaustive threat intelligence labels (`exchange`, `bridge`, `mixer`, `defi`, `gambling`).

---

## 4. SWIFT Correspondent Banking (ISO 20022 pacs.008 / MT103)

- **Network ID**: `swift`
- **Transaction Kinds**: Corporate supplier payments (`supplier_payment`), cross-border remittances (`remittance`), and international trade contracts (`trade_payment`).
- **Corridor Mechanics & Lifecycles**:
  - Hop chains consisting of debtor agent, 0–3 intermediary correspondent banks, and creditor agent.
  - Charge bearer distributions: `SHA` (shared), `OUR` (debtor pays all fees), `BEN` (beneficiary pays fees).
  - Synthetic exchange rate tables providing cross-currency conversion between instructed currency and settlement currency.
  - Snapshot progression: payment instruction (`payment` / `instruction`) → status updates (`ACTC` accepted technical validation → `ACSP` accepted settlement in process with expanding intermediary traces → `ACCC` accepted settlement completed).
  - Exception flows: `RJCT` rejections, legitimate recall requests, and return transfers.

---

## 5. Closed-Loop Digital Wallets

- **Network ID**: `wallet`
- **Transaction Kinds**:
  - `p2p_send` & `p2p_request`: Direct balances transferred between individual wallet accounts.
  - `merchant_payment`: Purchase payments with buyer protection indicators.
  - `funding`: Wallet top-ups provisionally credited from stored bank or card instruments.
  - `withdrawal`: Cash-out to external verified instruments.
  - `refund` & `dispute`: Snapshot dispute lifecycles and linked refund records with parent transaction references.
  - Rare funding failures: Provisional credit is reversed days later (`funding_failed`).

---

## 6. Feed Quirks & Hard Negatives

- **Feed Quirks**:
  - **Disorder & Jitter**: Non-decreasing engine virtual ticks deliver events with realistic variable transit delay.
  - **Duplicates**: Exact duplicate event replay sharing `event_id` and flagged with `truth.duplicate_of`.
  - **Malformed Mutations**: Deliberate field omissions or format violations flagged with `truth.expected_error` to test adapter exception handling.
  - **Naive Timestamps & DE7**: Timezone-free strings and card Transmission Date/Time formatting (`MMDDhhmmss`).
- **Hard Negatives**:
  - 8 realistic, non-fraudulent behaviors that mimic fraud signatures (payroll fan-outs, merchant flash sales, split bills, aggregator settlements, exchange sweep flows, high-chargeback merchants, cross-border travelers, freelancer fan-ins).
  - Always marked `truth.is_fraud = False` and tagged with `truth.lookalike_of`.
