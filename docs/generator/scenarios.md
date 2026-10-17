# Fraud and Financial Crime Scenarios Catalog

The Ripple Synthetic Transaction Generator incorporates 10 calibrated scenario typologies spanning consumer payments, wholesale interbank clearing, digital wallets, cards, and blockchain assets.

Every scenario generates multi-stage, multi-hop activity graph motifs with realistic temporal delays and distinct role assignments (`victim`, `first_hop`, `layering`, `cash_out`).

---

## 1. UPI Mule Chain (`upi_mule_chain`)

- **Rail(s)**: `upi`
- **Typology**: Authorized Push Payment (APP) fraud followed by rapid multi-hop mule layering.
- **Real-World Mechanism**: A victim is deceived via social engineering or phishing into authorizing a real-time transfer to a primary mule account. The funds are immediately fragmented and forwarded through 2–5 hops of intermediate mule accounts (each forwarding 90–98% within 2–30 minutes) before cash-out at a retail merchant QR code or ATM withdrawal.
- **Graph Motif**: Star fan-in from multiple victims $\to$ single first-hop aggregator $\to$ directed linear path of intermediate layering nodes $\to$ high-outdegree terminal merchant node.
- **Hard Negatives Resembled**:
  - `p2p_bill_split`: Roommates or friends consolidating restaurant bills or rent before paying a landlord or utility.
  - `informal_saving_circle` (Chit fund / ROSCA): Legitimate community pooling and distribution.
- **Adversarial Drift Regime**: `v2_mule_template` shifts linear chains into parallel star fan-out topologies with structured micro-transfers beneath the ₹10,000 U16 monitoring cap.

---

## 2. Fan-In Smurfing & Layering (`fan_in_smurfing`)

- **Rail(s)**: `upi`, `wallet`
- **Typology**: Structuring beneath regulatory reporting thresholds (Smurfing).
- **Real-World Mechanism**: A network of 6–20 recruited smurfs initiate structured transfers just below statutory reporting thresholds ($10,000 / ₹10,000) into a central collector account over 6–48 hours. The collector subsequently transfers the aggregated sum to an institutional broker or overseas correspondent within 24 hours.
- **Graph Motif**: Dense fan-in star directed toward a single collector vertex, followed by high-value outbound consolidation edge.
- **Hard Negatives Resembled**:
  - `crowdfunding_pool`: Legitimate medical or community donation campaigns with high fan-in.
  - `wedding_gifting`: Cultural gift pooling with dozens of relatives sending funds to a couple.

---

## 3. Closed-Loop Wallet Provisional Funding Abuse (`wallet_provisional_funding_abuse`)

- **Rail(s)**: `wallet`
- **Typology**: Exploit of provisional credit on ACH or bank-rail top-ups.
- **Real-World Mechanism**: A perpetrator links a compromised bank account to a closed-loop digital wallet and initiates an instant funding request. The wallet issues immediate provisional spendable credit. Within minutes to hours, the perpetrator executes rapid outbound P2P transfers or external cash-outs. 2–5 days later, the origin bank returns the ACH transfer as unauthorized (`funding_failed`), leaving the origin wallet account in a negative balance.
- **Graph Motif**: External funding node $\to$ immediate burst of outward directed edges $\to$ delayed compensating reversal edge.
- **Hard Negatives Resembled**:
  - `funding_followed_by_retail_spend`: Legitimate wallet top-up followed by retail spend, with an occasional mistaken bank dispute.

---

## 4. Card Testing and Velocity Abuse (`card_testing`)

- **Rail(s)**: `card`
- **Typology**: Automated botnet enumeration of stolen credit card numbers.
- **Real-World Mechanism**: Fraudsters test lists of compromised card credentials against merchant payment gateways using low-value authorizations ($0.50–$3.00) in rapid succession (sub-second to few seconds). 60–90% of requests decline with invalid CVV or expired date. Once an approval is obtained, larger purchases ($150–$900) are immediately attempted.
- **Graph Motif**: High-frequency multi-edge burst from diverse PAN tokens to a targeted merchant MID within seconds, exhibiting low approval ratios.
- **Hard Negatives Resembled**:
  - `ticket_flash_sale`: Legitimate high-velocity checkout surges during concert or travel flash sales with elevated decline rates due to bank fraud filters.

---

## 5. Chargeback Wave Fraud (`chargeback_fraud_merchant`)

- **Rail(s)**: `card`
- **Typology**: Bust-out merchant fraud or counterfeit digital storefronts.
- **Real-World Mechanism**: A collusive or compromised merchant processes hundreds of card-not-present (CNP) purchases over a 14–30 day window, capturing cardholder funds. 30–60 days later, cardholders discover unauthorized charges and lodge chargebacks, causing a massive synchronized chargeback wave that exceeds card scheme thresholds (e.g. Visa/Mastercard VFMP/ECP).
- **Graph Motif**: Disconnected cardholders $\to$ central merchant MID, followed by delayed inward chargeback edges 30–60 days later.
- **Hard Negatives Resembled**:
  - `seasonal_subscription_renewal`: Legitimate annual billing surges followed by a small baseline rate of customer service refund requests.

---

## 6. Crypto Peeling Chain & Layering (`crypto_peeling_layering`)

- **Rail(s)**: `crypto`
- **Typology**: Peeling chain laundering of stolen digital assets.
- **Real-World Mechanism**: A high-value theft or ransom pool is washed through 8–30 hops of unhosted ephemeral wallets. At each hop, a small fraction (1–8%) is "peeled" off to an OTC desk or nested exchange, while the bulk remainder is forwarded to a freshly generated unhosted address. Exact on-chain value conservation holds strictly: $\text{Current} = \text{Peel} + \text{Forward} + \text{Fee}$.
- **Graph Motif**: Long directed chain with single inputs, dual outputs (one continuation hop, one terminal peel hop).
- **Hard Negatives Resembled**:
  - `exchange_cold_storage_sweep`: Legitimate exchange automated sweeping of hot-wallet deposit addresses into omnibus cold storage vaults.

---

## 7. NFT Wash Trading Ring (`nft_wash_trading`)

- **Rail(s)**: `crypto`
- **Typology**: Synthetic market manipulation and artificial volume inflation.
- **Real-World Mechanism**: A closed ring of 3–6 collusive unhosted wallets repeatedly buy and sell the same non-fungible token (NFT) or illiquid asset among themselves at escalating prices, creating the illusion of organic demand and price discovery before dumping onto an external buyer.
- **Graph Motif**: Closed directed cycle / ring with reciprocal edges where asset custody cycles while consideration strictly escalates.
- **Hard Negatives Resembled**:
  - `nft_collector_barter`: Genuine community trading and swaps among high-reputation digital art collectors.

---

## 8. Account Takeover & Rapid Drain (`account_takeover_drain`)

- **Rail(s)**: `upi`, `wallet`
- **Typology**: Credential stuffing or SIM swap resulting in immediate balance liquidation.
- **Real-World Mechanism**: A dormant or moderately active user account suddenly authenticates from an anomalous new device identifier and foreign IP subnet. Within 2–15 minutes of login, password/PIN changes or new beneficiary additions occur, followed immediately by rapid back-to-back outbound transfers draining the account balance.
- **Graph Motif**: Telemetry anomaly on a previously stable vertex immediately preceding sudden high-degree outbound flow.
- **Hard Negatives Resembled**:
  - `device_upgrade_moving`: Legitimate user upgrading their smartphone while traveling or moving residences.

---

## 9. SWIFT Cross-Border Structuring (`swift_structuring_layering`)

- **Rail(s)**: `swift`
- **Typology**: International trade-based money laundering (TBML) and correspondent hop structuring.
- **Real-World Mechanism**: A network of corporate shell entities registered across 3–5 jurisdictions (e.g. UAE, Singapore, Cyprus, Panama) route large wire transfers structured just beneath regulatory reporting thresholds ($49,500 vs $50,000 PTR/CTR) with generic commercial invoice codes (SUPP, GDDS). Dwell times are shortened to 1–2 business days before onward wiring.
- **Graph Motif**: Directed path across international jurisdiction codes with intermediate correspondent bank agents and consistent structured amounts.
- **Hard Negatives Resembled**:
  - `psp_settlement_forwarding`: Legitimate international payment service providers forwarding merchant settlement batches across jurisdictions.

---

## 10. Multi-Rail Cross-Network Laundering (`cross_network_laundering`)

- **Rail(s)**: `wallet` $\to$ `swift` $\to$ `crypto`
- **Typology**: Complex cross-network layering crossing retail, wholesale, and decentralized rails.
- **Real-World Mechanism**:
  1. *Stage 1 (Retail)*: Victim account compromised via wallet P2P transfer.
  2. *Stage 2 (Wholesale)*: Launderer consolidates funds and wires via international SWIFT MT103 / pacs.008 to a regulated virtual asset service provider (VASP).
  3. *Stage 3 (Decentralized)*: Funds credited at exchange are converted into crypto and withdrawn to unhosted peeling chain addresses.
- **Graph Motif**: Multi-layer heterogeneous path traversing three distinct rail subgraphs connected through multi-rail actor identities.
- **Hard Negatives Resembled**:
  - `legitimate_cross_border_crypto_remittance`: Expatriate workers funding an exchange via bank transfer to remit funds to family abroad.
