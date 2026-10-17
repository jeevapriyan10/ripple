"""Calibration profiles, parameter validation, and provenance tracking.

Rule R4 Honesty:
- Every parameter is an object with {value, unit, provenance, citation, note}.
- A parameter with provenance 'published' MUST provide a non-empty citation.
- Every shipped parameter has provenance 'assumption' with citation null.
- Uncalibrated parameters must be plainly labeled.
"""

import copy
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CALIBRATION_DIR = Path(__file__).parent / "calibration"


class CalibrationError(Exception):
    """Raised when a calibration profile is malformed or violates honesty rules."""

    pass


class CalibrationProfile:
    """Immutable parameter container with provenance tracking and access logging."""

    def __init__(self, raw_data: dict[str, Any], name: str = "default") -> None:
        self.name = name
        self._raw_data = copy.deepcopy(raw_data)
        self._accessed_keys: set[str] = set()
        self._flat_params: dict[str, dict[str, Any]] = {}
        self._flatten("", self._raw_data)
        self._validate()

    def _flatten(self, prefix: str, obj: Any) -> None:
        if isinstance(obj, dict):
            # Check if this dict is a leaf parameter object: has "value" and "provenance"
            if "value" in obj and "provenance" in obj:
                self._flat_params[prefix] = obj
            else:
                for k, v in obj.items():
                    sub_prefix = f"{prefix}.{k}" if prefix else k
                    self._flatten(sub_prefix, v)

    def _validate(self) -> None:
        """Validate loader rules: published parameters must have non-empty citation."""
        for key, p in self._flat_params.items():
            if not isinstance(p, dict):
                raise CalibrationError(f"Parameter '{key}' must be an object")
            prov = p.get("provenance")
            if prov not in {"assumption", "published"}:
                raise CalibrationError(
                    f"Parameter '{key}' has invalid provenance '{prov}'; "
                    "must be 'assumption' or 'published'."
                )
            citation = p.get("citation")
            if prov == "published" and (not citation or not str(citation).strip()):
                raise CalibrationError(
                    f"Parameter '{key}' has provenance 'published' but missing citation."
                )

    def get(self, path: str, default: Any = None) -> Any:
        """Retrieve a parameter value by dotted path, recording access for coverage checks."""
        self._accessed_keys.add(path)
        if path in self._flat_params:
            return self._flat_params[path]["value"]

        # Traversal for non-leaf nested structures or values
        parts = path.split(".")
        curr: Any = self._raw_data
        for p in parts:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            else:
                if default is not None:
                    return default
                raise KeyError(f"Calibration parameter '{path}' not found in profile '{self.name}'")

        if isinstance(curr, dict) and "value" in curr:
            return curr["value"]
        return curr

    def get_param(self, path: str) -> dict[str, Any]:
        """Retrieve the parameter descriptor object {value, unit, provenance, citation, note}."""
        self._accessed_keys.add(path)
        if path in self._flat_params:
            return copy.deepcopy(self._flat_params[path])
        raise KeyError(f"Calibration parameter '{path}' not found in profile '{self.name}'")

    def summary(self) -> dict[str, int]:
        """Return counts of parameters grouped by provenance ('assumption', 'published')."""
        counts = {"assumption": 0, "published": 0}
        for p in self._flat_params.values():
            prov = p.get("provenance", "assumption")
            counts[prov] = counts.get(prov, 0) + 1
        return counts

    def profile_hash(self) -> str:
        """Return SHA-256 hex digest of the canonical JSON profile data."""
        canonical_str = json.dumps(self._raw_data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()

    def unused_parameters(self, filter_prefixes: Sequence[str] | None = None) -> list[str]:
        """Return list of parameter paths that were never queried during simulation."""
        all_keys = set(self._flat_params.keys())
        unused = sorted(all_keys - self._accessed_keys)
        if filter_prefixes:
            filtered: list[str] = []
            for k in unused:
                if any(k.startswith(p) for p in filter_prefixes):
                    filtered.append(k)
            return filtered
        return unused

    def override(self, overrides: dict[str, Any]) -> "CalibrationProfile":
        """Produce a new immutable CalibrationProfile with applied value overrides."""
        new_data = copy.deepcopy(self._raw_data)
        for key, new_val in overrides.items():
            parts = key.split(".")
            curr = new_data
            for p in parts[:-1]:
                if p not in curr or not isinstance(curr[p], dict):
                    curr[p] = {}
                curr = curr[p]
            leaf = parts[-1]
            if isinstance(curr.get(leaf), dict) and "value" in curr[leaf]:
                curr[leaf]["value"] = new_val
            else:
                curr[leaf] = {
                    "value": new_val,
                    "unit": "override",
                    "provenance": "assumption",
                    "citation": None,
                    "note": f"Runtime override of {key}",
                }
        return CalibrationProfile(new_data, name=f"{self.name}_override")


def load_calibration_profile(name: str = "default") -> CalibrationProfile:
    """Load calibration profile from package data by name."""
    target_path = CALIBRATION_DIR / f"{name}.json"
    if not target_path.exists():
        raise FileNotFoundError(f"Calibration profile '{name}' not found at {target_path}")

    with open(target_path, encoding="utf-8") as f:
        data = json.load(f)

    return CalibrationProfile(data, name=name)
