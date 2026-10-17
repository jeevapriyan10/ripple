"""Dataset reporting, graph metrics aggregation, and univariate separability check.

Computes rank-based AUC in pure Python (no numpy / scipy) to evaluate feature separability
against two failure modes:
1. Too easy: AUC > 0.90 on a single trivial feature (e.g. node degree separates fraud perfectly).
2. Too random: All features remain near chance level (AUC < 0.55).
"""

import gzip
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def compute_rank_auc(positives: list[float], negatives: list[float]) -> float:
    """Compute rank-based Area Under the ROC Curve (Mann-Whitney U statistic) in pure Python.

    Handles ties by assigning the average rank. Returns AUC in [0.5, 1.0].
    """
    n_pos = len(positives)
    n_neg = len(negatives)
    if n_pos == 0 or n_neg == 0:
        return 0.5

    # Pair each score with its label: 1 for positive, 0 for negative
    combined: list[tuple[float, int]] = [(p, 1) for p in positives] + [(n, 0) for n in negatives]
    combined.sort(key=lambda x: x[0])

    # Assign ranks with tie averaging (1-indexed)
    ranks: list[float] = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j < len(combined) - 1 and combined[j][0] == combined[j + 1][0]:
            j += 1
        avg_rank = (i + 1 + j + 1) / 2.0
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1

    # Sum of ranks for positive class
    sum_pos_ranks = sum(ranks[idx] for idx, (_, label) in enumerate(combined) if label == 1)

    u_stat = sum_pos_ranks - (n_pos * (n_pos + 1)) / 2.0
    auc = u_stat / (n_pos * n_neg)

    # AUC direction: if feature inversely predicts fraud, invert to reflect separability power
    return max(auc, 1.0 - auc)


@dataclass
class DatasetReport:
    """Comprehensive dataset report analyzing volume, lifecycles, and separability."""

    dataset_name: str
    manifest: dict[str, Any]
    counts_by_network: dict[str, int]
    event_type_counts: dict[str, int]
    fraud_count: int
    total_events: int
    hard_negative_count: int
    scenario_counts: dict[str, int]
    regime_counts: dict[str, int]
    separability_top5: list[dict[str, Any]] = field(default_factory=list)
    separability_warnings: list[str] = field(default_factory=list)
    calibration_summary_line: str = "0 parameters, 0 published, all assumptions"

    @property
    def fraud_fraction(self) -> float:
        return (self.fraud_count / self.total_events) if self.total_events > 0 else 0.0

    @property
    def hard_negative_fraction(self) -> float:
        return (self.hard_negative_count / self.total_events) if self.total_events > 0 else 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert report metrics into a dictionary."""
        return {
            "dataset_name": self.dataset_name,
            "total_events": self.total_events,
            "fraud_count": self.fraud_count,
            "fraud_fraction": round(self.fraud_fraction, 4),
            "hard_negative_count": self.hard_negative_count,
            "hard_negative_fraction": round(self.hard_negative_fraction, 4),
            "counts_by_network": self.counts_by_network,
            "event_type_counts": self.event_type_counts,
            "scenario_counts": self.scenario_counts,
            "regime_counts": self.regime_counts,
            "separability_top5": self.separability_top5,
            "separability_warnings": self.separability_warnings,
            "calibration_summary": self.calibration_summary_line,
        }

    def to_markdown(self) -> str:
        """Format report into clean GitHub Flavored Markdown."""
        lines = [
            f"# Dataset Report: `{self.dataset_name}`",
            "",
            "## Summary Metrics",
            f"- **Total Events**: {self.total_events:,}",
            (
                f"- **Fraud Prevalence**: {self.fraud_fraction * 100:.2f}% "
                f"({self.fraud_count:,} events)"
            ),
            (
                f"- **Hard Negatives**: {self.hard_negative_fraction * 100:.2f}% "
                f"({self.hard_negative_count:,} events)"
            ),
            f"- **Calibration Provenance**: {self.calibration_summary_line}",
            "",
            "## Network Volume Breakdown",
            "| Network | Event Count | Share |",
            "|---|---|---|",
        ]
        for net, cnt in sorted(self.counts_by_network.items()):
            share = (cnt / self.total_events * 100) if self.total_events > 0 else 0.0
            lines.append(f"| `{net}` | {cnt:,} | {share:.1f}% |")

        lines.extend(
            [
                "",
                "## Fraud Scenarios & Regimes",
                "| Scenario | Count |",
                "|---|---|",
            ]
        )
        for sc, sc_cnt in sorted(self.scenario_counts.items()):
            lines.append(f"| `{sc}` | {sc_cnt:,} |")

        lines.extend(
            [
                "",
                "## Univariate Separability Check (Pure-Python AUC)",
                "| Feature | Separability (AUC) | Risk Warning |",
                "|---|---|---|",
            ]
        )
        for feat in self.separability_top5:
            auc = feat["auc"]
            warn = (
                "🚨 Too Easy" if auc > 0.90 else ("⚠️ Unlearnable" if auc < 0.55 else "✅ Balanced")
            )
            lines.append(f"| `{feat['feature']}` | {auc:.3f} | {warn} |")

        if self.separability_warnings:
            lines.extend(["", "### Separability Warnings"])
            for w in self.separability_warnings:
                lines.append(f"- ⚠️ {w}")

        return "\n".join(lines)


def analyze_dataset(dataset_path: Path | str) -> DatasetReport:
    """Analyze a generated dataset and evaluate its topological separability."""
    path = Path(dataset_path)
    manifest_path = path / "manifest.json"
    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)

    events_path = path / "events.jsonl.gz"
    truth_path = path / "truth.jsonl.gz"

    total_events = 0
    fraud_count = 0
    hn_count = 0
    counts_by_network: dict[str, int] = {}
    event_type_counts: dict[str, int] = {}
    scenario_counts: dict[str, int] = {}
    regime_counts: dict[str, int] = {}

    # Party aggregation maps
    party_in_count: dict[str, int] = {}
    party_out_count: dict[str, int] = {}
    party_in_amount: dict[str, float] = {}
    party_out_amount: dict[str, float] = {}
    party_in_peers: dict[str, set[str]] = {}
    party_out_peers: dict[str, set[str]] = {}
    party_is_mule: dict[str, bool] = {}

    with (
        gzip.open(events_path, "rt", encoding="utf-8") as ev_f,
        gzip.open(truth_path, "rt", encoding="utf-8") as tr_f,
    ):
        for ev_line, tr_line in zip(ev_f, tr_f, strict=True):
            if not ev_line.strip() or not tr_line.strip():
                continue
            total_events += 1
            ev_data = json.loads(ev_line)
            tr_data = json.loads(tr_line)

            net = tr_data.get("network_id", "unknown")
            counts_by_network[net] = counts_by_network.get(net, 0) + 1

            stg = tr_data.get("stage", "unknown")
            event_type_counts[stg] = event_type_counts.get(stg, 0) + 1

            is_fr = tr_data.get("is_fraud", False)
            if is_fr:
                fraud_count += 1
                sc_id = tr_data.get("pattern") or tr_data.get("scenario_id") or "unspecified"
                scenario_counts[sc_id] = scenario_counts.get(sc_id, 0) + 1

            if tr_data.get("hard_negative", False):
                hn_count += 1

            reg = tr_data.get("regime", "baseline")
            regime_counts[reg] = regime_counts.get(reg, 0) + 1

            # Extract party interactions
            snd = tr_data.get("sender_actor_id")
            rcv = tr_data.get("receiver_actor_id")
            amt = float(ev_data.get("payload", {}).get("amount", 0.0) or 0.0)

            snd_role = tr_data.get("sender_role")
            rcv_role = tr_data.get("receiver_role")

            if snd:
                party_out_count[snd] = party_out_count.get(snd, 0) + 1
                party_out_amount[snd] = party_out_amount.get(snd, 0.0) + amt
                if rcv:
                    party_out_peers.setdefault(snd, set()).add(rcv)
                if snd_role and snd_role in {"FIRST_HOP", "LAYERING", "CASH_OUT"}:
                    party_is_mule[snd] = True
                elif snd not in party_is_mule:
                    party_is_mule[snd] = False

            if rcv:
                party_in_count[rcv] = party_in_count.get(rcv, 0) + 1
                party_in_amount[rcv] = party_in_amount.get(rcv, 0.0) + amt
                if snd:
                    party_in_peers.setdefault(rcv, set()).add(snd)
                if rcv_role and rcv_role in {"FIRST_HOP", "LAYERING", "CASH_OUT"}:
                    party_is_mule[rcv] = True
                elif rcv not in party_is_mule:
                    party_is_mule[rcv] = False

    # Extract univariate features per party
    all_parties = list(party_is_mule.keys())
    feature_names = [
        "in_count",
        "out_count",
        "distinct_counterparties_in",
        "distinct_counterparties_out",
        "total_in_amount",
        "total_out_amount",
        "forward_ratio",
    ]

    feature_aucs: list[dict[str, Any]] = []
    separability_warnings: list[str] = []

    if len(all_parties) >= 10:
        pos_indices = [p for p in all_parties if party_is_mule[p]]
        neg_indices = [p for p in all_parties if not party_is_mule[p]]

        if pos_indices and neg_indices:
            for feat in feature_names:
                pos_vals: list[float] = []
                neg_vals: list[float] = []

                for p in all_parties:
                    val = 0.0
                    if feat == "in_count":
                        val = float(party_in_count.get(p, 0))
                    elif feat == "out_count":
                        val = float(party_out_count.get(p, 0))
                    elif feat == "distinct_counterparties_in":
                        val = float(len(party_in_peers.get(p, set())))
                    elif feat == "distinct_counterparties_out":
                        val = float(len(party_out_peers.get(p, set())))
                    elif feat == "total_in_amount":
                        val = party_in_amount.get(p, 0.0)
                    elif feat == "total_out_amount":
                        val = party_out_amount.get(p, 0.0)
                    elif feat == "forward_ratio":
                        in_amt = party_in_amount.get(p, 0.0)
                        out_amt = party_out_amount.get(p, 0.0)
                        val = (out_amt / in_amt) if in_amt > 0.0 else 0.0

                    if party_is_mule[p]:
                        pos_vals.append(val)
                    else:
                        neg_vals.append(val)

                auc = compute_rank_auc(pos_vals, neg_vals)
                feature_aucs.append({"feature": feat, "auc": round(auc, 4)})

    feature_aucs.sort(key=lambda x: x["auc"], reverse=True)
    top5 = feature_aucs[:5]

    # Evaluate separability sanity bounds
    if top5:
        max_auc = top5[0]["auc"]
        if max_auc > 0.90:
            separability_warnings.append(
                f"Feature '{top5[0]['feature']}' exceeds AUC 0.90 ({max_auc:.3f}). "
                "Data may be too trivially separable."
            )
        if all(f["auc"] < 0.55 for f in top5):
            separability_warnings.append(
                "All evaluated features have AUC < 0.55. Data may be too random / unlearnable."
            )

    prov = manifest.get("provenance_summary", {})
    assumptions_cnt = prov.get("assumption", 0)
    published_cnt = prov.get("published", 0)
    total_params = assumptions_cnt + published_cnt
    summary_line = f"{total_params} parameters, {published_cnt} published, all assumptions"

    return DatasetReport(
        dataset_name=manifest.get("dataset_name", path.name),
        manifest=manifest,
        counts_by_network=counts_by_network,
        event_type_counts=event_type_counts,
        fraud_count=fraud_count,
        total_events=total_events,
        hard_negative_count=hn_count,
        scenario_counts=scenario_counts,
        regime_counts=regime_counts,
        separability_top5=top5,
        separability_warnings=separability_warnings,
        calibration_summary_line=summary_line,
    )
