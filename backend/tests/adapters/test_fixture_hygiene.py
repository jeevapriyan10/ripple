"""Repo-level fixture hygiene test (Part B4).

Scans every JSON file under backend/tests/**/fixtures and adapter source files for:
1. Luhn-valid card numbers (13-19 digits) not in the published test card allow-list.
2. Email addresses on non-reserved domains (only example.com, example.org, example.net,
   *.test, *.invalid, *.example are allowed).
3. Private key headers (-----BEGIN).
4. Cloud key patterns (AKIA + 16 chars).
5. Validates fixture_hygiene.allow.json entries have non-empty justification reasons.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = REPO_ROOT / "tests"
ALLOW_FILE = Path(__file__).resolve().parent / "fixture_hygiene.allow.json"
ADAPTERS_SRC_DIR = REPO_ROOT / "ripple" / "adapters"

PUBLISHED_TEST_CARDS = {
    "4111111111111111",  # Visa test
    "5555555555554444",  # Mastercard test
    "378282246310005",  # Amex test
    "6011111111111117",  # Discover test
}

RESERVED_DOMAINS = {"example.com", "example.org", "example.net"}
RESERVED_TLDS = (".test", ".invalid", ".example")

EMAIL_REGEX = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
CLOUD_KEY_REGEX = re.compile(r"AKIA[0-9A-Z]{16}")
PRIVATE_KEY_HEADER = "-----BEGIN"


def is_luhn_valid(num_str: str) -> bool:
    """Check Luhn checksum for a numeric string."""
    digits = [int(c) for c in num_str]
    checksum = 0
    reverse_digits = digits[::-1]
    for i, digit in enumerate(reverse_digits):
        if i % 2 == 1:
            doubled = digit * 2
            checksum += doubled - 9 if doubled > 9 else doubled
        else:
            checksum += digit
    return checksum % 10 == 0


def is_reserved_email(domain: str) -> bool:
    """Verify email domain is strictly within the RFC/IANA reserved domains."""
    d = domain.lower()
    return d in RESERVED_DOMAINS or any(d.endswith(tld) for tld in RESERVED_TLDS)


def iter_json_strings(obj: Any, pointer: str = "") -> Iterator[tuple[str, str]]:
    """Yield (json_pointer, string_value) for all string leaves in a JSON structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            escaped_k = k.replace("~", "~0").replace("/", "~1")
            yield from iter_json_strings(v, f"{pointer}/{escaped_k}")
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            yield from iter_json_strings(item, f"{pointer}/{idx}")
    elif isinstance(obj, str):
        yield pointer, obj


def load_allow_list() -> dict[str, str]:
    """Load and validate the fixture hygiene allow-list."""
    if not ALLOW_FILE.is_file():
        return {}
    content = ALLOW_FILE.read_text(encoding="utf-8")
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("fixture_hygiene.allow.json must be a JSON object mapping to reasons.")
    return {str(k): str(v) for k, v in data.items() if not str(k).startswith("$")}


def test_allow_list_entries_have_valid_reasons() -> None:
    """Every entry in fixture_hygiene.allow.json must have a mandatory non-empty reason."""
    allow_list = load_allow_list()
    for key, reason in allow_list.items():
        assert reason.strip(), f"Allow-list entry '{key}' must have a non-empty reason."


def test_fixture_hygiene_scan() -> None:
    """Scan all fixture JSON files for card numbers, non-reserved emails, and secrets."""
    allow_list = load_allow_list()
    fixture_files = sorted(FIXTURES_DIR.glob("**/fixtures/**/*.json"))
    assert fixture_files, "No fixture files found to scan."

    violations: list[str] = []

    for file_path in fixture_files:
        rel_path = file_path.relative_to(REPO_ROOT).as_posix()
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            violations.append(f"{rel_path}: Failed to parse JSON: {exc}")
            continue

        for pointer, val in iter_json_strings(data):
            lookup_key_rel = f"{rel_path}#{pointer}"
            lookup_key_name = f"{file_path.name}#{pointer}"

            if lookup_key_rel in allow_list or lookup_key_name in allow_list:
                continue

            # Check 1: Private key header
            if PRIVATE_KEY_HEADER in val:
                violations.append(
                    f"{rel_path}#{pointer}: Contains private key header '{PRIVATE_KEY_HEADER}'"
                )

            # Check 2: Cloud key pattern
            if CLOUD_KEY_REGEX.search(val):
                violations.append(
                    f"{rel_path}#{pointer}: Contains cloud key pattern matching AKIA*"
                )

            # Check 3: Luhn card number
            cleaned_digits = val.replace(" ", "").replace("-", "")
            if cleaned_digits.isdigit() and 13 <= len(cleaned_digits) <= 19:
                if is_luhn_valid(cleaned_digits):
                    if cleaned_digits not in PUBLISHED_TEST_CARDS:
                        violations.append(
                            f"{rel_path}#{pointer}: Contains unapproved Luhn-valid card number"
                        )

            # Check 4: Non-reserved emails
            for match in EMAIL_REGEX.finditer(val):
                domain = match.group(1)
                if not is_reserved_email(domain):
                    violations.append(
                        f"{rel_path}#{pointer}: Email '{match.group(0)}' "
                        f"uses non-reserved domain '{domain}'"
                    )

    assert not violations, f"Fixture hygiene violations found ({len(violations)}):\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_adapter_source_hygiene_scan() -> None:
    """Scan Python source files of ripple/adapters for hard-coded credentials or leaks."""
    source_files = sorted(ADAPTERS_SRC_DIR.glob("**/*.py"))
    assert source_files, "No adapter source files found to scan."

    violations: list[str] = []

    for file_path in source_files:
        rel_path = file_path.relative_to(REPO_ROOT).as_posix()
        content = file_path.read_text(encoding="utf-8")

        # Check 1: Private key header
        if PRIVATE_KEY_HEADER in content:
            violations.append(f"{rel_path}: Contains private key header '{PRIVATE_KEY_HEADER}'")

        # Check 2: Cloud key pattern
        if CLOUD_KEY_REGEX.search(content):
            violations.append(f"{rel_path}: Contains cloud key pattern matching AKIA*")

        # Check 3: Non-reserved emails
        for match in EMAIL_REGEX.finditer(content):
            domain = match.group(1)
            if not is_reserved_email(domain):
                violations.append(
                    f"{rel_path}: Email '{match.group(0)}' uses non-reserved domain '{domain}'"
                )

        # Check 4: Unapproved Luhn card numbers
        for token in re.findall(r"\b[0-9\- ]{13,24}\b", content):
            clean = token.replace(" ", "").replace("-", "")
            if clean.isdigit() and 13 <= len(clean) <= 19 and is_luhn_valid(clean):
                if clean not in PUBLISHED_TEST_CARDS:
                    violations.append(f"{rel_path}: Contains unapproved Luhn-valid card number")

    assert not violations, f"Source hygiene violations found ({len(violations)}):\n" + "\n".join(
        f"  - {v}" for v in violations
    )
