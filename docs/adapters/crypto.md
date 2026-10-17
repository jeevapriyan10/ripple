# Cryptocurrency Network Adapter (`crypto`)

## Overview

Cryptocurrency and distributed ledger networks execute direct, peer-to-peer, programmable value transfers over decentralized peer-to-peer networks and public consensus mechanisms.

### How the Rail Works
1. **Addressing & Account Models**:
   - **Account-based networks** (e.g. Ethereum / EVM, Solana, Tron) record explicit state balances for accounts/contracts. Value transfers emit direct `(from, to, amount)` transfers or smart contract events (e.g. ERC-20 `Transfer(from, to, value)`).
   - **UTXO networks** (e.g. Bitcoin) record unspent transaction outputs. Raw multi-input / multi-output transactions must undergo upstream UTXO decomposition into logical transfer pairs before reaching this adapter.
2. **Transaction Lifecycle**:
   - **Mempool (`pending`)**: The transaction is signed and broadcast by an account, entering nodes' memory pools awaiting miner/validator selection.
   - **Inclusion / Confirmation (`included`)**: A block containing the transaction is mined or proposed and included in the canonical chain. Successive blocks increment the confirmation count.
   - **Finality (`finalized`)**: Once the chain's consensus threshold or required confirmations are reached, the transaction achieves deterministic economic finality.
   - **Chain Reorganizations (`reorged`)**: If a temporary chain fork is superseded by a longer or higher-weight chain, a previously included transaction may return to pending or disappear.
3. **Settlement**: Public blockchain settlement relies on block consensus (`BLOCK_CONFIRMATION`). Transfers are non-reversible on-chain (`is_reversible=False`).

---

## Payload Spec v1 (JSON)

Each event describes **one single value transfer**:

| Field | Type | Required | Description |
|---|---|---|---|
| `chain` | String | Yes | Blockchain identifier (`ethereum`, `bitcoin`, `solana`, `tron`, or a custom chain with explicit parameters). |
| `tx_hash` | String | Yes | Transaction hash / signature on the native ledger. Serves as correlation base. |
| `transfer_index` | Integer | No | Disambiguator for multiple transfers inside one transaction (e.g. batch ERC-20 transfers). Default `0`. |
| `status` | String | Yes | Lifecycle status: `pending`, `included`, `failed`, `dropped`, `replaced`, `reorged`. |
| `execution_status` | String | No | Smart contract execution result: `success` (default) or `reverted`. |
| `from_address` | String | Yes | Sender public address or smart contract. |
| `to_address` | String | Yes | Recipient public address or smart contract. |
| `asset` | Object | No | Asset descriptor: `{type: "native" | "token" | "nft", symbol, decimals, contract_address, token_id}`. Native assets default from the chain table; tokens require symbol, decimals, and contract address. NFTs require contract address, token_id (string `[A-Za-z0-9]{1,80}`), fixed decimals 0, and optional quantity. |
| `amount` / `amount_raw` | String | Conditional | Exactly one required for native assets and fungible tokens (`amount`: decimal string in whole units; `amount_raw`: integer string in base units). Strictly prohibited for NFTs (use `quantity` instead). Floats strictly prohibited. |
| `quantity` | String | Conditional | Number of NFT items transferred (integer string, default `"1"`). Mandatory/defaulted for NFTs; ERC-721 must be exactly `"1"`. Prohibited for fungible assets. |
| `consideration` | Object | No | NFT marketplace sale price paid in a separate transfer: `{amount: str, symbol: str, usd_value?: str}`. Preserved in `network_metadata` only; never as Money. |
| `sale_tx_hash` | String | No | Transaction hash of the marketplace sale transaction for NFT transfers. Preserved in `network_metadata`. |
| `fee` | Object | No | Gas / network fee: `{amount` or `amount_raw`, `asset}`. Asset defaults to the chain's native currency. |
| `first_seen_at` | String / Int | No | ISO-8601 string or integer epoch seconds when a node first observed the transaction in the mempool. |
| `block_number` | Integer | Conditional | Ledger block or slot number. Required when `status="included"`. |
| `block_time` | String / Int | Conditional | Block header timestamp. Required when `status="included"`. |
| `confirmations` | Integer | Conditional | Current block confirmations. Required when `status="included"`. |
| `finalized` | Boolean | No | Explicit consensus finality flag from upstream indexing node. |
| `finalized_at` | String / Int | No | Timestamp when finality was established. |
| `usd_value` | String / Number | No | External oracle / price-feed USD valuation (decimal string). Mapped to `Money.normalized_value`. For NFTs, populated from `consideration.usd_value` when present. |
| `from_label` / `to_label` | Object | No | External intelligence labels: `{entity: str, category: "exchange" | "mixer" | "bridge" | "defi" | "gambling" | "sanctioned" | "unknown"}`. |
| `from_is_contract` / `to_is_contract` | Boolean | No | Indicates whether address is a smart contract / bytecode account. |
| `token_standard` | String | No | Token protocol standard (e.g. `ERC-20`, `ERC-721`, `ERC-1155`, `SPL`, `TRC-20`). |
| `replaced_by` | String | Conditional | New transaction hash when `status="replaced"` (e.g. RBF / nonce replacement). |
| `nonce` | Integer | No | Account transaction sequence nonce. |
| `source_feed` | String | No | Ingestion source / RPC node provider (e.g. `alchemy`, `quicknode`, `mempool_api`). |
| `address_family` | String | Conditional | One of `evm`, `bitcoin`, `solana`, `tron`. Mandatory when `chain` is custom / unlisted. |
| `required_confirmations` | Integer | Conditional | Confirmation threshold. Mandatory when `chain` is custom / unlisted. |

---

## Field Mapping (Native &rarr; Universal)

| Native Crypto Field | Universal Domain Field | Mapping Logic |
|---|---|---|
| `tx_hash` | `Transaction.network_transaction_ref` | Verbatim transaction hash |
| Derived | `Transaction.transaction_id` | `deterministic_uuid("crypto", chain, tx_hash, str(transfer_index))` |
| Derived | `NormalizationResult.correlation_key` | `"crypto:" + chain + ":" + (tx_hash.lower() if evm else tx_hash) + ":" + transfer_index` |
| `status` + `confirmations` | `NormalizationResult.lifecycle_stage` | `"mempool"`, `"confirmation_<n>"`, `"finalized"`, `"failed"`, `"dropped"`, `"replaced"`, `"reorged"` |
| `amount` / `amount_raw` | `Transaction.amount` | Fungible: `Money(amount=..., currency=symbol.upper(), scale=decimals, asset_class=AssetClass.CRYPTO)`. NFTs: `Money(amount=quantity, currency=symbol.upper(), scale=0, asset_class=AssetClass.CRYPTO)`. |
| `asset` (NFT) | `Transaction.network_metadata` | For NFTs, `asset_id = "<chain>:<contract_address>:<token_id>"` (contract lowercased for EVM). `token_id` and `token_standard` recorded in metadata. |
| `consideration` / `sale_tx_hash` | `Transaction.network_metadata` | Preserved verbatim in metadata. If `consideration.usd_value` is supplied, it populates `Money.normalized_value`. |
| `fee` | `Transaction.fee` | `Money` in fee asset (usually native asset, scale from chain table) |
| `first_seen_at` / `block_time` | `Transaction.initiated_at` | `first_seen_at` if present, else `block_time` with warning |
| `finalized_at` / `block_time` | `Transaction.settled_at` | Only set for `CONFIRMED`; `finalized_at` if present, else `block_time` with warning |
| Direction | `Transaction.direction` | Always `TransactionDirection.TRANSFER` |
| Channel | `Transaction.channel` | Always `"onchain"` |
| `from_address` | `Transaction.sender_id` & `Party` | `CRYPTO_ADDRESS` identifier; `party_id = deterministic_uuid("crypto", family, normalized_address)`. Normalized according to family rules. |
| `to_address` | `Transaction.receiver_id` & `Party` | `CRYPTO_ADDRESS` identifier; same deterministic party identity. |
| `from_label` / `to_label` | `Party.party_type` & `RiskFlags` | Category `"exchange"` &rarr; `PartyType.EXCHANGE`. Category `"sanctioned"` &rarr; `RiskFlags.is_sanctioned = True`. Mule fields remain `None`. |
| `usd_value` | `Money.normalized_value` | Preserved as `Decimal` in universal amount; `network_metadata["normalized_currency"] = "USD"`. |
| Ledger metadata | `Transaction.network_metadata` | Stores `chain`, `tx_hash`, `transfer_index`, `block_number`, `confirmations`, `required_confirmations`, `asset_id`, `token_standard`, `nonce`, `replaced_by`, `reorged`, and approximations made. |

---

## Status Mapping Table

| Native Status | Execution Status | Confirmations / Finality | Universal Status | `settled_at` | Lifecycle Stage |
|---|---|---|---|---|---|
| `pending` | Any | Mempool | `PENDING_SETTLEMENT` | `None` | `mempool` |
| `included` | `reverted` | Any | `FAILED` | `None` (fee retained) | `failed` |
| `included` | `success` | `finalized=True` OR `confirmations >= required` | `CONFIRMED` | `finalized_at` (or `block_time` with warning) | `finalized` |
| `included` | `success` | `confirmations < required` and not finalized | `PENDING_SETTLEMENT` | `None` | `confirmation_<n>` |
| `failed` | Any | Any | `FAILED` | `None` | `failed` |
| `dropped` | Any | Evicted from mempool | `CANCELLED` | `None` | `dropped` |
| `replaced` | Any | Replaced via RBF/nonce | `CANCELLED` | `None` | `replaced` |
| `reorged` | Any | Chain reorg / un-confirmed | `PENDING_SETTLEMENT` | `None` | `reorged` |

---

## Intentionally Discarded Fields (Data Minimization)

The crypto adapter does not discard data fields because public blockchain transactions contain no personal names, addresses, or off-chain customer identifiers. Raw UTXO inputs/outputs and zero-value transfers are rejected upstream rather than silently dropped.

---

## Known Limitations

1. **Transfer-Level Specification (UTXO Decomposition & Clustering Out of Scope)**:
   This adapter operates strictly at the transfer level (single sender, single receiver, single value moved). Account-based chains map 1:1. Bitcoin-style multi-input, multi-output UTXO transactions must undergo decomposition upstream. Wallet clustering heuristics (e.g. common-input ownership, change-address detection) belong in upstream indexing nodes or graph enrichment layers, not in the adapter.
2. **Zero-Value, Self-Transfers, and Contract Creations Unsupported**:
   Smart contract deployments (no `to_address`), self-transfers (`from_address == to_address`), and zero-value transfers (e.g. token approvals, governance voting, address-poisoning dust) violate Module 2 universal domain invariants and are explicitly rejected with `UnsupportedPayloadError`. Consequently, zero-value address poisoning attacks are invisible to this adapter.
3. **Token Symbol Spoofing**:
   ERC-20/SPL token symbols are unverified metadata chosen by token creators. Malicious actors frequently deploy fake tokens with symbols like `USDT` or `USDC`. Downstream fraud detection and scoring layers must key on `network_metadata.asset_id` (`<chain>:<contract_address>`), never on the reported symbol string.
4. **Decimals Above 18 Unsupported**:
   Ripple's universal domain model enforces a maximum precision scale of 18 decimal places (`Money.scale <= 18`). Obscure or non-standard tokens with decimals $> 18$ are rejected with `NormalizationError`.
5. **Party Identity Per Address Family**:
   Party identities are keyed by `(address_family, normalized_address)`. On EVM chains, counterfactual smart contract wallets (e.g. CREATE2 factory contracts) that share the same address across different EVM chains will merge into a single `Party` aggregate.
6. **Simulation Confirmation Thresholds**:
   Built-in confirmation thresholds (Bitcoin: 6, Ethereum: 64, Solana: 32, Tron: 19) are conventional defaults for simulation and testing. They are NOT authoritative financial guarantees. Production environments must configure corridor-specific finality policies.
7. **Chain Reorganization Handling in Mesh**:
   When a transaction is marked `reorged`, the adapter returns `PENDING_SETTLEMENT` with `settled_at=None`, `reorged=True`, and a warning. The downstream Mesh graph storage layer must treat this newer snapshot as superseding previously stored confirmed transactions.
8. **Mempool First-Seen Time Subjectivity**:
   `first_seen_at` is inherently subjective to the vantage point of the observing node. Timestamps between distinct mempool listeners may vary by several seconds.
9. **Shielded & Privacy Coins Unsupported**:
   Privacy-preserving transactions (e.g. Monero, Zcash shielded pools) lack public sender, receiver, and transfer amounts and cannot be represented in Ripple's graph model.
10. **Testnet Addresses Unsupported**:
    Structural address checks enforce mainnet patterns (e.g. Bitcoin `bc1` mainnet bech32; testnet `tb1` is rejected).
11. **No Wash-Trade Pairing (Downstream Job)**:
    Wash-trade detection (pairing an NFT transfer with its corresponding marketplace purchase or consideration payment across two distinct transfers) is a downstream Score/Chain job, not the adapter's responsibility. The adapter records `consideration` and `sale_tx_hash` as metadata only.
12. **Null-Address Super-Node (Mints & Burns)**:
    Mints and burns are represented as standard on-chain transfers from or to the blockchain's null address (e.g. `0x0000000000000000000000000000000000000000`). Mesh graph ingestion should treat the null address as a system super-node.
13. **Token ID Preserved Only for NFT Assets**:
    `token_id` is preserved in `asset_id` (`"<chain>:<contract>:<token_id>"`) and `network_metadata` only for assets of type `"nft"`. Fungible tokens do not retain a `token_id`.
