# Calibration Profile and Provenance

> [!WARNING]
> **Synthetic Data Disclaimer**: Every parameter in this calibration profile is an **assumption** (`provenance: "assumption"`, `citation: null`).
> **NO PARAMETER IS CALIBRATED TO REAL TRAFFIC OR REAL-WORLD DATASETS.**
> Real-world financial network aggregates (NPCI, RBI, Card Schemes, SWIFT, or Blockchain Analytics) vary wildly by region and tier.
> Realism in Ripple is achieved via mathematical dynamics (heavy-tailed Pareto/LogNormal distributions, diurnal circadian rhythms, business-day settlement lifecycles, and multi-stage state machines), NOT by claimed real-world statistics.

---

## Honesty & Calibration Policy (Rule R4)

In accordance with Rule R4 of the Generator Design:
1. Every calibration parameter is explicitly typed with `value`, `unit`, `provenance`, `citation`, and `note`.
2. Any parameter marked `provenance: "published"` strictly requires an authoritative human citation. Because no external data was copied or fabricated, all default parameters are marked `provenance: "assumption"` with `citation: null`.
3. If an organization calibrates Ripple against proprietary or regulatory aggregate reports, citations and values can be supplied in a customized profile using `GeneratorConfig(calibration="custom_profile")`.

---

## Default Calibration Catalog (`calibration/default.json`)

### 1. World Composition (`world`)

| Parameter | Value | Unit | Provenance | Note |
|---|---|---|---|---|
| `world.individual_ratio` | `0.85` | ratio | assumption | Assumed fraction of retail consumer population |
| `world.merchant_ratio` | `0.10` | ratio | assumption | Assumed fraction of commercial merchant entities |
| `world.business_ratio` | `0.03` | ratio | assumption | Assumed fraction of corporate / enterprise entities |
| `world.exchange_count` | `4` | count | assumption | Number of centralized cryptocurrency exchanges in the simulated ecosystem |
| `world.bridge_count` | `2` | count | assumption | Number of cross-chain bridges |
| `world.mixer_count` | `2` | count | assumption | Number of cryptocurrency mixing / privacy pools |
| `world.defi_count` | `3` | count | assumption | Number of decentralized finance protocols |
| `world.gambling_count` | `2` | count | assumption | Number of high-risk gambling platforms |
| `world.psp_count` | `3` | count | assumption | Number of third-party payment service providers |
| `world.mule_pool_fraction` | `0.05` | ratio | assumption | Fraction of individuals eligible for mule recruitment |
| `world.burner_fraction` | `0.30` | ratio | assumption | Fraction of mules that are newly incubated burner accounts |
| `world.cross_network_link_rate` | `0.40` | ratio | assumption | Probability an actor reuses identical phone or email across networks |
| `world.activity_pareto_alpha` | `1.8` | exponent | assumption | Pareto shape parameter governing heavy-tailed individual activity |
| `world.merchant_zipf_s` | `1.2` | exponent | assumption | Zipf exponent for merchant popularity |

### 2. Ingestion Latencies & Feed Dynamics (`feed`)

| Parameter | Value | Unit | Provenance | Note |
|---|---|---|---|---|
| `feed.upi_base_latency_seconds` | `0.5` | seconds | assumption | Assumed network transport latency for UPI |
| `feed.card_base_latency_seconds` | `1.2` | seconds | assumption | Assumed network transport latency for Card authorization |
| `feed.crypto_base_latency_seconds` | `4.0` | seconds | assumption | Assumed network transport latency for Crypto gossip propagation |
| `feed.swift_base_latency_seconds` | `30.0` | seconds | assumption | Assumed network transport latency for SWIFT message exchange |
| `feed.wallet_base_latency_seconds` | `0.8` | seconds | assumption | Assumed network transport latency for closed-loop wallet events |

### 3. Diurnal & Weekday Rhythms (`diurnal`)

| Parameter | Value | Unit | Provenance | Note |
|---|---|---|---|---|
| `diurnal.weekday_multiplier` | `1.0` | multiplier | assumption | Monday-Friday activity base multiplier |
| `diurnal.weekend_multiplier` | `0.65` | multiplier | assumption | Weekend retail dropoff multiplier |
| `diurnal.peak_hour_weight` | `1.6` | weight | assumption | Daytime peak activity weight (afternoon / evening) |
| `diurnal.trough_hour_weight` | `0.15` | weight | assumption | Nighttime minimum activity weight (02:00-05:00 local time) |

### 4. Ground Truth Labels (`labels`)

| Parameter | Value | Unit | Provenance | Note |
|---|---|---|---|---|
| `labels.label_coverage` | `0.85` | ratio | assumption | Fraction of fraudulent events that eventually receive ground truth labels |
| `labels.label_delay_median_hours` | `48.0` | hours | assumption | Median elapsed hours between fraud event and investigative confirmation |
| `labels.label_delay_sigma` | `1.2` | sigma | assumption | Log-normal scale parameter for label confirmation delay |

### 5. Network Traffic Profiles

Each network rail is calibrated with base activity rates, heavy-tailed amount parameters (lognormal median and sigma), psychological rounding rates, cap limits, and lifecycle outcomes:
- **UPI (`upi`)**: P2P vs merchant QR vs collect requests, response code distributions (00 success, U16 limit exceed, U30, ZM), timeout/pending lifecycles.
- **Card (`card`)**: POS vs ecommerce vs cross-border vs ATM, approval vs decline codes (00, 05, 51, 61), settlement timing (T+1/T+2 business days), MDR fee fractions, dispute/chargeback probabilities.
- **Crypto (`crypto`)**: Native transfers, stable tokens, exchange sweeps, chain-specific block times, reorg and replacement rates, threat intelligence coverage.
- **SWIFT (`swift`)**: Correspondent hop chains (0–3 hops), charge bearer splits (SHA/OUR/BEN), value-date delays, FX synthetic conversions, status progression (ACTC → ACSP → ACCC).
- **Wallet (`wallet`)**: P2P sends, merchant payments, provisional funding, funding failure reversals, dispute hold states.

### 6. Hard Negatives (`hard_negatives`)

| Parameter | Value | Unit | Provenance | Note |
|---|---|---|---|---|
| `hard_negatives.rate` | `0.03` | ratio | assumption | Fraction of background volume dedicated to benign look-alikes |
| `hard_negatives.split_bill_count_min` | `3` | count | assumption | Minimum friends splitting a bill |
| `hard_negatives.split_bill_count_max` | `6` | count | assumption | Maximum friends splitting a bill |
| `hard_negatives.payroll_employee_min` | `4` | count | assumption | Minimum employees in a payroll fanout |
| `hard_negatives.payroll_employee_max` | `12` | count | assumption | Maximum employees in a payroll fanout |
| `hard_negatives.flash_sale_buyer_count` | `8` | count | assumption | Burst size for benign merchant flash sales |
