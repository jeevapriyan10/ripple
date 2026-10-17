"""Reporting models for adapter conformance suite execution.

Stores granular check outcomes and formats machine-readable and markdown summaries.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from ripple.adapters.testing.cases import ConformanceOptions


class CheckStatus(str, Enum):
    """Execution status for a conformance check."""

    PASS = "PASS"
    FAIL = "FAIL"
    SKIP = "SKIP"


class CheckResult(BaseModel):
    """Outcome of a single check evaluation."""

    check_id: str
    title: str
    status: CheckStatus
    detail: str = ""
    case_name: str | None = None


class ConformanceReport(BaseModel):
    """Aggregate conformance suite evaluation report for an adapter."""

    network_id: str
    options: ConformanceOptions = Field(default_factory=ConformanceOptions)
    results: list[CheckResult] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return True if no check failed."""
        return all(r.status != CheckStatus.FAIL for r in self.results)

    @property
    def failures(self) -> list[CheckResult]:
        """Return all failed check results."""
        return [r for r in self.results if r.status == CheckStatus.FAIL]

    def assert_conformant(self) -> None:
        """Raise AssertionError listing every failure if any check failed."""
        if not self.passed:
            failure_lines = [
                f"Conformance suite failed for adapter '{self.network_id}' "
                f"with {len(self.failures)} failure(s):"
            ]
            for f in self.failures:
                case_str = f" [case: {f.case_name}]" if f.case_name else ""
                failure_lines.append(f"  • {f.check_id} ({f.title}){case_str}: {f.detail}")
            raise AssertionError("\n".join(failure_lines))

    def to_dict(self) -> dict[str, Any]:
        """Convert report to a JSON-serializable dictionary."""
        return self.model_dump(mode="json")

    def to_markdown(self) -> str:
        """Generate a clean markdown report of check results."""
        lines = [
            f"# Conformance Report: `{self.network_id}`",
            "",
            f"**Overall Status:** {'✅ PASSED' if self.passed else '❌ FAILED'} | "
            f"**Total Checks:** {len(self.results)} | "
            f"**Failures:** {len(self.failures)}",
            "",
        ]

        if self.options.network_stamp_exempt:
            lines.append(
                f"> **Exemption (Network Stamping):** {self.options.network_stamp_exempt_reason}"
            )
        if self.options.profile_consistency_exempt:
            lines.append(
                "> **Exemption (Profile Consistency):** "
                f"{self.options.profile_consistency_exempt_reason}"
            )
        if self.options.network_stamp_exempt or self.options.profile_consistency_exempt:
            lines.append("")

        lines.extend(
            [
                "| Check ID | Title | Status | Case | Details |",
                "| :--- | :--- | :---: | :--- | :--- |",
            ]
        )

        for r in self.results:
            icon = (
                "✅ PASS"
                if r.status == CheckStatus.PASS
                else ("⚠️ SKIP" if r.status == CheckStatus.SKIP else "❌ FAIL")
            )
            case_text = f"`{r.case_name}`" if r.case_name else "-"
            detail_text = r.detail.replace("\n", " ").strip() if r.detail else "-"
            lines.append(f"| {r.check_id} | {r.title} | {icon} | {case_text} | {detail_text} |")

        return "\n".join(lines)
