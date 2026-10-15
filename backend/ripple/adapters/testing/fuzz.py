"""Deterministic mutation fuzzing generator for adapter payloads.

Systematically enumerates JSON paths and applies deterministic perturbations
to test error-boundary resilience and type-safety without randomness.
"""

from __future__ import annotations

import copy
from typing import Any

_MUTATIONS: list[tuple[str, Any]] = [
    ("delete", "__DELETE__"),
    ("null", None),
    ("empty_str", ""),
    ("int", 123),
    ("bool", True),
    ("float", 12.34),
    ("negative_num", -50),
    ("empty_list", []),
    ("empty_dict", {}),
    ("short_str", "x"),
    ("long_str", "x" * 10000),
]


def _enumerate_paths(
    obj: Any, current_path: tuple[str | int, ...] = (), depth: int = 1
) -> list[tuple[str | int, ...]]:
    """Enumerate all JSON paths up to depth 3 (first 3 list items)."""
    paths: list[tuple[str | int, ...]] = []
    if depth > 3:
        return paths

    if isinstance(obj, dict):
        for k in sorted(str(k) for k in obj.keys()):
            p = (*current_path, k)
            paths.append(p)
            val = obj.get(k)
            paths.extend(_enumerate_paths(val, p, depth + 1))
    elif isinstance(obj, list):
        for i in range(min(len(obj), 3)):
            p = (*current_path, i)
            paths.append(p)
            paths.extend(_enumerate_paths(obj[i], p, depth + 1))

    return paths


def _apply_mutation(
    payload: dict[str, Any], path: tuple[str | int, ...], value: Any
) -> dict[str, Any]:
    """Clone payload and apply a mutation at the given path."""
    clone = copy.deepcopy(payload)
    curr: Any = clone
    for part in path[:-1]:
        if isinstance(curr, dict) and isinstance(part, str):
            curr = curr.get(part)
        elif isinstance(curr, list) and isinstance(part, int) and 0 <= part < len(curr):
            curr = curr[part]
        else:
            return clone  # Path unreachable, return unchanged

    last_part = path[-1]
    if value == "__DELETE__":
        if isinstance(curr, dict) and isinstance(last_part, str):
            curr.pop(last_part, None)
        elif isinstance(curr, list) and isinstance(last_part, int) and 0 <= last_part < len(curr):
            curr.pop(last_part)
    else:
        if isinstance(curr, dict) and isinstance(last_part, str):
            curr[last_part] = value
        elif isinstance(curr, list) and isinstance(last_part, int) and 0 <= last_part < len(curr):
            curr[last_part] = value

    return clone


def generate_mutants(
    payload: dict[str, Any], cap: int = 1500
) -> list[tuple[str, str, dict[str, Any]]]:
    """Generate deterministic mutants for a payload.

    Args:
        payload: Canonical payload dictionary.
        cap: Maximum number of mutants to return.

    Returns:
        List of (path_string, mutation_name, mutant_payload).
    """
    paths = _enumerate_paths(payload)
    all_mutants: list[tuple[str, str, dict[str, Any]]] = []

    for path in paths:
        path_str = ".".join(str(p) for p in path)
        for mut_name, mut_val in _MUTATIONS:
            mutant_dict = _apply_mutation(payload, path, mut_val)
            all_mutants.append((path_str, mut_name, mutant_dict))

    if len(all_mutants) <= cap or cap <= 0:
        return all_mutants

    # Evenly-spaced deterministic subsampling across the entire space
    step = len(all_mutants) / float(cap)
    selected: list[tuple[str, str, dict[str, Any]]] = []
    for i in range(cap):
        idx = int(i * step)
        selected.append(all_mutants[idx])

    return selected
