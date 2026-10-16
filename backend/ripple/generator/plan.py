"""Transaction execution plans, lifecycle stages, and pure renderer protocols.

Rule R5 Invariant:
- All random choices and outcome decisions are made when a TxnPlan is built.
- Renderers are pure functions (plan, stage_index) -> payload dict with no randomness
  and zero clock reads.
- Plans are created eagerly up front.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class StagePlan(BaseModel):
    """Specification of a single lifecycle event within a transaction plan."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(..., description="Stage name (e.g. 'authorization', 'clearing', 'settled')")
    event_time: datetime = Field(..., description="Simulated event timestamp in UTC")
    outcome_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Eagerly determined outcome parameters (e.g. response_code, fee, error)",
    )


class TxnPlan(BaseModel):
    """Eager transaction execution plan defining an entire multi-stage lifecycle."""

    model_config = ConfigDict(frozen=True)

    plan_id: str = Field(..., description="Unique plan identifier")
    source_key: str = Field(..., description="Planner source key (e.g. 'bg_card', 'sc_upi_mule')")
    network: str = Field(..., description="Payment rail (e.g. 'card', 'upi', 'crypto')")
    kind: str = Field(..., description="Transaction kind (e.g. 'pos_purchase', 'p2p_pay')")
    sender_actor_id: str = Field(..., description="Actor ID of originator")
    receiver_actor_id: str = Field(..., description="Actor ID of beneficiary")
    amount: Decimal = Field(..., description="Monetary transfer principal")
    currency: str = Field(..., description="ISO-4217 alpha-3 currency code")
    stages: list[StagePlan] = Field(..., description="Ordered list of lifecycle stage plans")
    attrs: dict[str, Any] = Field(default_factory=dict, description="Metadata and parameters")
    truth_seed: dict[str, Any] = Field(
        default_factory=dict, description="Ground-truth sidecar parameters"
    )


class StageRenderer(Protocol):
    """Pure functional protocol converting a plan stage into a native adapter payload dict."""

    def __call__(self, plan: TxnPlan, stage_index: int) -> dict[str, Any]:
        """Produce native payload dictionary without randomness or system clock reads."""
        ...


_RENDERER_REGISTRY: dict[str, StageRenderer] = {}


def register_renderer(network: str, renderer: StageRenderer) -> None:
    """Register a pure stage renderer for a payment network."""
    _RENDERER_REGISTRY[network.lower()] = renderer


def get_renderer(network: str) -> StageRenderer:
    """Retrieve registered stage renderer for a payment network."""
    clean_net = network.lower()
    if clean_net not in _RENDERER_REGISTRY:
        raise KeyError(f"No renderer registered for payment network '{network}'")
    return _RENDERER_REGISTRY[clean_net]
