# Synthetic Transaction Generator Limitations & Assumptions

While the Ripple Synthetic Transaction Generator is calibrated against published empirical studies and central bank payment statistics, users and researchers must consider the following known limitations when interpreting downstream model evaluation results.

---

## 1. Stationary Actor Population and Identity Graphs

- **Assumption**: The simulated population of actors (`individuals`, `merchants`, `businesses`, `exchanges`, `banks`) is generated at time $t_0$ and remains structurally fixed throughout the simulation duration.
- **Limitation**: In live production payment systems, new account onboarding occurs dynamically, accounts close or churn, and identities merge or split. The current generator does not model dynamic new-account onboarding surges or long-term entity lifecycle churn during the simulation window.

---

## 2. Unobserved Legs and Cash Off-Ramps

- **Assumption**: Criminal proceeds leaving the formal banking or digital asset ecosystem are modeled as terminal "cash-out" events (e.g. ATM withdrawal or retail merchant POS purchase).
- **Limitation**: The physical cash economy (cash courier, hawala, underground physical exchange) is unobserved in digital transaction ledgers. Downstream GNNs cannot track funds once they pass a terminal cash-out node.

---

## 3. Regulatory Threshold Invariance

- **Assumption**: Statutory currency reporting thresholds (e.g. $10,000 for US FinCEN CTRs, ₹10,000 / ₹50,000 for Indian FIU-IND thresholds) are modeled as static parameters.
- **Limitation**: Real-world adversaries adapt continuously to changing internal institutional rule engine thresholds that are often lower and more dynamic than public statutory limits.

---

## 4. Average Empirical Diurnal Curves

- **Assumption**: Diurnal time-of-day curves and weekday/weekend seasonality multipliers are applied uniformly across actors within each network according to country/timezone profiles.
- **Limitation**: Idiosyncratic localized micro-events (such as regional holidays, power/internet outages, local festivals, or geopolitical disruptions) are not modeled unless explicitly configured via custom calibration overrides.

---

## 5. Network Independence in Organic Volume

- **Assumption**: Organic background payment volume across the 5 networks is planned by separate specialized source planners (`UpiTrafficPlanner`, `CardTrafficPlanner`, etc.), with cross-network link rates governed by actor network participation.
- **Limitation**: Macroeconomic substitutes (such as a card network outage causing consumers to switch en masse to UPI or cash) are not dynamically simulated.

---

## 6. Model Evaluation Caveats

- **Separability Warning**: High performance (e.g. AUC > 0.95) on the standard preset may indicate that the GNN is learning generator artifacts rather than generalizable topological motifs. Users should always benchmark models against the `hard_negatives` subset and evaluate under `drift` regimes with adversarial template shifts.
