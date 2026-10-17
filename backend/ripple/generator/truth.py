"""Ground-truth sidecar models, role hierarchy, and event envelopes.

Rule R3 Invariant:
- Ground truth (fraud flags, scenario IDs, topological roles, regimes) NEVER
  appears in any event payload dict or RawEvent. It is stored exclusively in sidecars.
"""

from collections.abc import Sequence
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ripple.adapters.contract import RawEvent

# Precedence hierarchy: terminal cash out is most specific, victim is least specific
_ROLE_PRIORITY: dict[str, int] = {
    "CASH_OUT": 5,
    "LAYERING": 4,
    "FIRST_HOP": 3,
    "VICTIM": 2,
    "UNKNOWN": 1,
}


def role_precedence(roles: Sequence[str | None]) -> str | None:
    """Resolve role assignment by precedence: CASH_OUT > LAYERING > FIRST_HOP > VICTIM."""
    highest_score = 0
    best_role: str | None = None
    for r in roles:
        if r is None:
            continue
        clean_r = str(r).strip().upper()
        score = _ROLE_PRIORITY.get(clean_r, 0)
        if score > highest_score:
            highest_score = score
            best_role = clean_r
    return best_role


class EventTruth(BaseModel):
    """Ground-truth metadata for a single simulated lifecycle event."""

    model_config = ConfigDict(frozen=True)

    event_id: str = Field(..., description="Corresponds to RawEvent.event_id")
    logical_txn_id: str = Field(..., description="Cross-stage logical transaction correlation key")
    stage: str = Field(..., description="Lifecycle stage name (e.g. 'authorization', 'completed')")
    network_id: str = Field(..., description="Payment rail (e.g. 'card', 'upi')")
    event_time: datetime = Field(
        ..., description="True simulated transaction event timestamp in UTC"
    )

    # Fraud & Crime typology
    scenario_id: str | None = Field(default=None, description="Scenario identifier if fraudulent")
    pattern: str | None = Field(default=None, description="Typology name if fraudulent")
    regime: str = Field(default="baseline", description="Adversarial regime or template name")
    is_fraud: bool = Field(default=False, description="True if confirmed fraudulent")

    # Hard-negative indicators
    hard_negative: bool = Field(default=False, description="True if legitimate look-alike pattern")
    lookalike_of: str | None = Field(
        default=None, description="Name of the fraud scenario this resembles"
    )

    # Actor identities & roles
    sender_actor_id: str = Field(..., description="Actor ID of the sender")
    receiver_actor_id: str = Field(..., description="Actor ID of the receiver")
    sender_role: str | None = Field(default=None, description="MuleRole enum name or None")
    receiver_role: str | None = Field(default=None, description="MuleRole enum name or None")
    sender_institution_id: str | None = Field(default=None)
    receiver_institution_id: str | None = Field(default=None)

    # Label availability and delay
    label_available_at: datetime | None = Field(
        default=None,
        description="Timestamp in UTC when fraud label becomes available to investigators",
    )

    # Ingestion quirks
    expected_error: bool = Field(
        default=False,
        description="True if intentionally malformed to trigger an adapter NormalizationError",
    )
    duplicate_of: str | None = Field(
        default=None,
        description="Original event_id if this is an injected transport duplicate",
    )


class GeneratedEvent(BaseModel):
    """Envelope coupling a public RawEvent with its private EventTruth sidecar."""

    model_config = ConfigDict(frozen=True)

    seq: int = Field(..., ge=0, description="Monotonically increasing event sequence number")
    event: RawEvent = Field(..., description="Native payload event delivered to adapters")
    truth: EventTruth = Field(..., description="Ground truth sidecar record")
