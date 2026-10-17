# Ripple Synthetic Generator: Architecture & Design

This document details the architectural principles, guarantees, event pipeline, and rules governing `ripple.generator` — the synthetic transaction generator and simulated economic world engine for Ripple.

---

## Core Invariant Rules (R1 - R6)

The following six rules are non-negotiable architectural constraints enforced across all generator components and tests:

### R1 Determinism
Same `GeneratorConfig` + same `ALGORITHM_VERSION` => byte-identical dataset files on the same Python version and platform (cross-platform is best-effort; document it). All randomness comes from named substreams of the master seed. Forbidden anywhere in `ripple/generator`: wall-clock reads (`datetime.now`/`utcnow`/`today`, `time.time`; `perf_counter` only inside benchmark helpers), `uuid1`/`uuid4`, builtin `hash()`, `os.environ` reads, iteration over sets, and the stdlib random methods `choice`/`shuffle`/`sample`/`randint`/`randrange`/`uniform`/`gauss`/`lognormvariate` and the like (their algorithms are not guaranteed stable across Python versions). Only `Random.random()` and `getrandbits()` may be used from the stdlib.

### R2 Synthetic-identifier safety
Nothing generated may plausibly identify a real person, account or card. Emails only on `example.com`/`example.org`/`example.net`. Names are placeholders (`"Synthetic Actor 000123"`; emitted on purpose so the adapters' discard paths are exercised). Phones are E.164-shaped under the non-routable prefix `+999`. UPI handles are fictional (`rplbank1`...), IFSC codes fictional (`"RPLX0"` + 6 digits). IBANs use fictional bank codes with correct mod-97 check digits and country-correct length. BICs use fictional institution codes (e.g. `ZRPA`, `ZRPB`) in valid BIC shape. Crypto addresses are random, and for bitcoin/bech32/base58 families deliberately checksum-INVALID so they can never be real. IPs only from documentation ranges (RFC 5737 IPv4 blocks, RFC 3849 `2001:db8::/32`). Card events carry only an opaque `card_token` (`tok_` + 32 hex) with synthetic bin/last4; never generate a PAN or any Luhn-valid 13-19 digit string.

### R3 Truth is a sidecar
Ground truth (fraud flags, scenario ids, roles, regimes) never appears in any event payload and is stored in separate files.

### R4 Honesty
Every calibration parameter has provenance `"assumption"` unless a human supplies a citation. Do not invent statistics, publications or source names. Documentation states plainly that the data is synthetic and uncalibrated to real traffic.

### R5 Plans vs renderers
All random decisions are made when a `TxnPlan` is built. Renderers are pure functions `(plan, stage index) -> payload dict` with no randomness and no clock. Plans are created eagerly (a scenario plans its whole chain at its start time); there are no callbacks.

### R6 Money
Payload amounts are decimal strings. Floats may be used only for sampling; quantize with `Decimal` (`ROUND_HALF_EVEN`) to the currency scale (use `ripple.adapters.currencies`) before anything is stored or rendered.

---

## Architectural Architecture & Event Flow

The generator operates on a discrete virtual-time event loop modeled in 1-hour virtual ticks:

```
+-------------------------------------------------------------------+
|                        GeneratorConfig                            |
|             (seed, days, actors, networks, fraud, ...)            |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                       World Builder                               |
| - Institutions (fictional BICs, IFSCs, handles)                   |
| - Population (actors, per-network identities, mule pool)          |
| - Substream independence: "world/actor/<idx>/<rail>"              |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                      Engine Simulation                            |
| For each 1-hour virtual tick:                                     |
|   1. Query planners (BackgroundTraffic + FraudScenarios)          |
|   2. Generate eager TxnPlans (amounts, stages, counterparties)     |
|   3. Expand stages with received_at = event_time + latency + jitter|
|   4. Push to Priority Queue (received_at, source_key, seq)        |
|   5. Drain & yield events with received_at < end_of_tick          |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                   Renderer Pipeline (Pure)                        |
|   (plan, stage_index) -> native payload dict                      |
|   Feed quirks applied (deterministic mutations / timing quirks)   |
|   Emit GeneratedEvent(seq, event: RawEvent, truth: EventTruth)     |
+-------------------------------------------------------------------+
                                  |
                                  v
+-------------------------------------------------------------------+
|                      Dataset Storage                              |
|   events.jsonl.gz (RawEvents only, no truth)                      |
|   truth.jsonl.gz (sidecar truth keyed by seq)                     |
|   actors.jsonl.gz & institutions.json                             |
|   manifest.json (SHA-256 hashes, config, checksum)                |
+-------------------------------------------------------------------+
```

---

## Determinism Guarantees & Limits

1. **Substream Partitioning**:
   Randomness is isolated into hierarchically named streams:
   - `world/institutions`
   - `world/actor/<index>/<network>`
   - `contacts/<actor_id>`
   - `traffic/<network>/<timezone>/<tick>`
   - `sc/<scenario_instance_id>`

   Because identity generation for an actor evaluates all potential networks regardless of `config.networks`, toggling a network on or off never perturbs the RNG state for any other network or actor.

2. **Heap Ordering & Tie Breaking**:
   Event ordering in the min-heap is strictly keyed by `(received_at, source_key, per_source_sequence)`. There is no global event counter determining relative order between sources, ensuring that disabling or adding a background source does not affect the tie-breaking sequence of remaining sources.

3. **Platform Reproducibility**:
   Determinism is exact and byte-identical across runs on the same Python minor version and OS platform. Minor floating-point rounding discrepancies across different C runtimes or architectures (e.g. x86_64 vs aarch64) in transcendentals (`sin`, `cos`, `log` in Box-Muller) are documented as best-effort; exact byte reproducibility is guaranteed on pinned environments.

---

## ALGORITHM_VERSION Policy

The constant `ALGORITHM_VERSION` tracks the semantic behavior of the generator engine, world model, and scenario generation logic.
- Current version: `"1"`.
- Policy: Whenever any generator logic, RNG consumption sequence, or scenario planning algorithm changes such that the output for a given `GeneratorConfig` would alter even a single byte, `ALGORITHM_VERSION` **must be bumped**.
- Datasets include `algorithm_version` in their `manifest.json`. Verification checks will reject datasets generated under incompatible algorithm versions.
