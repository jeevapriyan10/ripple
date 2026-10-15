"""Conformance check implementations for the Ripple adapter layer.

Defines checks CF-01 through CF-21 to evaluate adapter compliance against
the purity, determinism, data hygiene, error contracts, and lifecycle invariants.
"""

from __future__ import annotations

import ast
import copy
import inspect
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ripple.adapters.contract import (
    NetworkAdapter,
    NormalizationResult,
    PayloadFormat,
    RawEvent,
)
from ripple.adapters.currencies import ISO_4217_CURRENCIES
from ripple.adapters.errors import AdapterError, NormalizationError
from ripple.adapters.profile import NetworkProfile
from ripple.adapters.registry import AdapterRegistry
from ripple.adapters.testing.cases import ConformanceCase, ConformanceOptions
from ripple.adapters.testing.fuzz import generate_mutants
from ripple.adapters.testing.guards import PurityViolationError, dynamic_purity_guard
from ripple.adapters.testing.report import CheckResult, CheckStatus
from ripple.domain.money import KNOWN_CURRENCIES, AssetClass
from ripple.domain.transaction import TransactionStatus


def _get_network_id(adapter: NetworkAdapter) -> str:
    """Extract network_id from adapter attribute or profile."""
    return getattr(adapter, "network_id", adapter.profile.network_id)


def _make_raw_event(case: ConformanceCase, network_id: str) -> RawEvent:
    """Helper creating a RawEvent from a test case and network id."""
    return RawEvent(
        network_id=network_id,
        payload=copy.deepcopy(case.payload),
        payload_format=case.payload_format,
        received_at=case.received_at,
    )


def _check_no_floats(obj: Any, path: str = "") -> list[str]:
    """Recursively search for float values in an arbitrary data structure."""
    if isinstance(obj, float):
        return [f"{path}: float value {obj}"]
    if isinstance(obj, str | int | bool | bytes | Decimal | datetime | type(None)):
        return []
    violations: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            violations.extend(_check_no_floats(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list | tuple | set):
        for idx, item in enumerate(obj):
            violations.extend(_check_no_floats(item, f"{path}[{idx}]"))
    return violations


def _check_utc_datetimes(obj: Any, path: str = "") -> list[str]:
    """Recursively search for naive datetimes or non-UTC offsets."""
    if isinstance(obj, datetime):
        if obj.tzinfo is None:
            return [f"{path}: naive datetime without tzinfo ({obj})"]
        offset = obj.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            return [f"{path}: datetime has non-UTC offset ({obj})"]
        return []
    if isinstance(obj, str | int | float | bool | bytes | Decimal | type(None)):
        return []
    violations: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            violations.extend(_check_utc_datetimes(v, f"{path}.{k}" if path else str(k)))
    elif isinstance(obj, list | tuple | set):
        for idx, item in enumerate(obj):
            violations.extend(_check_utc_datetimes(item, f"{path}[{idx}]"))
    return violations


# --- CF-01: Contract surface ---
def check_cf01_contract_surface(adapter: NetworkAdapter) -> CheckResult:
    """Verify adapter inherits from NetworkAdapter, has valid profile and describe."""
    try:
        if not isinstance(adapter, NetworkAdapter):
            return CheckResult(
                check_id="CF-01",
                title="Contract surface",
                status=CheckStatus.FAIL,
                detail=f"Adapter {type(adapter).__name__} does not inherit from NetworkAdapter.",
            )
        if not isinstance(adapter.profile, NetworkProfile):
            return CheckResult(
                check_id="CF-01",
                title="Contract surface",
                status=CheckStatus.FAIL,
                detail="adapter.profile is not a NetworkProfile instance.",
            )
        if not adapter.adapter_version or not adapter.adapter_version.strip():
            return CheckResult(
                check_id="CF-01",
                title="Contract surface",
                status=CheckStatus.FAIL,
                detail="adapter.adapter_version is empty.",
            )
        desc_text = getattr(adapter, "description", adapter.profile.description)
        if not desc_text or not desc_text.strip():
            return CheckResult(
                check_id="CF-01",
                title="Contract surface",
                status=CheckStatus.FAIL,
                detail="adapter description is empty.",
            )
        net_id = _get_network_id(adapter)
        if not net_id or not net_id.strip():
            return CheckResult(
                check_id="CF-01",
                title="Contract surface",
                status=CheckStatus.FAIL,
                detail="adapter network_id is empty.",
            )

        desc = adapter.describe()
        if not isinstance(desc, dict):
            return CheckResult(
                check_id="CF-01",
                title="Contract surface",
                status=CheckStatus.FAIL,
                detail=f"describe() must return a dict, got {type(desc).__name__}.",
            )
        json.dumps(desc)

        return CheckResult(check_id="CF-01", title="Contract surface", status=CheckStatus.PASS)
    except Exception as exc:
        return CheckResult(
            check_id="CF-01", title="Contract surface", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-02: Registry integration ---
def check_cf02_registry_integration(
    adapter: NetworkAdapter, adapter_cls: type[NetworkAdapter]
) -> CheckResult:
    """Verify registration, get, route, and from_settings in a fresh registry."""
    try:
        from ripple.config import Settings

        reg = AdapterRegistry()
        reg.register(adapter_cls)
        network_id = _get_network_id(adapter)

        retrieved = reg.get(network_id)
        if not isinstance(retrieved, adapter_cls):
            return CheckResult(
                check_id="CF-02",
                title="Registry integration",
                status=CheckStatus.FAIL,
                detail=f"Registry.get({network_id}) did not yield {adapter_cls.__name__}.",
            )

        test_event = RawEvent(
            network_id=network_id,
            payload={},
            payload_format=PayloadFormat.JSON,
            received_at=datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC),
        )
        routed = reg.route(test_event)
        if not isinstance(routed, adapter_cls):
            return CheckResult(
                check_id="CF-02",
                title="Registry integration",
                status=CheckStatus.FAIL,
                detail=f"Registry.route() did not return instance of {adapter_cls.__name__}.",
            )

        settings_instance = adapter_cls.from_settings(Settings())
        if not isinstance(settings_instance, adapter_cls):
            return CheckResult(
                check_id="CF-02",
                title="Registry integration",
                status=CheckStatus.FAIL,
                detail=f"from_settings() did not return instance of {adapter_cls.__name__}.",
            )

        return CheckResult(check_id="CF-02", title="Registry integration", status=CheckStatus.PASS)
    except Exception as exc:
        return CheckResult(
            check_id="CF-02", title="Registry integration", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-03: can_handle ---
def check_cf03_can_handle(adapter: NetworkAdapter, ok_cases: list[ConformanceCase]) -> CheckResult:
    """Verify can_handle returns True for ok cases and handles garbage events without raising."""
    try:
        network_id = _get_network_id(adapter)
        for case in ok_cases:
            ev = _make_raw_event(case, network_id)
            if not adapter.can_handle(ev):
                return CheckResult(
                    check_id="CF-03",
                    title="can_handle",
                    status=CheckStatus.FAIL,
                    detail=f"can_handle returned False for valid case '{case.name}'.",
                    case_name=case.name,
                )

        fixed_dt = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
        garbage_events = [
            RawEvent(
                network_id="alien_network_id_xyz",
                payload={},
                payload_format=PayloadFormat.JSON,
                received_at=fixed_dt,
            ),
            RawEvent(
                network_id=network_id,
                payload={},
                payload_format=PayloadFormat.JSON,
                received_at=fixed_dt,
            ),
            RawEvent(
                network_id=network_id,
                payload={"unknown_key": "junk"},
                payload_format=PayloadFormat.JSON,
                received_at=fixed_dt,
            ),
            RawEvent.model_construct(
                network_id=network_id,
                payload=[1, 2, 3],
                payload_format=PayloadFormat.JSON,
                received_at=fixed_dt,
            ),
            RawEvent(
                network_id=network_id,
                payload="not a dict",
                payload_format=PayloadFormat.JSON,
                received_at=fixed_dt,
            ),
        ]
        for g_ev in garbage_events:
            ans = adapter.can_handle(g_ev)
            if not isinstance(ans, bool):
                return CheckResult(
                    check_id="CF-03",
                    title="can_handle",
                    status=CheckStatus.FAIL,
                    detail=f"can_handle returned non-bool {type(ans).__name__} on garbage input.",
                )

        return CheckResult(check_id="CF-03", title="can_handle", status=CheckStatus.PASS)
    except Exception as exc:
        return CheckResult(
            check_id="CF-03", title="can_handle", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-04: Determinism ---
def check_cf04_determinism(
    adapter: NetworkAdapter,
    adapter_cls: type[NetworkAdapter],
    ok_cases: list[ConformanceCase],
) -> list[CheckResult]:
    """Verify normalizing each ok case twice and on a fresh instance yields equal output."""
    results: list[CheckResult] = []
    try:
        network_id = _get_network_id(adapter)
        fresh_adapter: NetworkAdapter
        if hasattr(adapter, "pan_token_key"):
            fresh_adapter = adapter_cls(pan_token_key=adapter.pan_token_key)  # type: ignore[call-arg]
        else:
            try:
                fresh_adapter = adapter_cls.from_settings()
            except Exception:
                try:
                    fresh_adapter = adapter_cls()
                except Exception:
                    fresh_adapter = adapter
        for case in ok_cases:
            ev = _make_raw_event(case, network_id)
            res1 = adapter.normalize(ev)
            res2 = adapter.normalize(ev)
            res_fresh = fresh_adapter.normalize(ev)

            d1 = res1.model_dump()
            d2 = res2.model_dump()
            dfresh = res_fresh.model_dump()

            if d1 != d2:
                results.append(
                    CheckResult(
                        check_id="CF-04",
                        title="Determinism",
                        status=CheckStatus.FAIL,
                        detail="Repeated normalization on same adapter produced different outputs.",
                        case_name=case.name,
                    )
                )
            elif d1 != dfresh:
                results.append(
                    CheckResult(
                        check_id="CF-04",
                        title="Determinism",
                        status=CheckStatus.FAIL,
                        detail="Normalization on fresh adapter produced different outputs.",
                        case_name=case.name,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        check_id="CF-04",
                        title="Determinism",
                        status=CheckStatus.PASS,
                        case_name=case.name,
                    )
                )
    except Exception as exc:
        results.append(
            CheckResult(
                check_id="CF-04",
                title="Determinism",
                status=CheckStatus.FAIL,
                detail=str(exc),
            )
        )
    return results


# --- CF-05: Static purity scan ---
_DISALLOWED_CALL_NAMES = {
    "uuid1",
    "uuid4",
    "utcnow",
    "monotonic",
    "perf_counter",
    "time_ns",
}

_DISALLOWED_MODULES = {
    "requests",
    "httpx",
    "aiohttp",
    "socket",
    "subprocess",
    "neo4j",
    "kafka",
    "confluent_kafka",
    "sqlalchemy",
}


class _PurityASTVisitor(ast.NodeVisitor):
    def __init__(self, filename: str) -> None:
        self.filename = filename
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            base = alias.name.split(".")[0]
            if base in _DISALLOWED_MODULES:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Disallowed import of '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            base = node.module.split(".")[0]
            if base in _DISALLOWED_MODULES:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Disallowed import from '{node.module}'"
                )
        for alias in node.names:
            if alias.name in _DISALLOWED_CALL_NAMES:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Disallowed direct import of '{alias.name}'"
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id == "open":
                self.violations.append(f"{self.filename}:{node.lineno}: Call to builtin open()")
            elif func.id in _DISALLOWED_CALL_NAMES:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to disallowed function '{func.id}()'"
                )
        elif isinstance(func, ast.Attribute):
            attr = func.attr
            if attr in _DISALLOWED_CALL_NAMES or attr in {"today"}:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to disallowed attribute '{attr}()'"
                )
            if (
                attr == "now"
                and isinstance(func.value, ast.Name)
                and func.value.id in {"datetime", "date"}
            ):
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to datetime.now() reads the clock"
                )
            if attr == "time" and isinstance(func.value, ast.Name) and func.value.id == "time":
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to time.time() reads the clock"
                )
            if attr in {"getenv"} and isinstance(func.value, ast.Name) and func.value.id == "os":
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to os.getenv() accesses the environment"
                )
            if isinstance(func.value, ast.Attribute) and func.value.attr == "environ":
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Access to os.environ reads the environment"
                )
            if isinstance(func.value, ast.Name) and func.value.id in {"random", "secrets"}:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to {func.value.id}.{attr}() disallowed"
                )
            if isinstance(func.value, ast.Name) and func.value.id in {
                "subprocess",
                "socket",
                "requests",
                "httpx",
            }:
                self.violations.append(
                    f"{self.filename}:{node.lineno}: Call to {func.value.id}.{attr}() performs I/O"
                )
        self.generic_visit(node)


def check_cf05_static_purity(adapter_cls: type[NetworkAdapter]) -> CheckResult:
    """Run an AST static purity scan over adapter package source and shared helpers."""
    try:
        src_file = inspect.getfile(adapter_cls)
        src_path = Path(src_file)

        files_to_scan: list[Path] = []
        if src_path.name == "__init__.py" or src_path.parent.name in {
            "card",
            "crypto",
            "swift",
            "upi",
            "wallet",
        }:
            files_to_scan.extend(src_path.parent.glob("*.py"))
        else:
            files_to_scan.append(src_path)

        # Include shared helpers if built-in
        adapters_dir = Path(__file__).resolve().parent.parent
        for shared_name in ("helpers.py", "contract.py", "currencies.py"):
            sh_file = adapters_dir / shared_name
            if sh_file.is_file() and sh_file not in files_to_scan:
                files_to_scan.append(sh_file)

        all_violations: list[str] = []
        for file in files_to_scan:
            try:
                tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
                visitor = _PurityASTVisitor(file.name)
                if file == src_path and src_path.name.startswith("test_"):
                    class_node = next(
                        (
                            n
                            for n in ast.walk(tree)
                            if isinstance(n, ast.ClassDef) and n.name == adapter_cls.__name__
                        ),
                        tree,
                    )
                    visitor.visit(class_node)
                else:
                    visitor.visit(tree)
                all_violations.extend(visitor.violations)
            except Exception as e:
                all_violations.append(f"Failed to parse {file.name}: {e}")

        if all_violations:
            return CheckResult(
                check_id="CF-05",
                title="Static purity scan",
                status=CheckStatus.FAIL,
                detail="; ".join(all_violations[:5]),
            )

        return CheckResult(check_id="CF-05", title="Static purity scan", status=CheckStatus.PASS)
    except Exception as exc:
        return CheckResult(
            check_id="CF-05", title="Static purity scan", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-06: Dynamic purity guards ---
def check_cf06_dynamic_purity(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Execute normalization inside dynamic purity guards to catch patched runtime violations."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            with dynamic_purity_guard():
                adapter.normalize(ev)
            results.append(
                CheckResult(
                    check_id="CF-06",
                    title="Dynamic purity guards",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
        except PurityViolationError as pve:
            results.append(
                CheckResult(
                    check_id="CF-06",
                    title="Dynamic purity guards",
                    status=CheckStatus.FAIL,
                    detail=str(pve),
                    case_name=case.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-06",
                    title="Dynamic purity guards",
                    status=CheckStatus.FAIL,
                    detail=f"Unexpected exception during purity guarded execution: {exc}",
                    case_name=case.name,
                )
            )
    return results


# --- CF-07: Time discipline ---
def check_cf07_time_discipline(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify observed_at equals received_at and all datetimes have UTC tzinfo with 0 offset."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            res = adapter.normalize(ev)
            if res.transaction.observed_at != ev.received_at:
                results.append(
                    CheckResult(
                        check_id="CF-07",
                        title="Time discipline",
                        status=CheckStatus.FAIL,
                        detail=(
                            f"observed_at ({res.transaction.observed_at}) does not match "
                            f"event.received_at ({ev.received_at})."
                        ),
                        case_name=case.name,
                    )
                )
                continue

            dt_violations = _check_utc_datetimes(res.model_dump())
            if dt_violations:
                results.append(
                    CheckResult(
                        check_id="CF-07",
                        title="Time discipline",
                        status=CheckStatus.FAIL,
                        detail="; ".join(dt_violations[:3]),
                        case_name=case.name,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        check_id="CF-07",
                        title="Time discipline",
                        status=CheckStatus.PASS,
                        case_name=case.name,
                    )
                )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-07",
                    title="Time discipline",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-08: Network stamping and profile consistency ---
def check_cf08_network_stamping(
    adapter: NetworkAdapter, options: ConformanceOptions, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify network stamping and profile consistency unless an explicit exemption is granted."""
    results: list[CheckResult] = []
    prof = adapter.profile
    network_id = _get_network_id(adapter)

    if options.network_stamp_exempt and options.profile_consistency_exempt:
        return [
            CheckResult(
                check_id="CF-08",
                title="Network stamping and profile consistency",
                status=CheckStatus.SKIP,
                detail=(
                    f"Exempt: {options.network_stamp_exempt_reason}; "
                    f"{options.profile_consistency_exempt_reason}"
                ),
            )
        ]

    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            res = adapter.normalize(ev)
            tx = res.transaction

            # Network stamping
            if not options.network_stamp_exempt:
                if tx.network.lower() != prof.network_id.lower():
                    results.append(
                        CheckResult(
                            check_id="CF-08",
                            title="Network stamping and profile consistency",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Transaction.network '{tx.network}' does not match "
                                f"profile network_id '{prof.network_id}'."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue
                for party in res.parties:
                    for ident in party.identifiers:
                        if ident.network.lower() != prof.network_id.lower():
                            results.append(
                                CheckResult(
                                    check_id="CF-08",
                                    title="Network stamping and profile consistency",
                                    status=CheckStatus.FAIL,
                                    detail=(
                                        f"PartyIdentifier network '{ident.network}' does not match "
                                        f"profile network_id '{prof.network_id}'."
                                    ),
                                    case_name=case.name,
                                )
                            )
                            break

            # Profile consistency
            if not options.profile_consistency_exempt:
                if tx.settlement_model != prof.settlement_model:
                    results.append(
                        CheckResult(
                            check_id="CF-08",
                            title="Network stamping and profile consistency",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"tx.settlement_model {tx.settlement_model} does not match "
                                f"profile {prof.settlement_model}."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue

                for party in res.parties:
                    for ident in party.identifiers:
                        if ident.type not in prof.supported_identifier_types:
                            results.append(
                                CheckResult(
                                    check_id="CF-08",
                                    title="Network stamping and profile consistency",
                                    status=CheckStatus.FAIL,
                                    detail=(
                                        f"PartyIdentifier type {ident.type} not in "
                                        "profile.supported_identifier_types."
                                    ),
                                    case_name=case.name,
                                )
                            )
                            break

                cur_upper = tx.amount.currency.upper()
                if cur_upper in KNOWN_CURRENCIES:
                    asset_class = KNOWN_CURRENCIES[cur_upper].asset_class
                elif cur_upper in ISO_4217_CURRENCIES:
                    asset_class = AssetClass.FIAT
                else:
                    asset_class = AssetClass.CRYPTO

                if asset_class not in prof.supported_asset_classes:
                    results.append(
                        CheckResult(
                            check_id="CF-08",
                            title="Network stamping and profile consistency",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"tx.amount currency '{tx.amount.currency}' asset class "
                                f"{asset_class} not supported by profile."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue

                if (
                    prof.supported_currencies
                    and tx.amount.currency not in prof.supported_currencies
                ):
                    results.append(
                        CheckResult(
                            check_id="CF-08",
                            title="Network stamping and profile consistency",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"tx.amount.currency {tx.amount.currency} not in "
                                "profile.supported_currencies."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue

                if not prof.intermediaries_visible and len(tx.intermediary_hops) > 0:
                    results.append(
                        CheckResult(
                            check_id="CF-08",
                            title="Network stamping and profile consistency",
                            status=CheckStatus.FAIL,
                            detail="Transaction has hops but intermediaries_visible is False.",
                            case_name=case.name,
                        )
                    )
                    continue

                if not prof.is_reversible and (
                    tx.is_reversible or tx.reversal_window_ends_at is not None
                ):
                    results.append(
                        CheckResult(
                            check_id="CF-08",
                            title="Network stamping and profile consistency",
                            status=CheckStatus.FAIL,
                            detail=(
                                "Transaction marked reversible when profile.is_reversible is False."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue

                if tx.reversal_window_ends_at is not None:
                    if not tx.is_reversible:
                        results.append(
                            CheckResult(
                                check_id="CF-08",
                                title="Network stamping and profile consistency",
                                status=CheckStatus.FAIL,
                                detail="reversal_window_ends_at set but is_reversible is False.",
                                case_name=case.name,
                            )
                        )
                        continue
                    if tx.reversal_window_ends_at < tx.initiated_at:
                        results.append(
                            CheckResult(
                                check_id="CF-08",
                                title="Network stamping and profile consistency",
                                status=CheckStatus.FAIL,
                                detail="reversal_window_ends_at is before initiated_at.",
                                case_name=case.name,
                            )
                        )
                        continue

            results.append(
                CheckResult(
                    check_id="CF-08",
                    title="Network stamping and profile consistency",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-08",
                    title="Network stamping and profile consistency",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-09: Lifecycle identity stability ---
def check_cf09_lifecycle_identity_stability(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> CheckResult:
    """Verify cases within the same lifecycle group share identical IDs; different groups differ."""
    try:
        network_id = _get_network_id(adapter)
        groups: dict[str, list[tuple[ConformanceCase, NormalizationResult]]] = {}
        for case in ok_cases:
            if case.group:
                ev = _make_raw_event(case, network_id)
                res = adapter.normalize(ev)
                groups.setdefault(case.group, []).append((case, res))

        if not groups:
            return CheckResult(
                check_id="CF-09",
                title="Lifecycle identity stability",
                status=CheckStatus.SKIP,
                detail="No lifecycle groups declared in manifest.",
            )

        # Invariant 1: within same group, IDs must match
        for grp_name, case_results in groups.items():
            if len(case_results) > 1:
                first_case, first_res = case_results[0]
                for other_case, other_res in case_results[1:]:
                    if first_res.correlation_key != other_res.correlation_key:
                        return CheckResult(
                            check_id="CF-09",
                            title="Lifecycle identity stability",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Group '{grp_name}': correlation_key mismatch between "
                                f"'{first_case.name}' and '{other_case.name}'."
                            ),
                            case_name=other_case.name,
                        )
                    if first_res.transaction.transaction_id != other_res.transaction.transaction_id:
                        return CheckResult(
                            check_id="CF-09",
                            title="Lifecycle identity stability",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Group '{grp_name}': transaction_id mismatch between "
                                f"'{first_case.name}' and '{other_case.name}'."
                            ),
                            case_name=other_case.name,
                        )
                    if first_res.transaction.sender_id != other_res.transaction.sender_id:
                        return CheckResult(
                            check_id="CF-09",
                            title="Lifecycle identity stability",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Group '{grp_name}': sender_id mismatch between "
                                f"'{first_case.name}' and '{other_case.name}'."
                            ),
                            case_name=other_case.name,
                        )
                    if first_res.transaction.receiver_id != other_res.transaction.receiver_id:
                        return CheckResult(
                            check_id="CF-09",
                            title="Lifecycle identity stability",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Group '{grp_name}': receiver_id mismatch between "
                                f"'{first_case.name}' and '{other_case.name}'."
                            ),
                            case_name=other_case.name,
                        )

        # Invariant 2: different groups must have different keys and transaction_ids
        grp_keys = list(groups.keys())
        for i in range(len(grp_keys)):
            for j in range(i + 1, len(grp_keys)):
                g1_name, g2_name = grp_keys[i], grp_keys[j]
                r1 = groups[g1_name][0][1]
                r2 = groups[g2_name][0][1]
                if r1.correlation_key == r2.correlation_key:
                    return CheckResult(
                        check_id="CF-09",
                        title="Lifecycle identity stability",
                        status=CheckStatus.FAIL,
                        detail=(
                            f"Distinct groups '{g1_name}' and '{g2_name}' "
                            f"share correlation_key '{r1.correlation_key}'."
                        ),
                    )
                if r1.transaction.transaction_id == r2.transaction.transaction_id:
                    return CheckResult(
                        check_id="CF-09",
                        title="Lifecycle identity stability",
                        status=CheckStatus.FAIL,
                        detail=(
                            f"Distinct groups '{g1_name}' and '{g2_name}' "
                            f"share transaction_id '{r1.transaction.transaction_id}'."
                        ),
                    )

        return CheckResult(
            check_id="CF-09", title="Lifecycle identity stability", status=CheckStatus.PASS
        )
    except Exception as exc:
        return CheckResult(
            check_id="CF-09",
            title="Lifecycle identity stability",
            status=CheckStatus.FAIL,
            detail=str(exc),
        )


# --- CF-10: Integrity ---
def check_cf10_integrity(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify parties include sender and receiver, party IDs are unique, and identifiers match."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            res = adapter.normalize(ev)
            party_ids = [p.party_id for p in res.parties]

            if len(party_ids) != len(set(party_ids)):
                results.append(
                    CheckResult(
                        check_id="CF-10",
                        title="Integrity",
                        status=CheckStatus.FAIL,
                        detail="Duplicate party_id detected across parties list.",
                        case_name=case.name,
                    )
                )
                continue

            parties_by_id = {p.party_id: p for p in res.parties}
            tx = res.transaction

            if tx.sender_id not in parties_by_id:
                results.append(
                    CheckResult(
                        check_id="CF-10",
                        title="Integrity",
                        status=CheckStatus.FAIL,
                        detail=f"sender_id '{tx.sender_id}' not found in result.parties.",
                        case_name=case.name,
                    )
                )
                continue

            if tx.receiver_id not in parties_by_id:
                results.append(
                    CheckResult(
                        check_id="CF-10",
                        title="Integrity",
                        status=CheckStatus.FAIL,
                        detail=f"receiver_id '{tx.receiver_id}' not found in result.parties.",
                        case_name=case.name,
                    )
                )
                continue

            empty_ident_parties = [p.party_id for p in res.parties if len(p.identifiers) == 0]
            if empty_ident_parties:
                results.append(
                    CheckResult(
                        check_id="CF-10",
                        title="Integrity",
                        status=CheckStatus.FAIL,
                        detail=f"Parties {empty_ident_parties} have no identifiers.",
                        case_name=case.name,
                    )
                )
                continue

            sender_party = parties_by_id[tx.sender_id]
            norm_sender_ident = tx.sender_identifier.normalize()
            sender_matches = any(
                i.normalize() == norm_sender_ident for i in sender_party.identifiers
            )
            if not sender_matches:
                results.append(
                    CheckResult(
                        check_id="CF-10",
                        title="Integrity",
                        status=CheckStatus.FAIL,
                        detail="tx.sender_identifier does not belong to sender party.",
                        case_name=case.name,
                    )
                )
                continue

            receiver_party = parties_by_id[tx.receiver_id]
            norm_receiver_ident = tx.receiver_identifier.normalize()
            receiver_matches = any(
                i.normalize() == norm_receiver_ident for i in receiver_party.identifiers
            )
            if not receiver_matches:
                results.append(
                    CheckResult(
                        check_id="CF-10",
                        title="Integrity",
                        status=CheckStatus.FAIL,
                        detail="tx.receiver_identifier does not belong to receiver party.",
                        case_name=case.name,
                    )
                )
                continue

            results.append(
                CheckResult(
                    check_id="CF-10",
                    title="Integrity",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-10",
                    title="Integrity",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-11: Status and settlement coherence ---
_TERMINAL_NON_SETTLED_STATUSES = {
    TransactionStatus.INITIATED,
    TransactionStatus.PENDING_AUTHORIZATION,
    TransactionStatus.AUTHORIZED,
    TransactionStatus.PENDING_SETTLEMENT,
    TransactionStatus.FAILED,
    TransactionStatus.REJECTED,
    TransactionStatus.CANCELLED,
}


def check_cf11_status_settlement_coherence(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify settled_at is present for SETTLED/CONFIRMED and None for other statuses."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            res = adapter.normalize(ev)
            tx = res.transaction
            if tx.status in {TransactionStatus.SETTLED, TransactionStatus.CONFIRMED}:
                if tx.settled_at is None:
                    results.append(
                        CheckResult(
                            check_id="CF-11",
                            title="Status and settlement coherence",
                            status=CheckStatus.FAIL,
                            detail=f"Status {tx.status} requires settled_at to be set.",
                            case_name=case.name,
                        )
                    )
                    continue
            elif tx.status in _TERMINAL_NON_SETTLED_STATUSES:
                if tx.settled_at is not None:
                    results.append(
                        CheckResult(
                            check_id="CF-11",
                            title="Status and settlement coherence",
                            status=CheckStatus.FAIL,
                            detail=f"Status {tx.status} must have settled_at=None.",
                            case_name=case.name,
                        )
                    )
                    continue

            results.append(
                CheckResult(
                    check_id="CF-11",
                    title="Status and settlement coherence",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-11",
                    title="Status and settlement coherence",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-12: Money discipline ---
def check_cf12_money_discipline(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify exact Decimal scale representation and complete absence of IEEE floats."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            res = adapter.normalize(ev)
            tx = res.transaction

            # Amount check
            if not isinstance(tx.amount.amount, Decimal):
                results.append(
                    CheckResult(
                        check_id="CF-12",
                        title="Money discipline",
                        status=CheckStatus.FAIL,
                        detail=(
                            "tx.amount.amount is not Decimal, got "
                            f"{type(tx.amount.amount).__name__}."
                        ),
                        case_name=case.name,
                    )
                )
                continue
            if tx.amount.scale > 18:
                results.append(
                    CheckResult(
                        check_id="CF-12",
                        title="Money discipline",
                        status=CheckStatus.FAIL,
                        detail=f"tx.amount.scale {tx.amount.scale} exceeds universal max 18.",
                        case_name=case.name,
                    )
                )
                continue

            # Fee check
            if tx.fee is not None:
                if not isinstance(tx.fee.amount, Decimal):
                    results.append(
                        CheckResult(
                            check_id="CF-12",
                            title="Money discipline",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"tx.fee.amount is not Decimal, got {type(tx.fee.amount).__name__}."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue
                if tx.fee.scale > 18:
                    results.append(
                        CheckResult(
                            check_id="CF-12",
                            title="Money discipline",
                            status=CheckStatus.FAIL,
                            detail=f"tx.fee.scale {tx.fee.scale} exceeds universal maximum of 18.",
                            case_name=case.name,
                        )
                    )
                    continue

            # Recursive float scan
            dumped = res.model_dump()
            float_violations = _check_no_floats(dumped)
            if float_violations:
                results.append(
                    CheckResult(
                        check_id="CF-12",
                        title="Money discipline",
                        status=CheckStatus.FAIL,
                        detail=f"Float found in model_dump(): {'; '.join(float_violations[:3])}",
                        case_name=case.name,
                    )
                )
                continue

            results.append(
                CheckResult(
                    check_id="CF-12",
                    title="Money discipline",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-12",
                    title="Money discipline",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-13: Serialization round-trip ---
def check_cf13_serialization_roundtrip(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify JSON serialization and re-validation into identical NormalizationResult."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        try:
            res = adapter.normalize(ev)
            dumped = res.model_dump(mode="json")
            json_str = json.dumps(dumped)
            re_loaded = json.loads(json_str)
            re_validated = NormalizationResult.model_validate(re_loaded)

            if re_validated.model_dump() != res.model_dump():
                results.append(
                    CheckResult(
                        check_id="CF-13",
                        title="Serialization round-trip",
                        status=CheckStatus.FAIL,
                        detail="Re-validated NormalizationResult differs from original.",
                        case_name=case.name,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        check_id="CF-13",
                        title="Serialization round-trip",
                        status=CheckStatus.PASS,
                        case_name=case.name,
                    )
                )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-13",
                    title="Serialization round-trip",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-14: Input immutability ---
def check_cf14_input_immutability(
    adapter: NetworkAdapter, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify adapter does not mutate the caller's input payload."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        ev = _make_raw_event(case, network_id)
        original_ev_payload = copy.deepcopy(ev.payload)
        original_case_payload = copy.deepcopy(case.payload)
        try:
            adapter.normalize(ev)
            if ev.payload != original_ev_payload or case.payload != original_case_payload:
                results.append(
                    CheckResult(
                        check_id="CF-14",
                        title="Input immutability",
                        status=CheckStatus.FAIL,
                        detail="Caller input payload was mutated during normalize().",
                        case_name=case.name,
                    )
                )
            else:
                results.append(
                    CheckResult(
                        check_id="CF-14",
                        title="Input immutability",
                        status=CheckStatus.PASS,
                        case_name=case.name,
                    )
                )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-14",
                    title="Input immutability",
                    status=CheckStatus.FAIL,
                    detail=str(exc),
                    case_name=case.name,
                )
            )
    return results


# --- CF-15: Statelessness ---
def check_cf15_statelessness(
    adapter: NetworkAdapter, options: ConformanceOptions, ok_cases: list[ConformanceCase]
) -> CheckResult:
    """Verify concurrent execution across threads produces identical output to serial execution."""
    if not ok_cases:
        return CheckResult(
            check_id="CF-15",
            title="Statelessness",
            status=CheckStatus.SKIP,
            detail="No ok cases to run.",
        )

    try:
        network_id = _get_network_id(adapter)
        serial_outputs: list[dict[str, Any]] = []
        for case in ok_cases:
            ev = _make_raw_event(case, network_id)
            res = adapter.normalize(ev)
            serial_outputs.append(res.model_dump())

        def _worker(case_idx: int) -> dict[str, Any]:
            case = ok_cases[case_idx]
            ev = _make_raw_event(case, network_id)
            return adapter.normalize(ev).model_dump()

        thread_count = max(2, options.thread_count)
        with ThreadPoolExecutor(max_workers=thread_count) as executor:
            tasks = []
            for _ in range(3):
                for idx in range(len(ok_cases)):
                    tasks.append((idx, executor.submit(_worker, idx)))

            for idx, fut in tasks:
                concurrent_out = fut.result()
                if concurrent_out != serial_outputs[idx]:
                    return CheckResult(
                        check_id="CF-15",
                        title="Statelessness",
                        status=CheckStatus.FAIL,
                        detail=(
                            "Concurrent output differs from serial for case "
                            f"'{ok_cases[idx].name}'."
                        ),
                        case_name=ok_cases[idx].name,
                    )

        return CheckResult(check_id="CF-15", title="Statelessness", status=CheckStatus.PASS)
    except Exception as exc:
        return CheckResult(
            check_id="CF-15", title="Statelessness", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-16: Error contract ---
def check_cf16_error_contract(
    adapter: NetworkAdapter, error_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify error cases raise expected AdapterError subclasses without leaking raw values."""
    results: list[CheckResult] = []
    if not error_cases:
        return [
            CheckResult(
                check_id="CF-16",
                title="Error contract",
                status=CheckStatus.SKIP,
                detail="No error cases declared in manifest.",
            )
        ]

    network_id = _get_network_id(adapter)
    for case in error_cases:
        ev = _make_raw_event(case, network_id)
        try:
            adapter.normalize(ev)
            results.append(
                CheckResult(
                    check_id="CF-16",
                    title="Error contract",
                    status=CheckStatus.FAIL,
                    detail="Normalization succeeded unexpectedly on an error case.",
                    case_name=case.name,
                )
            )
        except AdapterError as ae:
            if case.error_type and ae.__class__.__name__ != case.error_type:
                results.append(
                    CheckResult(
                        check_id="CF-16",
                        title="Error contract",
                        status=CheckStatus.FAIL,
                        detail=(
                            f"Expected error type '{case.error_type}', got "
                            f"'{ae.__class__.__name__}'."
                        ),
                        case_name=case.name,
                    )
                )
                continue

            if isinstance(ae, NormalizationError):
                if not ae.field or not ae.field.strip():
                    results.append(
                        CheckResult(
                            check_id="CF-16",
                            title="Error contract",
                            status=CheckStatus.FAIL,
                            detail="NormalizationError has empty field attribute.",
                            case_name=case.name,
                        )
                    )
                    continue
                if not ae.reason or not ae.reason.strip():
                    results.append(
                        CheckResult(
                            check_id="CF-16",
                            title="Error contract",
                            status=CheckStatus.FAIL,
                            detail="NormalizationError has empty reason attribute.",
                            case_name=case.name,
                        )
                    )
                    continue
                if case.error_field and ae.field != case.error_field:
                    results.append(
                        CheckResult(
                            check_id="CF-16",
                            title="Error contract",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Expected error_field '{case.error_field}', got " f"'{ae.field}'."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue

            exc_texts = f"{str(ae)} {repr(ae)} {' '.join(str(a) for a in ae.args)}"
            leaked: list[str] = [s for s in case.must_not_appear if s in exc_texts]
            if leaked:
                results.append(
                    CheckResult(
                        check_id="CF-16",
                        title="Error contract",
                        status=CheckStatus.FAIL,
                        detail=f"Exception text leaked forbidden string: '{leaked[0]}'.",
                        case_name=case.name,
                    )
                )
                continue

            results.append(
                CheckResult(
                    check_id="CF-16",
                    title="Error contract",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-16",
                    title="Error contract",
                    status=CheckStatus.FAIL,
                    detail=f"Unwrapped non-AdapterError: {type(exc).__name__}: {exc}",
                    case_name=case.name,
                )
            )
    return results


# --- CF-17: Batch isolation ---
def check_cf17_batch_isolation(
    adapter: NetworkAdapter,
    ok_cases: list[ConformanceCase],
    error_cases: list[ConformanceCase],
) -> CheckResult:
    """Verify normalize_batch on [ok, error, ok] isolates failures from successes."""
    if not ok_cases or not error_cases:
        return CheckResult(
            check_id="CF-17",
            title="Batch isolation",
            status=CheckStatus.SKIP,
            detail="Requires at least one ok case and one error case.",
        )

    try:
        network_id = _get_network_id(adapter)
        ok1 = _make_raw_event(ok_cases[0], network_id)
        err = _make_raw_event(error_cases[0], network_id)
        ok2 = _make_raw_event(ok_cases[1] if len(ok_cases) > 1 else ok_cases[0], network_id)

        batch = [ok1, err, ok2]
        batch_res = adapter.normalize_batch(batch)

        if batch_res.total_count != 3:
            return CheckResult(
                check_id="CF-17",
                title="Batch isolation",
                status=CheckStatus.FAIL,
                detail=f"normalize_batch processed {batch_res.total_count} items, expected 3.",
            )

        if batch_res.success_count != 2:
            return CheckResult(
                check_id="CF-17",
                title="Batch isolation",
                status=CheckStatus.FAIL,
                detail=f"normalize_batch returned {batch_res.success_count} successes, expected 2.",
            )
        if batch_res.failure_count != 1:
            return CheckResult(
                check_id="CF-17",
                title="Batch isolation",
                status=CheckStatus.FAIL,
                detail=f"normalize_batch returned {batch_res.failure_count} failures, expected 1.",
            )

        return CheckResult(check_id="CF-17", title="Batch isolation", status=CheckStatus.PASS)
    except Exception as exc:
        return CheckResult(
            check_id="CF-17", title="Batch isolation", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-18: Leak scan ---
class _LogCaptureHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def check_cf18_leak_scan(
    adapter: NetworkAdapter, cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify sensitive fields never leak in JSON, warnings, logs, or exception texts."""
    results: list[CheckResult] = []
    root_logger = logging.getLogger()
    old_level = root_logger.level
    root_logger.setLevel(logging.DEBUG)
    network_id = _get_network_id(adapter)

    try:
        for case in cases:
            ev = _make_raw_event(case, network_id)
            handler = _LogCaptureHandler()
            handler.setLevel(logging.DEBUG)
            root_logger.addHandler(handler)

            exc_str = ""
            res: NormalizationResult | None = None
            try:
                res = adapter.normalize(ev)
            except Exception as e:
                e_args = ""
                if hasattr(e, "args"):
                    e_args = " ".join(str(a) for a in getattr(e, "args", ()))
                exc_str = f"{str(e)} {repr(e)} {e_args}"
            finally:
                root_logger.removeHandler(handler)

            serialized_result = res.model_dump_json() if res is not None else ""
            warnings_str = " ".join(res.warnings) if res is not None else ""
            logs_str = " ".join(f"{r.getMessage()} {r.exc_text or ''}" for r in handler.records)

            all_emitted_text = f"{serialized_result} {warnings_str} {logs_str} {exc_str}"

            # Invariant 1: must_not_appear
            forbidden_found: list[str] = [
                term for term in case.must_not_appear if term in all_emitted_text
            ]
            if forbidden_found:
                results.append(
                    CheckResult(
                        check_id="CF-18",
                        title="Leak scan",
                        status=CheckStatus.FAIL,
                        detail=f"Forbidden term leaked in output: '{forbidden_found[0]}'.",
                        case_name=case.name,
                    )
                )
                continue

            # Invariant 2: must_appear for ok cases
            if case.expect == "ok" and res is not None:
                missing_terms: list[str] = [
                    term for term in case.must_appear if term not in serialized_result
                ]
                if missing_terms:
                    results.append(
                        CheckResult(
                            check_id="CF-18",
                            title="Leak scan",
                            status=CheckStatus.FAIL,
                            detail=(
                                f"Required term '{missing_terms[0]}' missing from "
                                "serialized result."
                            ),
                            case_name=case.name,
                        )
                    )
                    continue

            results.append(
                CheckResult(
                    check_id="CF-18",
                    title="Leak scan",
                    status=CheckStatus.PASS,
                    case_name=case.name,
                )
            )
    finally:
        root_logger.setLevel(old_level)

    return results


# --- CF-19: Mutation robustness ---
def check_cf19_mutation_robustness(
    adapter: NetworkAdapter, options: ConformanceOptions, ok_cases: list[ConformanceCase]
) -> list[CheckResult]:
    """Verify deterministic mutation fuzzing yields typed AdapterErrors or valid results."""
    results: list[CheckResult] = []
    network_id = _get_network_id(adapter)
    for case in ok_cases:
        mutants = generate_mutants(case.payload, cap=options.mutation_cap_per_case)
        failed_mutant: str | None = None

        for path_str, mut_name, mutant_payload in mutants:
            mut_ev = RawEvent(
                network_id=network_id,
                payload=mutant_payload,
                payload_format=case.payload_format,
                received_at=case.received_at,
            )
            try:
                mut_res = adapter.normalize(mut_ev)
                if _check_no_floats(mut_res.model_dump()):
                    failed_mutant = f"Path '{path_str}' ({mut_name}): produced float in result."
                    break
                if _check_utc_datetimes(mut_res.model_dump()):
                    failed_mutant = f"Path '{path_str}' ({mut_name}): produced non-UTC datetime."
                    break
            except AdapterError:
                pass
            except Exception as unhandled_exc:
                failed_mutant = (
                    f"Path '{path_str}' ({mut_name}): raised unhandled "
                    f"{type(unhandled_exc).__name__}: {unhandled_exc}"
                )
                break

        if failed_mutant:
            results.append(
                CheckResult(
                    check_id="CF-19",
                    title="Mutation robustness",
                    status=CheckStatus.FAIL,
                    detail=failed_mutant,
                    case_name=case.name,
                )
            )
        else:
            results.append(
                CheckResult(
                    check_id="CF-19",
                    title="Mutation robustness",
                    status=CheckStatus.PASS,
                    detail=f"Tested {len(mutants)} deterministic mutants.",
                    case_name=case.name,
                )
            )
    return results


# --- CF-20: Performance smoke ---
def check_cf20_performance_smoke(
    adapter: NetworkAdapter, options: ConformanceOptions, ok_cases: list[ConformanceCase]
) -> CheckResult:
    """Measure p50 and p95 normalization latency across 200 iterations."""
    if not ok_cases:
        return CheckResult(
            check_id="CF-20",
            title="Performance smoke",
            status=CheckStatus.SKIP,
            detail="No ok cases to benchmark.",
        )

    try:
        network_id = _get_network_id(adapter)
        case = ok_cases[0]
        ev = _make_raw_event(case, network_id)
        latencies_ms: list[float] = []

        for _ in range(200):
            t0 = time.perf_counter()
            adapter.normalize(ev)
            t1 = time.perf_counter()
            latencies_ms.append((t1 - t0) * 1000.0)

        latencies_ms.sort()
        p50 = latencies_ms[100]
        p95 = latencies_ms[190]

        detail = f"p50: {p50:.2f}ms, p95: {p95:.2f}ms (ceiling: {options.timing_ceiling_ms:.1f}ms)"
        if p95 > options.timing_ceiling_ms:
            return CheckResult(
                check_id="CF-20",
                title="Performance smoke",
                status=CheckStatus.FAIL,
                detail=(
                    f"p95 latency {p95:.2f}ms exceeded ceiling "
                    f"{options.timing_ceiling_ms:.1f}ms."
                ),
            )

        return CheckResult(
            check_id="CF-20", title="Performance smoke", status=CheckStatus.PASS, detail=detail
        )
    except Exception as exc:
        return CheckResult(
            check_id="CF-20", title="Performance smoke", status=CheckStatus.FAIL, detail=str(exc)
        )


# --- CF-21: Cross-network rejection ---
def check_cf21_cross_network_rejection(
    adapter: NetworkAdapter, foreign_cases: list[ConformanceCase] | None
) -> list[CheckResult]:
    """Verify adapter rejects payloads from foreign networks addressed to its network_id."""
    results: list[CheckResult] = []
    if not foreign_cases:
        return [
            CheckResult(
                check_id="CF-21",
                title="Cross-network rejection",
                status=CheckStatus.SKIP,
                detail="No foreign cases supplied.",
            )
        ]

    network_id = _get_network_id(adapter)
    for foreign in foreign_cases:
        ev = RawEvent(
            network_id=network_id,
            payload=copy.deepcopy(foreign.payload),
            payload_format=foreign.payload_format,
            received_at=foreign.received_at,
        )
        try:
            adapter.normalize(ev)
            results.append(
                CheckResult(
                    check_id="CF-21",
                    title="Cross-network rejection",
                    status=CheckStatus.FAIL,
                    detail=(f"Foreign payload from case '{foreign.name}' unexpectedly normalized."),
                    case_name=foreign.name,
                )
            )
        except AdapterError:
            results.append(
                CheckResult(
                    check_id="CF-21",
                    title="Cross-network rejection",
                    status=CheckStatus.PASS,
                    case_name=foreign.name,
                )
            )
        except Exception as exc:
            results.append(
                CheckResult(
                    check_id="CF-21",
                    title="Cross-network rejection",
                    status=CheckStatus.FAIL,
                    detail=(
                        f"Foreign case '{foreign.name}' raised non-AdapterError "
                        f"{type(exc).__name__}: {exc}"
                    ),
                    case_name=foreign.name,
                )
            )
    return results
