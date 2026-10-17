"""Tests for dataset persistence, integrity verification, determinism, and path traversal."""

import json
from pathlib import Path

import pytest

from ripple.generator.config import GeneratorConfig
from ripple.generator.dataset import (
    DatasetError,
    iter_joined,
    read_events,
    read_truth,
    verify_dataset,
    write_dataset,
)
from tests.generator.test_engine import DummyTestPlanner


def test_dataset_round_trip_and_verification(tmp_path: Path) -> None:
    """Assert written dataset matches manifest checksums and round-trips cleanly."""
    config = GeneratorConfig(seed=42, actors=50, days=1)
    planner = DummyTestPlanner(count_per_tick=1)

    ds_path = write_dataset(
        config=config,
        name="test_sample_01",
        base_dir=tmp_path,
        sources=[planner],
    )

    assert ds_path.exists()
    assert (ds_path / "events.jsonl.gz").exists()
    assert (ds_path / "truth.jsonl.gz").exists()
    assert (ds_path / "actors.jsonl.gz").exists()
    assert (ds_path / "institutions.json").exists()
    assert (ds_path / "manifest.json").exists()

    # 1. Verification succeeds
    valid, errors = verify_dataset(ds_path)
    assert valid is True
    assert errors == []

    # 2. Reading events and truth
    events = list(read_events(ds_path))
    truth = list(read_truth(ds_path))
    assert len(events) > 0
    assert len(events) == len(truth)

    joined = list(iter_joined(ds_path))
    assert len(joined) == len(events)
    for ev, tr in joined:
        assert ev.event_id == tr.event_id


def test_verification_detects_flipped_byte(tmp_path: Path) -> None:
    """Assert corrupting a single byte in events.jsonl.gz causes verification to fail."""
    config = GeneratorConfig(seed=42, actors=50, days=1)
    planner = DummyTestPlanner(count_per_tick=1)

    ds_path = write_dataset(
        config=config,
        name="test_corrupt",
        base_dir=tmp_path,
        sources=[planner],
    )

    # Corrupt one byte in events.jsonl.gz
    ev_file = ds_path / "events.jsonl.gz"
    content = bytearray(ev_file.read_bytes())
    # Flip lowest bit of middle byte
    mid = len(content) // 2
    content[mid] ^= 0x01
    ev_file.write_bytes(content)

    valid, errors = verify_dataset(ds_path)
    assert valid is False
    assert any("events.jsonl.gz" in err for err in errors)


def test_byte_identical_reproducibility(tmp_path: Path) -> None:
    """Rule R1 Invariant: identical config produces identical files and checksums."""
    config = GeneratorConfig(seed=1234, actors=50, days=1)

    planner1 = DummyTestPlanner(count_per_tick=1)
    dir1 = write_dataset(
        config=config,
        name="run_01",
        base_dir=tmp_path,
        sources=[planner1],
    )

    planner2 = DummyTestPlanner(count_per_tick=1)
    dir2 = write_dataset(
        config=config,
        name="run_02",
        base_dir=tmp_path,
        sources=[planner2],
    )

    # Read manifests
    m1 = json.loads((dir1 / "manifest.json").read_text(encoding="utf-8"))
    m2 = json.loads((dir2 / "manifest.json").read_text(encoding="utf-8"))

    assert m1["dataset_checksum"] == m2["dataset_checksum"]
    assert m1["file_checksums"] == m2["file_checksums"]

    # Verify exact byte equality of compressed files
    for fname in ["events.jsonl.gz", "truth.jsonl.gz", "actors.jsonl.gz", "institutions.json"]:
        bytes1 = (dir1 / fname).read_bytes()
        bytes2 = (dir2 / fname).read_bytes()
        assert bytes1 == bytes2, f"File {fname} is not byte-identical across runs"


def test_path_traversal_rejection(tmp_path: Path) -> None:
    """Assert path traversal attempts are strictly rejected."""
    config = GeneratorConfig(seed=42, actors=50, days=1)

    with pytest.raises(DatasetError, match="path traversal"):
        write_dataset(config=config, name="../evil_path", base_dir=tmp_path)

    with pytest.raises(DatasetError, match="path traversal"):
        write_dataset(config=config, name="foo/bar", base_dir=tmp_path)

    with pytest.raises(DatasetError, match="path traversal"):
        write_dataset(config=config, name="foo\\bar", base_dir=tmp_path)
