"""Ground truth label timing, application, point-in-time querying, and temporal splitting.

Prevents label leakage (Rule R3):
- Labels must NEVER feed features at inference.
- Training datasets and feature extraction must strictly use point-in-time (as-of) queries.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ripple.adapters.contract import NormalizationResult
from ripple.domain.party import MuleRole
from ripple.generator.truth import EventTruth, GeneratedEvent, role_precedence


def apply_ground_truth(result: NormalizationResult, truth: EventTruth) -> None:
    """Mutate normalized transaction and parties with ground truth metadata.

    Victims get is_mule=False with role VICTIM.
    Mule intermediaries/cash-outs get is_mule=True with their specific role.
    """
    if result.transaction is not None:
        result.transaction.is_fraud = truth.is_fraud
        result.transaction.fraud_pattern = truth.pattern

    # Map roles onto sender and receiver parties if present
    for p in result.parties:
        role_to_apply: str | None = None
        if truth.sender_role and truth.sender_actor_id in p.party_id:
            role_to_apply = truth.sender_role
        elif truth.receiver_role and truth.receiver_actor_id in p.party_id:
            role_to_apply = truth.receiver_role

        if role_to_apply is not None:
            m_role = MuleRole(role_to_apply)
            p.mule_role = m_role
            p.is_mule = m_role in {MuleRole.FIRST_HOP, MuleRole.LAYERING, MuleRole.CASH_OUT}


def transaction_labels_as_of(
    truths: Iterable[EventTruth],
    as_of_time: datetime,
) -> dict[str, dict[str, Any]]:
    """Return point-in-time transaction labels available on or before as_of_time.

    Fraud events whose label_available_at > as_of_time are reported as unconfirmed (is_fraud=False).
    """
    labels: dict[str, dict[str, Any]] = {}
    for t in truths:
        # If label is confirmed and available at or before as_of_time
        is_confirmed_fraud = (
            t.is_fraud and t.label_available_at is not None and t.label_available_at <= as_of_time
        )
        labels[t.logical_txn_id] = {
            "is_fraud": is_confirmed_fraud,
            "pattern": t.pattern if is_confirmed_fraud else None,
        }
    return labels


def party_labels_as_of(
    truths: Iterable[EventTruth],
    actor_to_party_map: dict[str, str],
    as_of_time: datetime,
) -> dict[str, dict[str, Any]]:
    """Return point-in-time party roles available on or before as_of_time.

    Resolves conflicting roles across multiple events using role_precedence().
    """
    roles_by_party: dict[str, list[str]] = {}

    for t in truths:
        if not t.is_fraud:
            continue
        # Check label availability
        if t.label_available_at is None or t.label_available_at > as_of_time:
            continue

        if t.sender_role:
            party_id = actor_to_party_map.get(t.sender_actor_id, t.sender_actor_id)
            roles_by_party.setdefault(party_id, []).append(t.sender_role)
        if t.receiver_role:
            party_id = actor_to_party_map.get(t.receiver_actor_id, t.receiver_actor_id)
            roles_by_party.setdefault(party_id, []).append(t.receiver_role)

    party_labels: dict[str, dict[str, Any]] = {}
    for p_id, r_list in roles_by_party.items():
        best_role = role_precedence(r_list)
        if best_role is not None:
            m_role = MuleRole(best_role)
            party_labels[p_id] = {
                "mule_role": m_role.value,
                "is_mule": m_role in {MuleRole.FIRST_HOP, MuleRole.LAYERING, MuleRole.CASH_OUT},
            }

    return party_labels


@dataclass(frozen=True)
class SplitSpec:
    """Specification for temporal dataset splitting with purge embargo windows."""

    train_end_day: float
    val_end_day: float
    purge_hours: float = 24.0


def temporal_split(
    events: Iterable[GeneratedEvent],
    spec: SplitSpec,
    start_time: datetime | None = None,
) -> dict[str, Any]:
    """Partition generated events into train, validation, and test splits.

    Drops events that land within purge gaps after train and validation endpoints.
    Identifies and reports scenarios that straddle split boundaries.
    """
    event_list = list(events)
    if not event_list:
        return {"train": [], "val": [], "test": [], "purged": [], "straddling_scenarios": []}

    t0 = start_time or event_list[0].truth.event_time
    train_end = t0 + timedelta(days=spec.train_end_day)
    train_purge_end = train_end + timedelta(hours=spec.purge_hours)

    val_end = t0 + timedelta(days=spec.val_end_day)
    val_purge_end = val_end + timedelta(hours=spec.purge_hours)

    train_split: list[GeneratedEvent] = []
    val_split: list[GeneratedEvent] = []
    test_split: list[GeneratedEvent] = []
    purged: list[GeneratedEvent] = []

    scenario_splits: dict[str, set[str]] = {}

    for ge in event_list:
        rec_time = ge.event.received_at
        sc_id = ge.truth.scenario_id

        if rec_time < train_end:
            assigned = "train"
            train_split.append(ge)
        elif rec_time < train_purge_end:
            assigned = "purge_train"
            purged.append(ge)
        elif rec_time < val_end:
            assigned = "val"
            val_split.append(ge)
        elif rec_time < val_purge_end:
            assigned = "purge_val"
            purged.append(ge)
        else:
            assigned = "test"
            test_split.append(ge)

        if sc_id:
            scenario_splits.setdefault(sc_id, set()).add(assigned)

    # Scenarios spanning more than one split (excluding purge)
    straddling = [
        sc_id
        for sc_id, s_sets in scenario_splits.items()
        if len(s_sets - {"purge_train", "purge_val"}) > 1
    ]

    return {
        "train": train_split,
        "val": val_split,
        "test": test_split,
        "purged": purged,
        "straddling_scenarios": straddling,
    }
