"""Conformance case and options data models.

Defines the structure for test cases, manifest files, and execution options
used across the adapter conformance suite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ripple.adapters.contract import PayloadFormat


class ConformanceOptions(BaseModel):
    """Execution options and profile exemption declarations for an adapter.

    Exemptions are permitted only when accompanied by a non-empty written rationale.
    """

    network_stamp_exempt: bool = False
    network_stamp_exempt_reason: str | None = None
    profile_consistency_exempt: bool = False
    profile_consistency_exempt_reason: str | None = None
    timing_ceiling_ms: float = 250.0
    mutation_cap_per_case: int = 1500
    thread_count: int = 8

    @model_validator(mode="after")
    def validate_exemptions(self) -> ConformanceOptions:
        """Ensure any claimed exemption provides a non-empty written reason."""
        if self.network_stamp_exempt:
            if not self.network_stamp_exempt_reason or not self.network_stamp_exempt_reason.strip():
                raise ValueError("network_stamp_exempt requires a non-empty written reason.")
        if self.profile_consistency_exempt:
            if (
                not self.profile_consistency_exempt_reason
                or not self.profile_consistency_exempt_reason.strip()
            ):
                raise ValueError("profile_consistency_exempt requires a non-empty written reason.")
        return self


class ConformanceCase(BaseModel):
    """A single conformance test case loaded from a manifest and fixture file."""

    name: str
    fixture: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_format: PayloadFormat = PayloadFormat.JSON
    received_at: datetime = Field(
        default_factory=lambda: datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    )
    expect: Literal["ok", "error"] = "ok"
    error_type: str | None = None
    error_field: str | None = None
    group: str | None = None
    must_appear: list[str] = Field(default_factory=list)
    must_not_appear: list[str] = Field(default_factory=list)
    notes: str | None = None


class ManifestSchema(BaseModel):
    """Schema for an adapter's conformance.json manifest file."""

    schema_version: int = Field(alias="schema", default=1)
    network_id: str
    options: ConformanceOptions = Field(default_factory=ConformanceOptions)
    cases: list[ConformanceCase]


def load_manifest(manifest_path: str | Path) -> tuple[ConformanceOptions, list[ConformanceCase]]:
    """Load and validate a conformance manifest and its associated fixture files.

    Args:
        manifest_path: Absolute or relative path to conformance.json.

    Returns:
        A tuple of (ConformanceOptions, list of loaded ConformanceCase instances).
    """
    path = Path(manifest_path)
    if not path.is_file():
        raise FileNotFoundError(f"Conformance manifest not found at: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw_manifest = json.load(f)

    manifest = ManifestSchema.model_validate(raw_manifest)
    manifest_dir = path.parent

    resolved_cases: list[ConformanceCase] = []
    for case in manifest.cases:
        case_copy = case.model_copy()
        if case_copy.fixture:
            fixture_file = manifest_dir / case_copy.fixture
            if not fixture_file.is_file():
                # Also check relative to manifest_dir directly or fixtures directory
                alt_fixture = manifest_dir / "fixtures" / case_copy.fixture
                if alt_fixture.is_file():
                    fixture_file = alt_fixture
                else:
                    raise FileNotFoundError(
                        f"Fixture file '{case_copy.fixture}' declared in {path} was not found."
                    )
            with fixture_file.open("r", encoding="utf-8") as f_fix:
                case_copy.payload = json.load(f_fix)
        resolved_cases.append(case_copy)

    return manifest.options, resolved_cases
