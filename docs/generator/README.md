# Multi-Network Synthetic Transaction Generator

The Ripple Synthetic Transaction Generator is the data engine powering model training, benchmarking, and streaming simulation across all five supported payment networks (`card`, `crypto`, `swift`, `upi`, `wallet`).

It generates an interconnected simulated financial world, evaluates background organic transaction volume, injects hard-negative behavioral look-alikes, and superimposes 10 complex multi-hop financial crime typologies with realistic temporal delay and adversarial drift.

---

## Key Guarantees & Invariants

1. **Rule R1: Determinism**: Given identical `seed`, `days`, `actors`, and `GeneratorConfig`, generation is 100% bit-identical across runs and platforms. Zero reads from the wall clock or stdlib `random`/`uuid`.
2. **Rule R2: Synthetic Safety**: Non-routable identifiers throughout (`+999` telephone numbers, invalid Bitcoin Base58 checksums, valid mod-97 synthetic IBANs, and strictly **no** Luhn-valid 13–19 digit PAN strings).
3. **Rule R3: Sidecar Ground Truth**: Ground-truth labels are written to an isolated, separate `truth.jsonl.gz` sidecar file, keyed by sequence index. Raw events emitted to `events.jsonl.gz` never contain labels, scenario flags, or fraud tags.
4. **Rule R4: Calibration Honesty**: Every empirical parameter in calibration catalogs explicitly states its source. Any parameter not backed by an authoritative cited source is tagged with `provenance: "assumption"` and `citation: null`.
5. **Rule R5: Pure Renderers**: Payload renderers are pure functions with zero I/O and zero state.
6. **Rule R6: Financial Precision**: All balances, fees, and transfer amounts are calculated using Python's exact `Decimal` arithmetic. Floats are prohibited in monetary fields.

---

## Directory Layout of a Generated Dataset

A generated dataset directory contains 6 deterministic artifacts:

```text
data/datasets/<dataset_name>/
├── manifest.json         # SHA-256 checksums, config snapshot, dataset metadata
├── institutions.json     # Participating banks, PSPs, schemes, and crypto exchanges
├── actors.jsonl.gz       # Simulated population (individuals, merchants, corporations)
├── party_map.jsonl.gz    # Deterministic mapping: (actor_id, network) -> universal party_id
├── events.jsonl.gz       # Canonical raw payment payloads (zero labels)
└── truth.jsonl.gz        # Joined 1:1 by seq index: fraud flags, typologies, mule roles
```

---

## Quickstart: Command Line Interface (CLI)

The generator provides a command-line interface via `python -m ripple.generator`:

```bash
# 1. List available presets and scenario typologies
python -m ripple.generator scenarios

# 2. Generate a standard small dataset (~10k events, seed 42)
python -m ripple.generator generate --preset small --seed 42 --name small-42

# 3. Generate a tiny dataset for quick testing
python -m ripple.generator generate --preset tiny --seed 123 --name tiny-test --no-strict

# 4. Verify cryptographic SHA-256 integrity and event/truth alignment
python -m ripple.generator verify small-42

# 5. Generate a separability report and feature rank-AUC analysis
python -m ripple.generator report small-42 --markdown-out docs/reports/small-42-report.md
```

### Makefile Convenience Targets

```bash
# Generate sample benchmark dataset
make generate-sample

# Verify dataset integrity
make verify-sample
```

---

## Python API Usage

```python
from datetime import datetime, timezone
from ripple.generator.config import GeneratorConfig
from ripple.generator.dataset import generate_dataset, verify_dataset
from ripple.generator.engine import generate_stream
from ripple.generator.labels import transaction_labels_as_of, party_labels_as_of, temporal_split
from ripple.generator.report import analyze_dataset

# 1. Programmatic stream generation
config = GeneratorConfig(
    seed=42,
    actors=500,
    days=7,
    networks=["card", "crypto", "swift", "upi", "wallet"],
    fraud={"enabled": True, "intensity": "medium"},
    drift={"enabled": True, "regime": "v2_mule_template", "shift_day": 4.0},
)

for gen_event in generate_stream(config):
    event = gen_event.event    # RawEvent passed to network adapters
    truth = gen_event.truth    # Ground truth sidecar record
    print(f"[{event.received_at}] {event.network_id} - is_fraud={truth.is_fraud}")

# 2. Batch dataset generation
ds_path = generate_dataset(config, name="benchmark-7d", overwrite=True)

# 3. Integrity verification
valid, errors = verify_dataset(ds_path)
assert valid is True

# 4. Label resolution with realistic reporting delay
as_of_time = datetime(2026, 3, 5, 0, 0, tzinfo=timezone.utc)
effective_labels = transaction_labels_as_of(ds_path, as_of=as_of_time)

# 5. Statistical separability report
report = analyze_dataset(ds_path)
print(report.to_markdown())
```

---

## Configuration Presets

| Preset | Actors | Days | Networks | Approx. Events | Purpose |
|---|---|---|---|---|---|
| `tiny` | 50 | 1 | All 5 | ~500 | Fast unit tests and smoke tests |
| `small` | 1,000 | 7 | All 5 | ~25,000 | Local model development and integration tests |
| `medium` | 10,000 | 30 | All 5 | ~500,000 | Full GNN training and evaluation benchmark |
| `large` | 100,000 | 90 | All 5 | ~5,000,000 | Production stress testing and distributed evaluation |
