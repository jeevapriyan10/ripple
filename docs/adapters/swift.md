# SWIFT Correspondent Banking Adapter (`swift`)

## Overview

Society for Worldwide Interbank Financial Telecommunication (SWIFT) facilitates secure financial messaging among member institutions across the global correspondent banking network.

### How the Rail Works
1. **Correspondent Banking Chains**:
   Cross-border payments move across chains of bilateral correspondent banking relationships. When a debtor bank does not maintain a direct Nostro/Vostro account with the creditor bank, funds transit one or more intermediate clearing banks (correspondent banks).
2. **Messaging & Tracking**:
   - Payments historically relied on MT103 customer credit transfer messages and MT202/MT202COV cover payments. Modern networks have migrated to ISO 20022 XML formats (`pacs.008` customer credit transfer).
   - SWIFT gpi (Global Payments Innovation) introduced end-to-end tracking via a Unique End-to-end Transaction Reference (UETR), providing deterministic status updates (`ACTC`, `ACSP`, `ACSC`, `ACCC`, `RJCT`).
3. **Settlement**: Settlement is deferred across interbank correspondent ledgers (`CORRESPONDENT_CHAIN`). Wire transfers are non-reversible entitlements (`is_reversible=False`). A recall is a cooperative request that depends on the consent of the receiving financial institution.

---

## Payload Spec v1 (Simplified JSON)

The adapter consumes a documented, simplified JSON format modeling ISO 20022 `pacs.008` and MT103 customer credit transfer concepts:

| Field | Type | Required | Description |
|---|---|---|---|
| `event_type` | String | Yes | `payment`, `status`, `recall_request`, `return`. |
| `message_type` | String | No | Informational message type (e.g. `pacs.008`, `MT103`). Preserved in metadata. |
| `uetr` | String | No | Unique End-to-end Transaction Reference (UUID format). Primary correlation key. |
| `sender_reference` | String | Conditional | Instruction reference (MT103 field :20:). Mandatory when `uetr` is omitted. |
| `creation_datetime` | String | Conditional | Timestamp when the payment instruction was prepared. Mandatory for `payment` events. |
| `value_date` | String | Conditional | Settlement date (`YYYY-MM-DD`). Mandatory for `payment` events. |
| `instructed_amount` | Object | No | Original payment instruction: `{amount: string, currency: string}`. |
| `settlement_amount` | Object | Yes | Interbank settlement amount: `{amount: string, currency: string}`. Mandatory on all events. |
| `exchange_rate` | String | No | FX conversion multiplier between instructed and settlement currencies. |
| `charge_bearer` | String | No | Cost allocation model: `SHA` (shared), `OUR` (debtor pays all), `BEN` (beneficiary pays all). |
| `debtor` | Object | Yes | Payer entity: `account` (`iban` or `number`), optional `type` (`individual` | `business`), `country`. |
| `creditor` | Object | Yes | Payee entity: same structure as debtor. |
| `debtor_agent` | Object | Yes | Debtor servicing bank: `{bic, received_at, charge: {amount, currency}}`. |
| `creditor_agent` | Object | Yes | Creditor receiving bank: same structure as debtor_agent. |
| `intermediaries` | List | No | Ordered list of intermediary clearing banks in funds-flow order: `[{bic, received_at, charge}]`. |
| `status` | String | No | SWIFT gpi status code: `ACTC`, `ACSP`, `ACSC`, `ACCC`, `PDNG`, `RJCT`. |
| `status_reason_code`| String | No | ISO status reason code (e.g. `NARR`, `AC04`). Preserved verbatim. |
| `status_timestamp` | String | Conditional | Timestamp of the status event. Required when status is supplied. |
| `purpose_code` | String | No | 4-letter ISO 20022 external purpose code (e.g. `CORT`, `INTC`, `SALA`). |
| `recall_reason` | String | Conditional | Recall reason code (`FRAD`, `DUPL`, `TECH`, `CUST`). Mandatory for `recall_request`. |
| `recall_requested_at`| String | Conditional | Timestamp of recall request. |
| `return_reason` | String | Conditional | Reason code for returned funds. |
| `returned_at` | String | Conditional | Timestamp when funds were returned. |
| `remittance_info` | String | No | Free-text payment narrative. Discarded immediately for data minimization. |
| `channel` | String | No | Channel identifier. Defaults to `"swift"`. |

---

## Field Mapping (Native &rarr; Universal)

| Native SWIFT Field | Universal Domain Field | Mapping Logic |
|---|---|---|
| `uetr` / `sender_reference`| `Transaction.network_transaction_ref` | `uetr` if present, else `sender_reference` |
| Derived | `Transaction.transaction_id` | `deterministic_uuid("swift", correlation_key)` |
| Derived | `NormalizationResult.correlation_key` | `"swift:" + uetr` if present, else `"swift:ref:" + sender_bic + ":" + ref + ":" + value_date` |
| `event_type` + `status` | `NormalizationResult.lifecycle_stage` | `"instruction"`, `"status_actc"`, `"status_acsp"`, `"status_acsc"`, `"status_pdng"`, `"status_accc"`, `"status_rjct"`, `"recall_requested"`, `"returned"` |
| `settlement_amount` | `Transaction.amount` | `Money(amount=..., currency=curr, scale=scale)` with scale from shared ISO-4217 table |
| Hop charges | `Transaction.fee` | Sum of all hop charges when sharing one currency; otherwise `None` with diagnostic warning |
| `creation_datetime` | `Transaction.initiated_at` | Parsed to UTC. Falls back to status timestamp or `value_date` with warning if absent. |
| `status_timestamp` / `returned_at` | `Transaction.settled_at` | Set for `SETTLED` (`ACCC`) or `REVERSED` (`returned_at`). For ACCC without timestamp, falls back to `value_date` at 00:00 UTC with warning. |
| Direction | `Transaction.direction` | Always `TransactionDirection.TRANSFER` |
| Intermediary Hops | `Transaction.intermediary_hops` | Sequence in funds-flow order: `DEBTOR_AGENT` (seq 1) &rarr; `CORRESPONDENT_BANK` (seq 2..N) &rarr; `CREDITOR_AGENT` (seq N+1) |
| Debtor / Creditor | `Transaction.sender_id` & `receiver_id` | Primary identifier: `IBAN` if present, else `BANK_ACCOUNT` (`"<agent_bic>:<number>"`). `party_id = deterministic_uuid("swift", normalized_id)`. |
| ISO Purpose Code | `Transaction.purpose_code` | 4-letter uppercase string passed through verbatim |
| Wire metadata | `Transaction.network_metadata` | Stores `instructed_amount`, `exchange_rate`, `charge_bearer`, `value_date`, `message_type`, `status_reason_code`, `recall_reason`, `return_reason`, `remittance_info_present`, and `remittance_info_length`. |

---

## Status Mapping Table

| Event Type | Native Status | Universal Status | `settled_at` | Lifecycle Stage |
|---|---|---|---|---|
| `payment` | *(none)* | `INITIATED` | `None` | `instruction` |
| `status` | `ACTC` | `PENDING_AUTHORIZATION` | `None` | `status_actc` |
| `status` | `ACSP` | `PENDING_SETTLEMENT` | `None` | `status_acsp` |
| `status` | `ACSC` | `PENDING_SETTLEMENT` | `None` (debtor settled) | `status_acsc` |
| `status` | `PDNG` | `PENDING_SETTLEMENT` | `None` | `status_pdng` |
| `status` | `ACCC` | `SETTLED` | `status_timestamp` (or `value_date` with warning) | `status_accc` |
| `status` | `RJCT` | `REJECTED` | `None` | `status_rjct` |
| `recall_request` | *(any)* | `DISPUTED` | Preserved | `recall_requested` |
| `return` | *(any)* | `REVERSED` | `returned_at` | `returned` |

### Known Recall Reason Codes
- `FRAD`: `fraudulent_origin`
- `DUPL`: `duplicate_payment`
- `TECH`: `technical_problem`
- `CUST`: `customer_request`
- Any other code is mapped to label `"unknown"`.

---

## Intentionally Discarded Fields (Data Minimization)

To adhere strictly to privacy guidelines, anti-surveillance policies, and data minimization:
- Customer personal names (`debtor.name`, `creditor.name`)
- Customer physical addresses (`debtor.address`, `creditor.address`)
- Free-text remittance notes (`remittance_info`)

These fields are **discarded immediately upon receipt** and appear nowhere in `Party`, `Transaction`, `network_metadata`, logs, or serialized responses.
- The adapter retains only boolean `remittance_info_present` and integer `remittance_info_length` for risk modeling.
- Discarded fields are declared on `SwiftAdapter.discarded_fields` and exposed via `describe()`.

---

## Strict Sensitive Data Rules (Hard Security Guarantees)

1. **Zero IBAN Leaks**:
   - IBANs are validated against ISO 13616 structural rules and MOD-97 checksums.
   - If validation fails, the raised `NormalizationError` names the field (`"debtor.account.iban"`) and cites the reason, but **never echoes the raw IBAN value**.
   - Raw IBANs are strictly prohibited from appearing in exception messages, traceback representations, warnings, logs, and API error response bodies.
2. **Wrap Domain Errors**:
   - All domain entity construction passes through `wrap_domain_errors`, sanitizing Pydantic and domain validation messages to prevent accidental reflection of sensitive account numbers or identifiers.

---

## Known Limitations

1. **Simplified JSON Specification vs. Wire Formats**:
   Real SWIFT network traffic flows over SWIFTNet as FIN MT text messages or ISO 20022 XML messages wrapped in PKI digital signatures. Ripple's adapter targets a documented, simplified JSON format modeling wire concepts. Upstream gateway appliances or messaging switches must parse MT/XML into JSON prior to adapter ingestion.
2. **FI-to-FI and Cover Payments Unsupported**:
   Financial Institution to Financial Institution transfers (`pacs.009`, `MT202`, `MT202COV`) lack retail customer debtor/creditor entities and are rejected with `UnsupportedPayloadError`.
3. **Amounts Differ Along the Correspondent Chain**:
   Cross-border wires frequently experience currency conversions and fee deductions at intermediate hops. The instructed amount, interbank settlement amount, and creditor credited amount may differ. The universal model records `settlement_amount` as `Transaction.amount`, preserving `instructed_amount` and individual hop charges in metadata and hops.
4. **Intermediary Lists May Be Incomplete**:
   In serial payment routing, early status messages may only reflect hops discovered up to that point. Successive snapshots enrich the intermediary list. Each incoming snapshot's hop list completely supersedes previous ones.
5. **Entity Resolution & Multi-Node Representation**:
   A single real-world customer who sends payments via an IBAN in Europe and a domestic national account number in the United States will instantiate as two distinct `Party` nodes. Entity resolution across identifiers is the responsibility of Module 8 (Mesh Entity Resolution).
6. **No Sanctions Screening or Name Matching**:
   Because customer personal names and addresses are discarded for data minimization, Ripple cannot perform sanctions screening, PEP checks, or name matching. Ripple is a graph-topological fraud detection engine, not a sanctions screening product.
7. **BIC Country as Customer Country Proxy**:
   When a customer payload lacks an explicit `country` and does not provide an IBAN, the adapter defaults `country_code` from characters 5-6 of the servicing agent's BIC. This serves only as an institutional proxy and may not match the customer's true residency.
