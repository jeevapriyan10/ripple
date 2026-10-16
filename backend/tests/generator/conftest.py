"""Pytest fixtures for generator tests."""

from typing import Any

import pytest

from ripple.generator.plan import TxnPlan, register_renderer


@pytest.fixture(autouse=True)
def setup_dummy_renderer() -> None:
    """Register dummy renderers for networks in generator testing."""

    def dummy_renderer(plan: TxnPlan, stage_idx: int) -> dict[str, Any]:
        return {
            "txn_id": plan.plan_id,
            "stage": plan.stages[stage_idx].name,
            "amount": str(plan.amount),
        }

    for net in ["upi", "card", "crypto", "swift", "wallet"]:
        register_renderer(net, dummy_renderer)
