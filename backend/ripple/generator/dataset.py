"""Dataset persistence, canonical JSONL/gzip serialization, and cryptographic verification.

Rule R1 & R3 Invariants:
- Deterministic serialization: sort_keys=True, compact separators=(',', ':'), ensure_ascii=True.
- GZIP archives created with mtime=0 for byte-identical reproducibility.
- events.jsonl.gz contains only RawEvents, never any ground-truth fields.
- truth.jsonl.gz contains only EventTruth records, joined 1:1 by sequence index.
- Path traversal rejection: dataset names must match ^[a-z0-9][a-z0-9_.-]{0,63}$.
"""

import gzip
import hashlib
import json
import re
import shutil
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from ripple import __version__ as GENERATOR_VERSION
from ripple.adapters.contract import RawEvent
from ripple.generator.calibration import CalibrationProfile, load_calibration_profile
from ripple.generator.config import ALGORITHM_VERSION, GeneratorConfig
from ripple.generator.engine import SourcePlanner, generate_stream
from ripple.generator.timeutil import iso_z
from ripple.generator.truth import EventTruth
from ripple.generator.world import build_world

# Strict dataset directory naming pattern rejecting path traversal
DATASET_NAME_REGEX = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")


class DatasetError(Exception):
    """Raised for dataset serialization, path traversal, or integrity check failures."""

    pass


class CanonicalEncoder(json.JSONEncoder):
    """JSON encoder rendering Decimals as exact strings, datetimes as ISO-8601 with 'Z',
    and UUIDs as strings.
    """

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return iso_z(o)
        if isinstance(o, UUID):
            return str(o)
        return super().default(o)


def canonical_dumps(obj: Any) -> str:
    """Serialize object to deterministic canonical JSON string."""
    return json.dumps(
        obj,
        cls=CanonicalEncoder,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def compute_file_sha256(filepath: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_dataset_checksum(file_checksums: dict[str, str]) -> str:
    """Compute overall dataset checksum as hash of sorted file checksums."""
    lines = [f"{fname}:{file_checksums[fname]}" for fname in sorted(file_checksums.keys())]
    composite = "\n".join(lines) + "\n"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


def validate_dataset_name(name: str) -> None:
    """Validate that dataset name is safe and does not contain path traversal elements."""
    if (
        not name
        or not DATASET_NAME_REGEX.match(name)
        or ".." in name
        or "/" in name
        or "\\" in name
    ):
        raise DatasetError(
            f"Invalid dataset name '{name}'. Must match ^[a-z0-9][a-z0-9_.-]{{0,63}}$ "
            "and contain no path traversal characters."
        )


def write_dataset(
    config: GeneratorConfig,
    name: str,
    base_dir: Path | None = None,
    overwrite: bool = False,
    calibration_profile: CalibrationProfile | None = None,
    sources: list[SourcePlanner] | None = None,
    strict: bool | None = None,
) -> Path:
    """Orchestrate full dataset generation and atomic directory writing."""
    validate_dataset_name(name)
    if strict is None:
        strict = sources is None

    target_base = base_dir or Path("./data")
    datasets_dir = target_base / "datasets"
    datasets_dir.mkdir(parents=True, exist_ok=True)
    target_dir = datasets_dir / name

    if target_dir.exists():
        if not overwrite:
            raise DatasetError(
                f"Dataset directory '{target_dir}' already exists. Pass overwrite=True to replace."
            )
        shutil.rmtree(target_dir)

    # Use staging temporary directory for atomic creation
    tmp_dir = datasets_dir / f".tmp_{name}_{int(datetime.now().timestamp())}"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        calib = calibration_profile or load_calibration_profile(config.calibration)
        world = build_world(config, calib)

        # 1. Write institutions.json
        institutions_path = tmp_dir / "institutions.json"
        inst_data = [inst.model_dump(mode="json") for inst in world.institutions]
        with open(institutions_path, "w", encoding="utf-8") as f:
            f.write(canonical_dumps(inst_data))

        # 2. Write actors.jsonl.gz (deterministic mtime=0)
        actors_path = tmp_dir / "actors.jsonl.gz"
        with open(actors_path, "wb") as f_raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=f_raw, mtime=0) as f_gz:
                for actor in world.actors:
                    line = canonical_dumps(actor.model_dump(mode="json")) + "\n"
                    f_gz.write(line.encode("utf-8"))

        # 3. Stream and write events.jsonl.gz, truth.jsonl.gz, and party_map.jsonl.gz
        events_path = tmp_dir / "events.jsonl.gz"
        truth_path = tmp_dir / "truth.jsonl.gz"
        party_map_path = tmp_dir / "party_map.jsonl.gz"

        total_events = 0
        counts_by_network: dict[str, int] = {}
        fraud_events = 0
        hard_negative_events = 0
        unexpected_failures: list[dict[str, str]] = []

        # Load adapters for validation
        from ripple.adapters.registry import get_registry, load_enabled_adapters

        adapter_registry = get_registry()
        load_enabled_adapters(registry=adapter_registry)

        party_map: dict[tuple[str, str], str] = {}

        with (
            open(events_path, "wb") as ev_raw,
            open(truth_path, "wb") as tr_raw,
            open(party_map_path, "wb") as pm_raw,
        ):
            with (
                gzip.GzipFile(filename="", mode="wb", fileobj=ev_raw, mtime=0) as ev_gz,
                gzip.GzipFile(filename="", mode="wb", fileobj=tr_raw, mtime=0) as tr_gz,
                gzip.GzipFile(filename="", mode="wb", fileobj=pm_raw, mtime=0) as pm_gz,
            ):
                for gen_event in generate_stream(
                    config, calibration_profile=calib, sources=sources
                ):
                    total_events += 1
                    net = gen_event.truth.network_id
                    counts_by_network[net] = counts_by_network.get(net, 0) + 1

                    if gen_event.truth.is_fraud:
                        fraud_events += 1
                    if gen_event.truth.hard_negative:
                        hard_negative_events += 1

                    # Adapter normalization validation
                    adapter = adapter_registry.get(net)
                    if adapter is not None:
                        try:
                            res = adapter.normalize(gen_event.event)
                            for party in res.parties:
                                # Map parties to actors
                                if gen_event.truth.sender_actor_id:
                                    party_map[(gen_event.truth.sender_actor_id, net)] = (
                                        party.party_id
                                    )
                                if gen_event.truth.receiver_actor_id:
                                    party_map[(gen_event.truth.receiver_actor_id, net)] = (
                                        party.party_id
                                    )
                        except Exception as exc:
                            if not gen_event.truth.expected_error:
                                unexpected_failures.append(
                                    {
                                        "event_id": gen_event.event.event_id,
                                        "network": net,
                                        "error_type": exc.__class__.__name__,
                                    }
                                )
                                if strict and len(unexpected_failures) >= 5:
                                    break

                    ev_line = canonical_dumps(gen_event.event.model_dump(mode="json")) + "\n"
                    ev_gz.write(ev_line.encode("utf-8"))

                    tr_data = gen_event.truth.model_dump(mode="json")
                    tr_data["seq"] = gen_event.seq
                    tr_line = canonical_dumps(tr_data) + "\n"
                    tr_gz.write(tr_line.encode("utf-8"))

                if strict and unexpected_failures:
                    raise DatasetError(
                        "Unexpected adapter normalization failures occurred "
                        f"(first 5): {unexpected_failures[:5]}"
                    )

                # Write sorted party_map
                for (a_id, a_net), p_id in sorted(party_map.items()):
                    pm_line = (
                        canonical_dumps({"actor_id": a_id, "network": a_net, "party_id": p_id})
                        + "\n"
                    )
                    pm_gz.write(pm_line.encode("utf-8"))

        # 4. Compute file checksums
        file_checksums: dict[str, str] = {
            "actors.jsonl.gz": compute_file_sha256(actors_path),
            "events.jsonl.gz": compute_file_sha256(events_path),
            "institutions.json": compute_file_sha256(institutions_path),
            "party_map.jsonl.gz": compute_file_sha256(party_map_path),
            "truth.jsonl.gz": compute_file_sha256(truth_path),
        }
        dataset_checksum = compute_dataset_checksum(file_checksums)

        # 5. Write manifest.json
        manifest_data: dict[str, Any] = {
            "algorithm_version": ALGORITHM_VERSION,
            "generator_version": GENERATOR_VERSION,
            "dataset_name": name,
            "created_at": iso_z(config.start_time),
            "config": json.loads(config.canonical_json()),
            "config_hash": config.config_hash(),
            "calibration_name": calib.name,
            "calibration_hash": calib.profile_hash(),
            "provenance_summary": calib.summary(),
            "counts": {
                "total_events": total_events,
                "by_network": counts_by_network,
                "fraud_events": fraud_events,
                "hard_negative_events": hard_negative_events,
                "mapped_parties": len(party_map),
            },
            "truncated_count": 0,
            "resolved_warnings": config.resolved_warnings,
            "file_checksums": file_checksums,
            "dataset_checksum": dataset_checksum,
        }

        manifest_path = tmp_dir / "manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            f.write(canonical_dumps(manifest_data))

        # Atomic rename
        tmp_dir.rename(target_dir)
        return target_dir

    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)


def read_events(path: Path) -> Iterator[RawEvent]:
    """Read stream of RawEvents from events.jsonl.gz."""
    events_path = path / "events.jsonl.gz" if path.is_dir() else path
    with gzip.open(events_path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                yield RawEvent(**data)


def read_truth(path: Path) -> Iterator[EventTruth]:
    """Read stream of EventTruth records from truth.jsonl.gz."""
    truth_path = path / "truth.jsonl.gz" if path.is_dir() else path
    with gzip.open(truth_path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                data = json.loads(line)
                yield EventTruth(**data)


def iter_joined(path: Path) -> Iterator[tuple[RawEvent, EventTruth]]:
    """Yield synchronized pairs of (RawEvent, EventTruth) joined by sequence index."""
    ev_iter = read_events(path)
    tr_iter = read_truth(path)
    yield from zip(ev_iter, tr_iter, strict=True)


def verify_dataset(path: Path) -> tuple[bool, list[str]]:
    """Verify dataset integrity by recomputing checksums against manifest.json."""
    errors: list[str] = []
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        return False, ["manifest.json missing"]

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as exc:
        return False, [f"Failed to parse manifest.json: {exc}"]

    # 1. Algorithm version check
    if manifest.get("algorithm_version") != ALGORITHM_VERSION:
        errors.append(
            f"Algorithm version mismatch: manifest={manifest.get('algorithm_version')}, "
            f"engine={ALGORITHM_VERSION}"
        )

    # 2. File checksums check
    recorded_checksums = manifest.get("file_checksums", {})
    recomputed_checksums: dict[str, str] = {}

    for fname, expected_hash in recorded_checksums.items():
        fpath = path / fname
        if not fpath.exists():
            errors.append(f"Expected file '{fname}' is missing from dataset")
            continue

        actual_hash = compute_file_sha256(fpath)
        recomputed_checksums[fname] = actual_hash
        if actual_hash != expected_hash:
            errors.append(
                f"Checksum mismatch for '{fname}': expected={expected_hash}, actual={actual_hash}"
            )

    # 3. Dataset checksum check
    expected_dataset_checksum = manifest.get("dataset_checksum")
    actual_dataset_checksum = compute_dataset_checksum(recomputed_checksums)
    if expected_dataset_checksum != actual_dataset_checksum:
        errors.append(
            f"Dataset checksum mismatch: expected={expected_dataset_checksum}, "
            f"actual={actual_dataset_checksum}"
        )

    return len(errors) == 0, errors


def read_party_map(path: Path) -> dict[str, str]:
    """Read actor_id to party_id mapping from party_map.jsonl.gz.

    Returns mapping actor_id -> party_id.
    """
    map_path = path / "party_map.jsonl.gz" if path.is_dir() else path
    mapping: dict[str, str] = {}
    if not map_path.exists():
        return mapping

    with gzip.open(map_path, "rt", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                mapping[row["actor_id"]] = row["party_id"]
    return mapping


generate_dataset = write_dataset
