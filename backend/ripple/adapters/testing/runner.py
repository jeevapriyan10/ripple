"""Orchestration runner for the Ripple adapter conformance test suite.

Executes checks CF-01 through CF-21 against a target adapter and compiles
the final ConformanceReport.
"""

from __future__ import annotations

from ripple.adapters.contract import NetworkAdapter
from ripple.adapters.testing.cases import ConformanceCase, ConformanceOptions
from ripple.adapters.testing.checks import (
    check_cf01_contract_surface,
    check_cf02_registry_integration,
    check_cf03_can_handle,
    check_cf04_determinism,
    check_cf05_static_purity,
    check_cf06_dynamic_purity,
    check_cf07_time_discipline,
    check_cf08_network_stamping,
    check_cf09_lifecycle_identity_stability,
    check_cf10_integrity,
    check_cf11_status_settlement_coherence,
    check_cf12_money_discipline,
    check_cf13_serialization_roundtrip,
    check_cf14_input_immutability,
    check_cf15_statelessness,
    check_cf16_error_contract,
    check_cf17_batch_isolation,
    check_cf18_leak_scan,
    check_cf19_mutation_robustness,
    check_cf20_performance_smoke,
    check_cf21_cross_network_rejection,
)
from ripple.adapters.testing.report import CheckResult, ConformanceReport


def run_conformance(
    adapter: NetworkAdapter,
    cases: list[ConformanceCase],
    options: ConformanceOptions | None = None,
    foreign_cases: list[ConformanceCase] | None = None,
) -> ConformanceReport:
    """Run all 21 conformance checks against an adapter and return a ConformanceReport.

    Args:
        adapter: The NetworkAdapter instance to test.
        cases: The list of ConformanceCase objects loaded from the manifest.
        options: Optional execution and profile exemption options.
        foreign_cases: Optional ok cases from other adapters to test cross-network rejection.

    Returns:
        A ConformanceReport containing granular results for each check.
    """
    opts = options or ConformanceOptions()
    adapter_cls = type(adapter)
    ok_cases = [c for c in cases if c.expect == "ok"]
    error_cases = [c for c in cases if c.expect == "error"]

    results: list[CheckResult] = []

    # CF-01 Contract surface
    results.append(check_cf01_contract_surface(adapter))

    # CF-02 Registry integration
    results.append(check_cf02_registry_integration(adapter, adapter_cls))

    # CF-03 can_handle
    results.append(check_cf03_can_handle(adapter, ok_cases))

    # CF-04 Determinism
    results.extend(check_cf04_determinism(adapter, adapter_cls, ok_cases))

    # CF-05 Static purity scan
    results.append(check_cf05_static_purity(adapter_cls))

    # CF-06 Dynamic purity guards
    results.extend(check_cf06_dynamic_purity(adapter, ok_cases))

    # CF-07 Time discipline
    results.extend(check_cf07_time_discipline(adapter, ok_cases))

    # CF-08 Network stamping and profile consistency
    results.extend(check_cf08_network_stamping(adapter, opts, ok_cases))

    # CF-09 Lifecycle identity stability
    results.append(check_cf09_lifecycle_identity_stability(adapter, ok_cases))

    # CF-10 Integrity
    results.extend(check_cf10_integrity(adapter, ok_cases))

    # CF-11 Status and settlement coherence
    results.extend(check_cf11_status_settlement_coherence(adapter, ok_cases))

    # CF-12 Money discipline
    results.extend(check_cf12_money_discipline(adapter, ok_cases))

    # CF-13 Serialization round-trip
    results.extend(check_cf13_serialization_roundtrip(adapter, ok_cases))

    # CF-14 Input immutability
    results.extend(check_cf14_input_immutability(adapter, ok_cases))

    # CF-15 Statelessness
    results.append(check_cf15_statelessness(adapter, opts, ok_cases))

    # CF-16 Error contract
    results.extend(check_cf16_error_contract(adapter, error_cases))

    # CF-17 Batch isolation
    results.append(check_cf17_batch_isolation(adapter, ok_cases, error_cases))

    # CF-18 Leak scan
    results.extend(check_cf18_leak_scan(adapter, cases))

    # CF-19 Mutation robustness
    results.extend(check_cf19_mutation_robustness(adapter, opts, ok_cases))

    # CF-20 Performance smoke
    results.append(check_cf20_performance_smoke(adapter, opts, ok_cases))

    # CF-21 Cross-network rejection
    results.extend(check_cf21_cross_network_rejection(adapter, foreign_cases))

    return ConformanceReport(
        network_id=getattr(adapter, "network_id", adapter.profile.network_id),
        options=opts,
        results=results,
    )
