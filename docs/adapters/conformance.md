# Adapter Conformance Suite (`ripple.adapters.testing`)

## Overview

The Adapter Conformance Suite is a reusable, automated, data-driven test library that verifies every adapter—whether built into Ripple or registered dynamically as a third-party plugin—strictly conforms to the Ripple Adapter Contract, domain invariants, temporal hygiene, security quarantine, and performance bounds.

The suite ships inside the main distribution package at `backend/ripple/adapters/testing/`. It is completely decoupled from the test runner (standard library and Pydantic v2 only, with **zero pytest imports in package code**), allowing downstream consumers or external plugin developers to run conformance checks directly in their own test environments.

---

## The 21 Conformance Checks (CF-01 to CF-21)

Every adapter must pass all 21 checks unless an explicit exemption is justified in its manifest:

| Check ID | Check Name | What It Guards Against |
|---|---|---|
| **CF-01** | Contract surface | Missing `NetworkAdapter` subclassing, invalid profile properties, empty version/description, non-serializable `describe()`, or malformed `network_id`. |
| **CF-02** | Registry integration | Registration failures in an isolated `AdapterRegistry`, failed `get()` or `route()` lookups, or non-functional `from_settings()`. |
| **CF-03** | `can_handle` safety | Returning `False` on valid rail events, raising unexpected exceptions on garbage/malformed payloads, or non-boolean returns. |
| **CF-04** | Determinism | Inconsistent outputs across repeated calls or across fresh adapter instances for identical inputs (e.g. random IDs, unseeded state). |
| **CF-05** | Static purity scan | AST scan of adapter source code detecting prohibited system calls (`uuid.uuid4()`, `datetime.now()`, `time.time()`, `random.*`, `os.environ`, `open()`, `subprocess`, `socket`, `requests`, `httpx`). |
| **CF-06** | Dynamic purity guards | Runtime monkey-patching of `uuid4`, `socket.connect`, `subprocess.Popen`, and write-mode `open()` ensuring no I/O or entropy calls execute during normalization. |
| **CF-07** | Time discipline | System clock reads during event processing. Ensures `transaction.observed_at == event.received_at` and every datetime in output has timezone `UTC` with zero offset. |
| **CF-08** | Network stamping & profile consistency | Identity/transaction network mismatch, invalid identifier types for profile, unauthorized currencies, unlisted asset classes, hops when intermediaries are invisible, or invalid reversal windows. |
| **CF-09** | Lifecycle identity stability | Inconsistent `correlation_key`, `transaction_id`, `sender_id`, or `receiver_id` across distinct lifecycle snapshots of a single real-world transaction. |
| **CF-10** | Graph integrity | Missing sender/receiver in `parties`, duplicate party IDs, parties with zero identifiers, or transaction endpoint identifiers not belonging to their respective party. |
| **CF-11** | Status and settlement coherence | Missing `settled_at` on `SETTLED` or `CONFIRMED` transactions; present `settled_at` on unfinalized, pending, or failed transactions. |
| **CF-12** | Money discipline | Representation of monetary amounts as IEEE floating-point numbers. Enforces exact `Decimal` representation, scale $\le 18$, and recursive zero-float verification. |
| **CF-13** | Serialization round-trip | Inability of `NormalizationResult.model_dump(mode="json")` to serialize to standard JSON and re-validate losslessly into an identical `NormalizationResult` (critical for Pulse Kafka streaming). |
| **CF-14** | Input immutability | Mutating the caller's input payload in-place (e.g. popping fields, mutating nested dictionaries). |
| **CF-15** | Statelessness | Thread contention or shared state corruption. Asserts concurrent multi-threaded execution across 8 threads matches serial execution bit-for-bit. |
| **CF-16** | Error contract | Non-AdapterError exceptions escaping the adapter (e.g. bare `KeyError`, `ValueError`, `TypeError`), incorrect error subclasses, or leaking sensitive strings in exception messages. |
| **CF-17** | Batch isolation | Failures in `normalize_batch` cascading to adjacent events. Asserts `[ok, error, ok]` yields 2 successes and 1 failure in precise order. |
| **CF-18** | Leak scan | Upstream credentials (PANs, CVVs, passwords) or personal identifiers (emails, names) leaking into serialized JSON, warning messages, logger output (DEBUG), or exception strings. |
| **CF-19** | Mutation robustness | Deterministic mutation fuzzing across payload paths (null, empty string, float, negative, huge strings, missing keys). Enforces that every mutant either normalizes cleanly or raises a typed `AdapterError`. |
| **CF-20** | Performance smoke | Normalization latency regression. Measures p50 and p95 latency over 200 runs; fails if p95 exceeds `timing_ceiling_ms` (default 250ms). |
| **CF-21** | Cross-network rejection | Permissive acceptance of foreign network payloads. Every valid payload from another rail, re-addressed to this adapter, must be rejected with an `AdapterError`. |

---

## Conformance Manifest Format (`conformance.json`)

Each adapter package or test fixture directory must include a `conformance.json` manifest:

```json
{
  "schema": 1,
  "network_id": "wallet",
  "options": {
    "network_stamp_exempt": false,
    "network_stamp_exempt_reason": null,
    "profile_consistency_exempt": false,
    "profile_consistency_exempt_reason": null,
    "timing_ceiling_ms": 250.0,
    "mutation_cap_per_case": 1500,
    "thread_count": 8
  },
  "cases": [
    {
      "name": "p2p_send_completed",
      "fixture": "p2p_send_completed.json",
      "payload_format": "json",
      "received_at": "2026-10-15T12:00:00Z",
      "expect": "ok",
      "group": "grp_p2p_send",
      "must_appear": ["W_TX_001"],
      "must_not_appear": ["supersecretpass123", "4111111111111111"],
      "notes": "Completed peer-to-peer send"
    },
    {
      "name": "error_missing_amount",
      "fixture": "error_missing_amount.json",
      "payload_format": "json",
      "received_at": "2026-10-15T12:00:00Z",
      "expect": "error",
      "error_type": "NormalizationError",
      "error_field": "amount",
      "must_not_appear": ["4111111111111111"]
    }
  ]
}
```

### Loading and Running Manifests Programmatically

```python
from ripple.adapters.testing import load_manifest, run_conformance
from ripple.adapters.wallet import WalletAdapter

# Load cases from manifest beside fixtures
manifest_path = "backend/tests/adapters/fixtures/wallet/conformance.json"
adapter = WalletAdapter()
cases, options = load_manifest(manifest_path)

# Execute all 21 checks
report = run_conformance(adapter, cases, options=options)

# Output results or raise assertion failure
report.assert_conformant()
print(report.to_markdown())
```

---

## Exemption Policy

1. **Exemptions are Strictly Constrained**:
   - Only the universal pass-through adapter (`universal`) may declare exemptions (`network_stamp_exempt`, `profile_consistency_exempt`).
   - Every exemption flag **must have a non-empty written reason**. If an exemption flag is `True` without an explanatory reason string, the manifest fails to load.
2. **Specialized Rails Cannot Claim Profile Exemptions**:
   - Dedicated rails (`upi`, `card`, `crypto`, `swift`, `wallet`) are bound to their declared profiles. If an adapter cannot satisfy its profile, the profile definition or adapter logic must be corrected.

---

## Findings: Defects Discovered in Earlier Modules (M02–M05)

During the development and initial execution of the Conformance Suite against existing modules, the suite uncovered five real defects. In accordance with project governance rules, each bug was resolved without changing domain schemas or profile contracts, and verified by regression tests:

### Finding 1: System Clock Reads in `Party.created_at` (CF-04, CF-07)
- **Component**: `backend/ripple/adapters/crypto/adapter.py`, `backend/ripple/adapters/swift/adapter.py`, `backend/ripple/adapters/universal/adapter.py`
- **Discovered by**: `CF-04` (Determinism) and `CF-07` (Time discipline)
- **Root Cause**: The crypto and SWIFT adapters omitted the `created_at` parameter when constructing `Party` instances. The underlying domain model `Party` defaults `created_at` to `datetime.now(UTC)`, which silently read the host operating system clock. This caused successive normalization calls of the identical event to yield non-deterministic party timestamps, violating the purity contract.
- **Resolution**: Explicitly pass `created_at=event.received_at` in all `Party` constructors. Updated `universal.py` to also use `event.received_at` when an explicit party creation timestamp is absent.

### Finding 2: In-Place Mutation of Caller Event Payload (CF-14)
- **Component**: `backend/ripple/adapters/card/adapter.py`
- **Discovered by**: `CF-14` (Input immutability)
- **Root Cause**: `CardAdapter.normalize()` directly executed `.pop("pan", None)` and popped sensitive CVV fields from `event.payload` in-place. If an upstream caller retained a reference to the incoming event payload dictionary, the caller's data was mutated during normalization.
- **Resolution**: Deep-copy the payload dictionary at the entry point of `CardAdapter.normalize()`, preserving the caller's dictionary intact while operating exclusively on private working state.

### Finding 3: Unhandled `TypeError` in `require()` Helper (CF-16, CF-19)
- **Component**: `backend/ripple/adapters/helpers.py`
- **Discovered by**: `CF-19` (Mutation robustness)
- **Root Cause**: The shared helper `require(payload, key)` checked `if key not in payload:`. When payload mutation fuzzing replaced an intermediate parent dictionary with a scalar (e.g. an integer or float), `key not in payload` raised an unhandled standard library `TypeError: argument of type 'int' is not iterable`, escaping the `AdapterError` family.
- **Resolution**: Added type validation in `require()` to ensure `payload` is an instance of `dict` before membership testing, raising a structured `NormalizationError` naming the field when the parent container is invalid.

### Finding 4: Unwrapped `RippleDomainError` (CF-16, CF-19)
- **Component**: `backend/ripple/adapters/helpers.py`
- **Discovered by**: `CF-19` (Mutation robustness)
- **Root Cause**: The `wrap_domain_errors()` context manager intercepted specific known subclasses (`InvalidMoneyError`, `InvalidIdentifierError`, `InvalidPartyError`, `InvalidTransactionError`) but did not catch the root base class `RippleDomainError`. When mutants triggered domain-level base errors (such as invalid ISO alpha-2 country codes), the raw exception escaped unhandled.
- **Resolution**: Added `RippleDomainError` to the caught exception tuple in `wrap_domain_errors()`, ensuring all domain errors are cleanly converted to `NormalizationError`.

### Finding 5: Integer and Float Parsing in Crypto and SWIFT (CF-12, CF-19)
- **Component**: `backend/ripple/adapters/crypto/adapter.py`, `backend/ripple/adapters/swift/adapter.py`
- **Discovered by**: `CF-12` (Money discipline) and `CF-19` (Mutation robustness)
- **Root Cause**: Numeric inputs such as `block_number`, `confirmations`, and `instructed_amount` were parsed using `int(...)` or accepted raw floats from fuzzing, which could either raise bare `ValueError` or leak floats into metadata.
- **Resolution**: Implemented safe integer and decimal parsing helpers that reject IEEE floats and raise typed `NormalizationError` naming the invalid field.
