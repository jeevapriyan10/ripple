"""Adapter conformance testing suite for Ripple payment network adapters.

A reusable, data-driven test library verifying pure normalization, determinism,
runtime security, profile compliance, and error-handling invariants.
"""

from __future__ import annotations

from ripple.adapters.testing.cases import (
    ConformanceCase,
    ConformanceOptions,
    load_manifest,
)
from ripple.adapters.testing.report import (
    CheckResult,
    CheckStatus,
    ConformanceReport,
)
from ripple.adapters.testing.runner import run_conformance

__all__ = [
    "CheckResult",
    "CheckStatus",
    "ConformanceCase",
    "ConformanceOptions",
    "ConformanceReport",
    "load_manifest",
    "run_conformance",
]
