"""Domain event definitions for fraud detection and risk alerts in Ripple.

This module provides the core contract between analytical detection pipelines
(Mesh, Chain, Score) and alert notification / case management layers. It is
deliberately minimal: later modules (e.g. Trace in M13) extend risk explanations
with structured graph provenance trees.
"""

from datetime import UTC, datetime
from enum import Enum
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ripple.domain.errors import RippleDomainError


class FlagSubjectType(str, Enum):
    """The category of domain subject targeted by a risk flag."""

    TRANSACTION = "TRANSACTION"
    PARTY = "PARTY"


class FlagSeverity(str, Enum):
    """Graded urgency level indicating the operational impact of a flag.

    LOW: Suspicious velocity or new counterparty; worth passive monitoring.
    MEDIUM: Elevated risk score; may trigger step-up authentication or soft hold.
    HIGH: Clear fraud typology match (e.g. rapid fan-out mule); triggers automated intervention.
    CRITICAL: Confirmed malicious activity (sanction hit, syndicate account); immediate freeze.
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FlagEvent(BaseModel):
    """Minimal immutable domain event emitted when a transaction or party is flagged for risk.

    Acts as the handoff payload between detection modules (Mesh / Chain / Score)
    and ingestion/alerting systems.
    """

    model_config = ConfigDict(frozen=True)

    flag_id: UUID = Field(
        default_factory=uuid4,
        description="Unique identifier for this specific alert event instance.",
    )
    subject: UUID = Field(
        ...,
        description="UUID of the flagged Transaction or Party.",
    )
    subject_type: FlagSubjectType = Field(
        ...,
        description="Subject classification (TRANSACTION or PARTY).",
    )
    risk_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Calibrated probability score between 0.0 (benign) and 1.0 (certain fraud).",
    )
    severity: FlagSeverity = Field(
        ...,
        description="Graded severity level (LOW, MEDIUM, HIGH, CRITICAL).",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Timestamp when the flag was generated, in UTC.",
    )
    reasons: list[str] = Field(
        default_factory=list,
        description="Rule or heuristic trigger explanation strings (extended in M13).",
    )
    model_version: str = Field(
        ...,
        description="Version identifier of the scoring or rule pipeline producing this flag.",
    )

    @model_validator(mode="after")
    def _validate_timestamps(self) -> Self:
        if (
            self.created_at.tzinfo is None
            or self.created_at.tzinfo.utcoffset(self.created_at) is None
        ):
            raise RippleDomainError("FlagEvent created_at must be timezone-aware (UTC).")
        return self
