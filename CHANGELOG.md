# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-17

### Added
- **Domain Foundation (M01, M02)**:
  - Universal graph-native payment domain model with exact `Decimal` precision (0–18 scale). Floats prohibited in financial paths.
  - Core domain entities: `Money`, `Currency`, `PartyIdentifier`, `Party`, `RiskFlags`, `Transaction`, `IntermediaryHop`, and `FlagEvent`.
  - Multi-hop graph projection (`ROUTED_THROUGH`, `TRANSACTION`, `OWNS_IDENTIFIER`) and `TransactionSubgraph` projection containers.
  - Cloud-native monorepo scaffold with Docker Compose, FastAPI backend, React 18 frontend, and structured JSON logging.
- **Network Adapters & Conformance (M03, M04, M05, M06)**:
  - Pure, deterministic, stateless `NetworkAdapter` contract with zero I/O and zero clock reads.
  - Automated 21-point Adapter Conformance Suite (`CF-01` through `CF-21`) covering contract surface, purity AST scanning, time discipline, caller input immutability, leak scanning, and deterministic mutation fuzzing.
  - **UPI Adapter (`upi`)**: Virtual Payment Address (VPA) resolution, P2P/merchant flows, instant settlement, and NPCI status codes.
  - **Card Adapter (`card`)**: Two-stage deferred settlement (Auth $\to$ Capture), PAN tokenization, Luhn validation, CVV/expiry sanitization, and 120-day dispute lifecycles.
  - **Crypto Adapter (`crypto`)**: Multi-chain support (EVM, Bitcoin, Solana, Tron), smart contract tokens (ERC-20, SPL, TRC-20), mempool $\to$ finalized block confirmation lifecycle.
  - **SWIFT Adapter (`swift`)**: Correspondent banking chains (pacs.008 / MT103), mod-97 IBAN checksum validation, BIC agent routing, and gpi payment status tracking.
  - **Wallet Adapter (`wallet`)**: Closed-loop digital wallet ledgers, P2P send/request, funding/withdrawal, provisional credit, buyer-protection disputes, and automated credential stripping.
- **Multi-Network Synthetic Transaction Generator (M07)**:
  - Bit-identical deterministic simulation engine powered by isolated split-mix counter PRNG (`RngFactory`) and priority-queue scheduling.
  - Synthetic safety enforcement: `+999` non-routable phone numbers, mod-97 valid synthetic IBANs, corrupted Bitcoin Base58 checksums, and strict prohibition of Luhn-valid PANs.
  - Five pure renderers emitting native payload formats matching all five adapter specifications.
  - Calibration catalog (`default.json`) with strict honesty metadata (`provenance: "assumption"`, `citation: null`).
  - Organic background traffic planners and 8 hard-negative behavioral look-alikes across all rails.
  - 10 complex multi-hop financial crime typologies:
    1. `upi_mule_chain`: APP fraud fan-in $\to$ rapid multi-hop layering $\to$ merchant cash-out.
    2. `fan_in_smurfing`: Sub-threshold structuring into central collector $\to$ institutional exit.
    3. `wallet_provisional_funding_abuse`: Provisional credit exploitation $\to$ rapid outbound transfer $\to$ delayed funding failure.
    4. `card_testing`: Botnet velocity testing with high decline ratios $\to$ immediate high-value approvals.
    5. `chargeback_fraud_merchant`: Bust-out merchant sales surge $\to$ synchronized 30–60 day chargeback wave.
    6. `crypto_peeling_layering`: 8–30 hop unhosted peeling chain with exact `Decimal` value conservation.
    7. `nft_wash_trading`: Closed ring cycle with escalating prices.
    8. `account_takeover_drain`: Rogue device/IP credential stuffing $\to$ immediate account balance liquidation.
    9. `swift_structuring_layering`: Shell corporate entities across 3–5 jurisdictions with structured wire amounts.
    10. `cross_network_laundering`: Multi-rail traversal (Wallet $\to$ SWIFT $\to$ Crypto exchange $\to$ unhosted peeling).
  - Adversarial drift regimes (`v2_mule_template`, `v2_card_testing`) with abrupt or gradual sigmoid transition curves.
  - Statistical separability reporting (`analyze_dataset`) with pure-Python rank-based AUC (Mann-Whitney U) and anti-shortcut guardrails.
  - Temporal train/val/test splitting with embargo purges to prevent future information leakage.
  - Developer CLI (`python -m ripple.generator`) and FastAPI REST endpoints (`/generator/scenarios`, `/generator/preview`, `/datasets`).
