"""API endpoints for developer synthetic transaction generator tooling.

Provides scenario catalog exploration, in-memory stream preview, and dataset listing.
Protected by ENABLE_GENERATOR_API (returns HTTP 404 when disabled).
"""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ripple.config import get_settings
from ripple.generator.config import ALGORITHM_VERSION, GeneratorConfig
from ripple.generator.dataset import DATASET_NAME_REGEX
from ripple.generator.engine import generate_stream
from ripple.generator.scenarios import list_scenarios

router = APIRouter(tags=["generator"])


class PreviewRequest(BaseModel):
    """Configuration parameters for in-memory synthetic stream preview."""

    preset: str = Field(default="tiny", description="Scale preset name")
    seed: int = Field(default=42, description="Random seed")
    actors: int | None = Field(default=None, description="Actor count override")
    days: int | None = Field(default=None, description="Day count override")
    difficulty: str = Field(default="medium", description="Difficulty level")
    networks: list[str] | None = Field(default=None, description="Payment networks")


def _check_generator_enabled() -> None:
    settings = get_settings()
    if not settings.ENABLE_GENERATOR_API:
        raise HTTPException(
            status_code=404,
            detail="Synthetic Generator API is disabled.",
        )


@router.get("/generator/scenarios")
def get_scenarios() -> list[dict[str, Any]]:
    """Retrieve catalog of all 10 registered fraud and laundering scenario typologies."""
    _check_generator_enabled()
    return list_scenarios()


@router.post("/generator/preview")
def preview_stream(req: PreviewRequest) -> dict[str, Any]:
    """Execute a dry-run in-memory stream generation and return summary and sample events."""
    _check_generator_enabled()
    settings = get_settings()

    # Enforce guardrail caps
    if req.actors is not None and req.actors > settings.GENERATOR_PREVIEW_MAX_ACTORS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Actor count {req.actors} exceeds preview cap of "
                f"{settings.GENERATOR_PREVIEW_MAX_ACTORS}."
            ),
        )
    if req.days is not None and req.days > settings.GENERATOR_PREVIEW_MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Day count {req.days} exceeds preview cap of "
                f"{settings.GENERATOR_PREVIEW_MAX_DAYS}."
            ),
        )

    overrides: dict[str, Any] = {}
    if req.actors is not None:
        overrides["actors"] = req.actors
    if req.days is not None:
        overrides["days"] = req.days
    if req.difficulty:
        overrides["difficulty"] = req.difficulty
    if req.networks:
        overrides["networks"] = req.networks

    cfg = GeneratorConfig.from_preset(req.preset, seed=req.seed, **overrides)
    # Check effective config values
    if cfg.actors > settings.GENERATOR_PREVIEW_MAX_ACTORS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Effective actors {cfg.actors} exceeds cap "
                f"{settings.GENERATOR_PREVIEW_MAX_ACTORS}."
            ),
        )
    if cfg.days > settings.GENERATOR_PREVIEW_MAX_DAYS:
        raise HTTPException(
            status_code=422,
            detail=f"Effective days {cfg.days} exceeds cap {settings.GENERATOR_PREVIEW_MAX_DAYS}.",
        )

    sample_size = settings.GENERATOR_PREVIEW_SAMPLE_SIZE
    max_events = settings.GENERATOR_PREVIEW_MAX_EVENTS

    counts_by_network: dict[str, int] = {}
    scenario_counts: dict[str, int] = {}
    sample_records: list[dict[str, Any]] = []
    total_events = 0
    fraud_events = 0

    for gen_event in generate_stream(cfg):
        total_events += 1
        net = gen_event.truth.network_id
        counts_by_network[net] = counts_by_network.get(net, 0) + 1

        if gen_event.truth.is_fraud:
            fraud_events += 1
            sc = gen_event.truth.pattern or gen_event.truth.scenario_id or "unspecified"
            scenario_counts[sc] = scenario_counts.get(sc, 0) + 1

        if len(sample_records) < sample_size:
            sample_records.append(
                {
                    "seq": gen_event.seq,
                    "event": gen_event.event.model_dump(mode="json"),
                    "truth": gen_event.truth.model_dump(mode="json"),
                }
            )

        if total_events >= max_events:
            break

    fraud_frac = (fraud_events / total_events) if total_events > 0 else 0.0

    return {
        "config_hash": cfg.config_hash(),
        "algorithm_version": ALGORITHM_VERSION,
        "total_events": total_events,
        "counts_by_network": counts_by_network,
        "fraud_fraction": round(fraud_frac, 4),
        "scenario_counts": scenario_counts,
        "sample": sample_records,
    }


@router.get("/datasets")
def list_datasets() -> list[dict[str, Any]]:
    """List available datasets by inspecting manifests only without reading raw event streams."""
    _check_generator_enabled()
    settings = get_settings()

    datasets_dir = Path(settings.GENERATOR_DATA_DIR) / "datasets"
    if not datasets_dir.exists():
        return []

    manifests: list[dict[str, Any]] = []
    for entry in sorted(datasets_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if not DATASET_NAME_REGEX.match(entry.name):
            continue

        manifest_file = entry / "manifest.json"
        if manifest_file.exists():
            try:
                with open(manifest_file, encoding="utf-8") as f:
                    manifest_data = json.load(f)
                    manifests.append(manifest_data)
            except Exception:
                continue

    return manifests
