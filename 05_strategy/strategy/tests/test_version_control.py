"""Foundation version-control test suite — 22 checks + integration flow.

Covers immutability, duplicate protection, canonical identity,
traceability, lineage, evidence, governance, snapshot safety,
rename/duplicate, historical restore, deterministic identity,
graph integrity, and replay compatibility.

All tests exercise actual storage (filesystem) and reload via fresh
objects to prove persistence beyond construction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC
from pathlib import Path

import pytest

from strategy.language import compile_to_ir
from strategy.language.storage import (
    create_strategy,
    duplicate_strategy,
    get_strategy_by_id,
    load_strategy_record,
    rename_strategy,
)
from strategy.research.dataset import ResearchDataset
from strategy.research.evidence import create_evidence, save_evidence
from strategy.research.governance import create_decision, save_decision
from strategy.research.lineage import load_lineage
from strategy.version import (
    DuplicateVersionError,
    StrategyVersion,
    VersionGraphError,
    VersionImmutableError,
    create_version,
    list_versions,
    load_version,
    restore_version_source,
    save_version,
    validate_graph,
    verify_version_ir,
)

# ── helpers ─────────────────────────────────────────────────────────────

SOURCE_A = """strategy("TestStrategy")
thresh = input(10, "Threshold")
if close > thresh:
    buy()
"""

SOURCE_B = """strategy("TestStrategy")
thresh = input(20, "Threshold")
if close < thresh:
    sell()
"""

SOURCE_TAMPER = """strategy("TestStrategy")
thresh = input(99, "Threshold")
if close > thresh:
    buy()
"""


def _ir_and_hash(source: str) -> tuple[str | None, str, int, dict[str, float]]:
    """Compile source to IR snapshot, hash, version, params (generic)."""
    try:
        ir = compile_to_ir(source)
        snap = ir.to_json()
        h = hashlib.sha256(snap.encode("utf-8")).hexdigest()
        params = {p.label: float(p.default) for p in ir.parameters}
        return snap, h, ir.ir_version, params
    except Exception:
        h = hashlib.sha256(source.encode("utf-8")).hexdigest()
        return None, h, 1, {}


def _make_strategy(tmp_path: Path, name: str = "TestStrategy", code: str = SOURCE_A):
    """Create a library strategy with stable UUID in tmp_path."""
    return create_strategy(name, code, data_dir=tmp_path)


def _bars(count: int = 30):
    """Deterministic Bar tuples for replay tests."""
    from datetime import datetime, timedelta

    from market.models.bar import Bar

    base = datetime(2026, 1, 1, 9, 15, tzinfo=UTC)
    out = []
    for i in range(count):
        ts = (base + timedelta(minutes=15 * i)).strftime("%Y-%m-%d %H:%M:%S")
        out.append(
            Bar(
                symbol="TEST",
                open=100 + i,
                high=102 + i,
                low=99 + i,
                close=101 + i,
                volume=1000,
                timestamp=ts,
            )
        )
    return tuple(out)


# ── 1. V1 creation ──────────────────────────────────────────────────────


def test_v1_creation(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_V1", SOURCE_A)
    snap, h, ver, params = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=ver,
        ir_hash=h,
        ir_snapshot=snap,
        parameters=params,
        data_dir=tmp_path,
    )
    assert v1.version_id
    assert v1.parent_version_id is None
    assert v1.strategy_id == rec.id
    assert v1.source == SOURCE_A
    assert (
        v1.source_hash
        == hashlib.sha256(
            "\n".join(l.rstrip() for l in SOURCE_A.strip().splitlines()).encode()  # noqa: E741
        ).hexdigest()
    )
    assert v1.ir_hash == h
    # persistence: fresh load
    loaded = load_version(rec.id, v1.version_id, data_dir=tmp_path)
    assert loaded is not None
    assert loaded.source == SOURCE_A
    assert loaded.version_id == v1.version_id


# ── 2. V2 creation ──────────────────────────────────────────────────────


def test_v2_creation(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_V2", SOURCE_A)
    snap_a, h_a, ver_a, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=ver_a,
        ir_hash=h_a,
        ir_snapshot=snap_a,
        parameters=pa,
        data_dir=tmp_path,
    )
    snap_b, h_b, ver_b, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=ver_b,
        ir_hash=h_b,
        ir_snapshot=snap_b,
        parameters=pb,
        data_dir=tmp_path,
    )
    assert v2.parent_version_id == v1.version_id
    assert v2.version_id != v1.version_id
    assert v2.source != v1.source
    versions = list_versions(rec.id, data_dir=tmp_path)
    assert len(versions) == 2
    assert versions[0].version_id == v1.version_id
    assert versions[1].version_id == v2.version_id


# ── 3. V1 immutable after V2 ────────────────────────────────────────────


def test_v1_immutable_after_v2(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_V1_IMM", SOURCE_A)
    snap_a, h_a, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=h_a,
        ir_snapshot=snap_a,
        parameters=pa,
        data_dir=tmp_path,
    )
    v1_json_before = v1.to_json()
    v1_source_before = v1.source
    snap_b, h_b, vb, pb = _ir_and_hash(SOURCE_B)
    create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=h_b,
        ir_snapshot=snap_b,
        parameters=pb,
        data_dir=tmp_path,
    )
    # reload V1 — must be unchanged
    reloaded = load_version(rec.id, v1.version_id, data_dir=tmp_path)
    assert reloaded is not None
    assert reloaded.to_json() == v1_json_before
    assert reloaded.source == v1_source_before
    assert reloaded.source_hash == v1.source_hash


# ── 4. V2 immutable ────────────────────────────────────────────────────


def test_v2_immutable(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_V2_IMM", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    # Attempt to overwrite V2 with different source via save_version must raise
    tampered = StrategyVersion(
        strategy_id=v2.strategy_id,
        version_id=v2.version_id,
        parent_version_id=v2.parent_version_id,
        source=SOURCE_TAMPER,
        source_hash=hashlib.sha256(SOURCE_TAMPER.encode()).hexdigest(),
        ir_hash=v2.ir_hash,
        ir_version=v2.ir_version,
        created_at=v2.created_at,
        metadata=v2.metadata,
        ir_snapshot=v2.ir_snapshot,
        parameters=v2.parameters,
    )
    with pytest.raises(VersionImmutableError):
        save_version(tampered, data_dir=tmp_path)
    # Original still intact
    reloaded = load_version(rec.id, v2.version_id, data_dir=tmp_path)
    assert reloaded is not None
    assert reloaded.source == SOURCE_B


# ── 5. duplicate save protection ────────────────────────────────────────


def test_duplicate_save_protection(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_DUP", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    # Saving source A again (same as V1) should NOT create V3 — raise DuplicateVersionError
    with pytest.raises(DuplicateVersionError) as exc:
        create_version(
            rec.id,
            SOURCE_A,
            ir_version=va,
            ir_hash=ha,
            ir_snapshot=sa,
            parameters=pa,
            data_dir=tmp_path,
        )
    assert exc.value.existing_version_id is not None
    # Only 2 versions exist, not 3
    assert len(list_versions(rec.id, data_dir=tmp_path)) == 2
    # Explicit allow_duplicate does create (branch)
    v_dup = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
        allow_duplicate=True,
        allow_branch=True,
    )
    assert v_dup.version_id != v1.version_id
    assert len(list_versions(rec.id, data_dir=tmp_path)) == 3


# ── 6. source snapshot recovery ────────────────────────────────────────


def test_source_snapshot_recovery(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_SNAP", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # File must contain full source, not just hash
    fpath = tmp_path / "strategy_versions" / rec.id / f"{v1.version_id}.json"
    raw = json.loads(fpath.read_text(encoding="utf-8"))
    assert "source" in raw
    assert raw["source"] == SOURCE_A
    # Restore helper returns exact source
    restored = restore_version_source(rec.id, v1.version_id, data_dir=tmp_path)
    assert restored == SOURCE_A
    # Fresh object after "restart": load again
    reloaded = load_version(rec.id, v1.version_id, data_dir=tmp_path)
    assert reloaded is not None
    assert reloaded.source == SOURCE_A


# ── 7. source_hash correctness ──────────────────────────────────────────


def test_source_hash_correctness(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_SHASH", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    canonical = "\n".join(l.rstrip() for l in SOURCE_A.strip().splitlines())  # noqa: E741
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert v1.source_hash == expected
    # Also computed via helper must match
    assert restore_version_source(rec.id, v1.version_id, data_dir=tmp_path) == SOURCE_A
    assert hashlib.sha256(canonical.encode()).hexdigest() == expected
    assert len(expected) == 64  # full SHA-256


# ── 8. ir_hash correctness ──────────────────────────────────────────────


def test_ir_hash_correctness(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_IRH", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    if sa is not None:
        expected_ir = hashlib.sha256(sa.encode("utf-8")).hexdigest()
        assert v1.ir_hash == expected_ir
        assert len(v1.ir_hash) == 64
        assert verify_version_ir(rec.id, v1.version_id, data_dir=tmp_path) is True
    else:
        assert v1.ir_hash == hashlib.sha256(SOURCE_A.encode()).hexdigest()


# ── 9. parent relationship ──────────────────────────────────────────────


def test_parent_relationship(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_PARENT", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    assert v1.parent_version_id is None
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    assert v2.parent_version_id == v1.version_id
    sb2, hb2, vb2, pb2 = _ir_and_hash(SOURCE_TAMPER)
    v3 = create_version(
        rec.id,
        SOURCE_TAMPER,
        ir_version=vb2,
        ir_hash=hb2,
        ir_snapshot=sb2,
        parameters=pb2,
        data_dir=tmp_path,
    )
    assert v3.parent_version_id == v2.version_id
    # Graph validation passes
    errs = validate_graph(rec.id, data_dir=tmp_path)
    assert errs == []


# ── 10. self-parent rejection ───────────────────────────────────────────


def test_self_parent_rejection(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_SELF", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    # Try to create version where parent == new version's own id (simulate by passing self)
    # Since new_version_id is generated inside create_version, we bypass by directly testing validate_new_version  # noqa: E501
    fake_id = "self-id-123"
    with pytest.raises(VersionGraphError):
        from strategy.version import validate_new_version

        validate_new_version(rec.id, fake_id, data_dir=tmp_path, new_version_id=fake_id)
    # Also ensure existing chain does not allow self-parent via manual file
    # Direct save with self-parent should be detectable via validate_graph
    bad = StrategyVersion(
        strategy_id=rec.id,
        version_id="bad-self",
        parent_version_id="bad-self",
        source=SOURCE_B,
        source_hash=hashlib.sha256(SOURCE_B.encode()).hexdigest(),
        ir_hash=hb,
        ir_version=vb,
        created_at="2026-01-01T00:00:00+00:00",
        metadata={},
        ir_snapshot=sb,
        parameters=pb,
    )
    # Force write (bypass immutability for test)
    p = tmp_path / "strategy_versions" / rec.id / "bad-self.json"
    p.write_text(bad.to_json(), encoding="utf-8")
    errs = validate_graph(rec.id, data_dir=tmp_path)
    assert any("self-parent" in e for e in errs)


# ── 11. cross-strategy parent rejection ────────────────────────────────


def test_cross_strategy_parent_rejection(tmp_path: Path):
    rec_a = _make_strategy(tmp_path, "VC_CROSS_A", SOURCE_A)
    rec_b = _make_strategy(tmp_path, "VC_CROSS_B", SOURCE_B)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v_a1 = create_version(
        rec_a.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    create_version(
        rec_b.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    # Try to create V for B with parent from A — must fail
    tamper_src = SOURCE_TAMPER
    st, ht, vt, pt = _ir_and_hash(tamper_src)
    with pytest.raises(VersionGraphError) as exc:
        create_version(
            rec_b.id,
            tamper_src,
            ir_version=vt,
            ir_hash=ht,
            ir_snapshot=st,
            parameters=pt,
            parent_version_id=v_a1.version_id,
            data_dir=tmp_path,
        )
    assert "foreign" in str(exc.value).lower() or "not the latest" in str(exc.value).lower()
    # Also test missing parent
    with pytest.raises(VersionGraphError):
        create_version(
            rec_a.id,
            tamper_src,
            ir_version=vt,
            ir_hash=ht,
            ir_snapshot=st,
            parameters=pt,
            parent_version_id="nonexistent-123",
            data_dir=tmp_path,
        )


# ── 12. strategy rename preserves ID ───────────────────────────────────


def test_strategy_rename_preserves_id(tmp_path: Path):
    rec = _make_strategy(tmp_path, "RenameMe", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    old_id = rec.id
    old_versions = list_versions(old_id, data_dir=tmp_path)
    assert len(old_versions) == 1
    # Rename file (display label changes, UUID stays)
    rename_strategy("RenameMe", "Renamed", data_dir=tmp_path)
    rec2 = load_strategy_record("Renamed", data_dir=tmp_path)
    assert rec2 is not None
    assert rec2.id == old_id
    # Version history still under same UUID dir, not moved
    versions_after = list_versions(old_id, data_dir=tmp_path)
    assert len(versions_after) == 1
    assert versions_after[0].version_id == v1.version_id
    assert versions_after[0].source == SOURCE_A
    # Old name no longer exists but id lookup still works
    assert get_strategy_by_id(old_id, data_dir=tmp_path) is not None
    assert get_strategy_by_id(old_id, data_dir=tmp_path).name == "Renamed"


# ── 13. duplicate strategy gets new ID ─────────────────────────────────


def test_duplicate_strategy_gets_new_id(tmp_path: Path):
    rec = _make_strategy(tmp_path, "DupOrig", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # Duplicate via storage
    duplicate_strategy("DupOrig", "DupCopy", data_dir=tmp_path)
    rec_dup = load_strategy_record("DupCopy", data_dir=tmp_path)
    assert rec_dup is not None
    assert rec_dup.id != rec.id
    assert rec_dup.code == rec.code
    # Version graph independent: dup has no versions yet (empty), original still 1
    assert len(list_versions(rec.id, data_dir=tmp_path)) == 1
    assert len(list_versions(rec_dup.id, data_dir=tmp_path)) == 0
    # Creating a version for duplicate does not affect original graph
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v_dup = create_version(
        rec_dup.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    assert v_dup.strategy_id == rec_dup.id
    assert len(list_versions(rec_dup.id, data_dir=tmp_path)) == 1
    assert len(list_versions(rec.id, data_dir=tmp_path)) == 1
    # Ensure not sharing file path (different dir)
    orig_file = tmp_path / "strategy_versions" / rec.id / f"{v1.version_id}.json"
    dup_file = tmp_path / "strategy_versions" / rec_dup.id / f"{v_dup.version_id}.json"
    assert orig_file.exists()
    assert dup_file.exists()
    assert orig_file != dup_file


# ── 14. execution preserves version_id ─────────────────────────────────


def test_execution_preserves_version_id(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_EXEC", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # Create execution snapshot referencing exact version
    from backtest.execution import create_snapshot, load_history, save_history
    from backtest.models.config import BacktestConfig

    from strategy.language import compile_to_ir

    ir = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    snap = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir, pa, cfg, data_dir=tmp_path)
    assert snap.strategy_id == rec.id
    assert snap.version_id == v1.version_id
    assert snap.source_hash == v1.source_hash
    assert snap.ir_hash == v1.ir_hash
    # Save history
    from backtest.execution import ExecutionHistory

    history = ExecutionHistory(snapshot=snap, events=[], signals=[])
    save_history(history, data_dir=tmp_path)
    # Fresh load proves persistence
    loaded = load_history(snap.execution_id, data_dir=tmp_path)
    assert loaded is not None
    assert loaded.snapshot.version_id == v1.version_id
    assert loaded.snapshot.strategy_id == rec.id
    # Create V2 and second execution; first must still point to V1
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    ir2 = compile_to_ir(SOURCE_B)
    snap2 = create_snapshot(rec.id, v2.version_id, v2.source_hash, ir2, pb, cfg, data_dir=tmp_path)
    assert snap2.version_id == v2.version_id
    assert snap2.version_id != v1.version_id
    # Reload first execution — still V1
    loaded1 = load_history(snap.execution_id, data_dir=tmp_path)
    assert loaded1.snapshot.version_id == v1.version_id


# ── 15. research preserves version_id ──────────────────────────────────


def test_research_preserves_version_id(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_RESEARCH", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    from backtest.execution import ExecutionHistory, create_snapshot
    from backtest.models.config import BacktestConfig

    ir = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    snap = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir, pa, cfg, data_dir=tmp_path)
    history = ExecutionHistory(snapshot=snap, events=[], signals=[{"index": 1}])
    # Research dataset must preserve same ids
    ds = ResearchDataset.from_histories(rec.id, v1.version_id, [history], parameters=pa)
    assert ds.strategy_id == rec.id
    assert ds.version_id == v1.version_id
    assert ds.execution_ids == (snap.execution_id,)
    # Experiment
    from strategy.research.experiment import create_experiment
    from strategy.research.storage import save_experiment

    exp = create_experiment(
        rec.id, v1.version_id, [snap.execution_id], hypothesis_text="test hypothesis"
    )
    assert exp.strategy_id == rec.id
    assert exp.version_id == v1.version_id
    save_experiment(exp, data_dir=tmp_path)
    # Verify persistence after reload
    from strategy.research.storage import load_experiment

    loaded_exp = load_experiment(exp.experiment_id, data_dir=tmp_path)
    assert loaded_exp.strategy_id == rec.id
    assert loaded_exp.version_id == v1.version_id
    # Second version must not alter first research
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    snap2 = create_snapshot(
        rec.id, v2.version_id, v2.source_hash, compile_to_ir(SOURCE_B), pb, cfg, data_dir=tmp_path
    )
    history2 = ExecutionHistory(snapshot=snap2, events=[], signals=[])
    ds2 = ResearchDataset.from_histories(rec.id, v2.version_id, [history2], parameters=pb)
    assert ds2.version_id == v2.version_id
    assert ds.version_id == v1.version_id


# ── 16. evidence preserves version_id ──────────────────────────────────


def test_evidence_preserves_version_id(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_EVID", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # Execution for evidence source
    from backtest.execution import create_snapshot
    from backtest.models.config import BacktestConfig

    ir = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    snap = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir, pa, cfg, data_dir=tmp_path)
    ev = create_evidence(
        rec.id,
        v1.version_id,
        source_type="execution",
        source_id=snap.execution_id,
        metric="expectancy",
        value=0.5,
        context={"bars": 100},
    )
    assert ev.strategy_id == rec.id
    assert ev.version_id == v1.version_id
    assert len(ev.hash) == 64  # full SHA-256
    save_evidence(ev, data_dir=tmp_path)
    # Fresh load
    from strategy.research.evidence import list_evidence, load_evidence

    loaded = load_evidence(ev.evidence_id, data_dir=tmp_path)
    assert loaded is not None
    assert loaded.version_id == v1.version_id
    assert loaded.strategy_id == rec.id
    # V2 evidence independent
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    snap2 = create_snapshot(
        rec.id, v2.version_id, v2.source_hash, compile_to_ir(SOURCE_B), pb, cfg, data_dir=tmp_path
    )
    ev2 = create_evidence(
        rec.id,
        v2.version_id,
        source_type="execution",
        source_id=snap2.execution_id,
        metric="expectancy",
        value=0.8,
    )
    save_evidence(ev2, data_dir=tmp_path)
    evs = list_evidence(data_dir=tmp_path)
    assert len([e for e in evs if e.version_id == v1.version_id]) == 1
    assert len([e for e in evs if e.version_id == v2.version_id]) == 1


# ── 17. lineage forward traversal ───────────────────────────────────────


def test_lineage_forward_traversal(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_LFWD", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # After V1, lineage should have STRATEGY -> VERSION
    g = load_lineage(data_dir=tmp_path)
    fwd = g.forward("STRATEGY", rec.id)
    assert ("VERSION", v1.version_id) in fwd
    # Create execution -> lineage
    from backtest.execution import ExecutionHistory, create_snapshot
    from backtest.models.config import BacktestConfig

    ir = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    snap = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir, pa, cfg, data_dir=tmp_path)
    history = ExecutionHistory(snapshot=snap, events=[], signals=[])
    from backtest.execution import save_history

    save_history(history, data_dir=tmp_path)
    g2 = load_lineage(data_dir=tmp_path)
    # Forward from STRATEGY should reach EXECUTION via VERSION
    all_desc = g2.trace_forward("STRATEGY", rec.id)
    assert ("VERSION", v1.version_id) in all_desc
    assert ("EXECUTION", snap.execution_id) in all_desc
    # Forward from VERSION should reach EXECUTION
    assert ("EXECUTION", snap.execution_id) in g2.forward("VERSION", v1.version_id)
    # Create evidence -> should be reachable
    ev = create_evidence(
        rec.id,
        v1.version_id,
        source_type="execution",
        source_id=snap.execution_id,
        metric="m",
        value=1,
    )
    save_evidence(ev, data_dir=tmp_path)
    g3 = load_lineage(data_dir=tmp_path)
    all_desc2 = g3.trace_forward("STRATEGY", rec.id)
    assert ("EVIDENCE", ev.evidence_id) in all_desc2


# ── 18. lineage backward traversal ──────────────────────────────────────


def test_lineage_backward_traversal(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_LBWD", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    from backtest.execution import ExecutionHistory, create_snapshot, save_history
    from backtest.models.config import BacktestConfig

    ir = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    snap = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir, pa, cfg, data_dir=tmp_path)
    save_history(ExecutionHistory(snapshot=snap, events=[], signals=[]), data_dir=tmp_path)
    ev = create_evidence(
        rec.id,
        v1.version_id,
        source_type="execution",
        source_id=snap.execution_id,
        metric="m",
        value=1,
    )
    save_evidence(ev, data_dir=tmp_path)
    from strategy.research.governance import create_decision, save_decision

    dec = create_decision(
        rec.id,
        v1.version_id,
        discovery_id="DISC-001",
        validation_id="VAL-001",
        evidence_ids=[ev.evidence_id],
        status="DRAFT",
        rationale="test",
    )
    save_decision(dec, data_dir=tmp_path)
    g = load_lineage(data_dir=tmp_path)
    # Backward from EVIDENCE should reach VERSION and STRATEGY
    back = g.trace_backward("EVIDENCE", ev.evidence_id)
    assert ("VERSION", v1.version_id) in back
    assert ("STRATEGY", rec.id) in back
    # Backward from DECISION should reach EVIDENCE, VERSION, STRATEGY
    back2 = g.trace_backward("DECISION", dec.decision_id)
    assert ("EVIDENCE", ev.evidence_id) in back2
    assert ("VERSION", v1.version_id) in back2
    assert ("STRATEGY", rec.id) in back2
    # Backward from EXECUTION
    back3 = g.trace_backward("EXECUTION", snap.execution_id)
    assert ("VERSION", v1.version_id) in back3
    assert ("STRATEGY", rec.id) in back3


# ── 19. historical restore ──────────────────────────────────────────────


def test_historical_restore(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_HIST", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    # Restore V1 source
    src1 = restore_version_source(rec.id, v1.version_id, data_dir=tmp_path)
    assert src1 == SOURCE_A
    src2 = restore_version_source(rec.id, v2.version_id, data_dir=tmp_path)
    assert src2 == SOURCE_B
    # Compile restored source and verify IR hash
    ir1 = compile_to_ir(src1)
    assert hashlib.sha256(ir1.to_json().encode()).hexdigest() == v1.ir_hash
    ir2 = compile_to_ir(src2)
    assert hashlib.sha256(ir2.to_json().encode()).hexdigest() == v2.ir_hash
    # Verify helper
    assert verify_version_ir(rec.id, v1.version_id, data_dir=tmp_path) is True
    assert verify_version_ir(rec.id, v2.version_id, data_dir=tmp_path) is True
    # Modify store to newer version does not change historical restore
    st, ht, vt, pt = _ir_and_hash(SOURCE_TAMPER)
    create_version(
        rec.id,
        SOURCE_TAMPER,
        ir_version=vt,
        ir_hash=ht,
        ir_snapshot=st,
        parameters=pt,
        data_dir=tmp_path,
    )
    assert restore_version_source(rec.id, v1.version_id, data_dir=tmp_path) == SOURCE_A


# ── 20. deterministic hashes ────────────────────────────────────────────


def test_deterministic_hashes(tmp_path: Path):
    # Same source with different whitespace/trailing should hash identically
    src_variants = [
        SOURCE_A,
        SOURCE_A + "   \n",
        SOURCE_A.replace("\n", " \n"),  # trailing spaces per line — canonical strips them
        "\n\n" + SOURCE_A.strip() + "\n\n",
    ]
    # Direct canonical hash check
    canonical_hashes = [
        hashlib.sha256("\n".join(l.rstrip() for l in s.strip().splitlines()).encode()).hexdigest()  # noqa: E741
        for s in src_variants
    ]
    assert len(set(canonical_hashes)) == 1
    # Version creation with variant should be considered duplicate (same canonical)
    rec = _make_strategy(tmp_path, "VC_DET", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # Try creating with whitespace-variant as source — should be duplicate
    variant = SOURCE_A + "   \n\n"
    sa2, ha2, va2, pa2 = _ir_and_hash(variant)
    # Note: IR hash of variant is same as original because compile_to_ir canonicalizes? But source_hash should be same  # noqa: E501
    with pytest.raises(DuplicateVersionError):
        create_version(
            rec.id,
            variant,
            ir_version=va2,
            ir_hash=ha2,
            ir_snapshot=sa2,
            parameters=pa2,
            data_dir=tmp_path,
        )
    # Timestamp not in hash
    v1_copy = load_version(rec.id, v1.version_id, data_dir=tmp_path)
    assert v1_copy is not None
    assert v1_copy.source_hash == v1.source_hash
    # Different source must have different hash
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    assert hb != ha


# ── 21. tamper detection ────────────────────────────────────────────────


def test_tamper_detection(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_TAMPER", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # Directly tamper with stored file's source (simulate corruption)
    fpath = tmp_path / "strategy_versions" / rec.id / f"{v1.version_id}.json"
    data = json.loads(fpath.read_text(encoding="utf-8"))
    data["source"] = SOURCE_TAMPER  # change source but keep old hash
    fpath.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
    # Restore should detect hash mismatch
    with pytest.raises(ValueError, match="tamper"):
        restore_version_source(rec.id, v1.version_id, data_dir=tmp_path)
    # Also verify IR should fail if snapshot tampered
    # Restore original, then tamper IR
    data["source"] = SOURCE_A
    data["source_hash"] = hashlib.sha256(
        "\n".join(l.rstrip() for l in SOURCE_A.strip().splitlines()).encode()  # noqa: E741
    ).hexdigest()
    # Tamper IR hash
    data["ir_hash"] = "0" * 64
    fpath.write_text(json.dumps(data, sort_keys=True, indent=2), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_version_ir(rec.id, v1.version_id, data_dir=tmp_path)


# ── 22. replay compatibility ────────────────────────────────────────────


def test_replay_compatibility(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_REPLAY", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    bars = _bars(50)
    from backtest.execution import ExecutionHistory, create_snapshot, replay_execution, save_history
    from backtest.models.config import BacktestConfig

    from strategy.vm import StrategyVM

    ir = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    # Build execution deterministically via VM (no runner needed) — use same logic as execution.save
    vm = StrategyVM(ir, pa)
    from strategy.models.parameters import StrategyParameters
    from strategy.models.state import StrategyState
    from strategy.runtime import BarView

    signals = []
    events = []
    from backtest.execution import ExecutionEvent

    seq = 0
    snap = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir, pa, cfg, data_dir=tmp_path)
    for idx in range(vm.warmup(), len(bars)):
        bar = bars[idx]
        view = BarView(bars=bars, index=idx, params=StrategyParameters(pa), state=StrategyState())
        sig = vm.on_bar(view)
        events.append(
            ExecutionEvent(
                execution_id=snap.execution_id,
                sequence=seq,
                event_type="BarProcessed",
                timestamp=bar.timestamp,
                data={"index": idx},
            )
        )
        seq += 1
        if sig is not None:
            signals.append(
                {
                    "index": sig.index,
                    "timestamp": sig.timestamp,
                    "kind": sig.kind.value,
                    "price": sig.price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                }
            )
            events.append(
                ExecutionEvent(
                    execution_id=snap.execution_id,
                    sequence=seq,
                    event_type="SignalGenerated",
                    timestamp=sig.timestamp,
                    data={"kind": sig.kind.value, "price": sig.price, "index": sig.index},
                )
            )
            seq += 1
    history = ExecutionHistory(snapshot=snap, events=events, signals=signals)
    save_history(history, data_dir=tmp_path)
    # Replay with same bars+IR should be VERIFIED
    result = replay_execution(history, bars, ir)
    assert result.status == "VERIFIED"
    assert result.expected_signals == result.actual_signals
    # V2 independent replay
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    ir2 = compile_to_ir(SOURCE_B)
    snap2 = create_snapshot(rec.id, v2.version_id, v2.source_hash, ir2, pb, cfg, data_dir=tmp_path)
    vm2 = StrategyVM(ir2, pb)
    signals2 = []
    events2 = []
    seq = 0
    for idx in range(vm2.warmup(), len(bars)):
        bar = bars[idx]
        view = BarView(bars=bars, index=idx, params=StrategyParameters(pb), state=StrategyState())
        sig = vm2.on_bar(view)
        events2.append(
            ExecutionEvent(
                execution_id=snap2.execution_id,
                sequence=seq,
                event_type="BarProcessed",
                timestamp=bar.timestamp,
                data={"index": idx},
            )
        )
        seq += 1
        if sig is not None:
            signals2.append(
                {
                    "index": sig.index,
                    "timestamp": sig.timestamp,
                    "kind": sig.kind.value,
                    "price": sig.price,
                    "stop_loss": sig.stop_loss,
                    "take_profit": sig.take_profit,
                }
            )
            events2.append(
                ExecutionEvent(
                    execution_id=snap2.execution_id,
                    sequence=seq,
                    event_type="SignalGenerated",
                    timestamp=sig.timestamp,
                    data={"kind": sig.kind.value, "price": sig.price, "index": sig.index},
                )
            )
            seq += 1
    history2 = ExecutionHistory(snapshot=snap2, events=events2, signals=signals2)
    save_history(history2, data_dir=tmp_path)
    result2 = replay_execution(history2, bars, ir2)
    assert result2.status == "VERIFIED"
    # Historical V1 replay still VERIFIED after V2 creation
    assert (
        replay_execution(
            load_history := __import__(  # noqa: F841
                "backtest.execution", fromlist=["load_history"]
            ).load_history(snap.execution_id, data_dir=tmp_path),
            bars,
            ir,
        ).status
        == "VERIFIED"
    )  # type: ignore[attr-defined]


def test_evidence_immutability(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_EVID_IMM", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    ev = create_evidence(
        rec.id,
        v1.version_id,
        source_type="execution",
        source_id="EXEC-001",
        metric="win_rate",
        value=0.6,
    )
    save_evidence(ev, data_dir=tmp_path)
    # Modify and try to save with same id but different value — must raise or not overwrite
    ev2 = create_evidence(
        rec.id,
        v1.version_id,
        source_type="execution",
        source_id="EXEC-001",
        metric="win_rate",
        value=0.9,
    )
    # Force same id
    object.__setattr__(ev2, "evidence_id", ev.evidence_id)  # type: ignore[attr-defined]
    with pytest.raises(FileExistsError):
        save_evidence(ev2, data_dir=tmp_path)
    # Original unchanged
    from strategy.research.evidence import load_evidence

    loaded = load_evidence(ev.evidence_id, data_dir=tmp_path)
    assert loaded.value == 0.6


def test_governance_does_not_auto_deploy(tmp_path: Path):
    rec = _make_strategy(tmp_path, "VC_GOV", SOURCE_A)
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # Create evidence + decision PASS
    ev = create_evidence(
        rec.id,
        v1.version_id,
        source_type="validation",
        source_id="VAL-001",
        metric="PASS",
        value=True,
    )
    save_evidence(ev, data_dir=tmp_path)
    dec = create_decision(
        rec.id,
        v1.version_id,
        discovery_id="DISC-001",
        validation_id="VAL-001",
        evidence_ids=[ev.evidence_id],
        status="VALIDATED",
        rationale="strong evidence",
    )
    save_decision(dec, data_dir=tmp_path)
    # Decision must NOT have created a new version nor modified strategy
    assert len(list_versions(rec.id, data_dir=tmp_path)) == 1
    # Strategy source unchanged
    assert load_strategy_record("VC_GOV", data_dir=tmp_path).code == SOURCE_A  # type: ignore[union-attr]


# ── Integration: full flow per spec Problem 18 ─────────────────────────


def test_integration_full_flow(tmp_path: Path):
    """Create Strategy A -> V1 -> Backtest V1 -> Research -> Evidence -> Decision -> Modify -> V2 -> Backtest V2 -> Research -> Evidence; verify isolation."""  # noqa: E501
    # 1. Create Strategy A
    rec = _make_strategy(tmp_path, "IntegrationStrat", SOURCE_A)
    # 2. Create V1
    sa, ha, va, pa = _ir_and_hash(SOURCE_A)
    v1 = create_version(
        rec.id,
        SOURCE_A,
        ir_version=va,
        ir_hash=ha,
        ir_snapshot=sa,
        parameters=pa,
        data_dir=tmp_path,
    )
    # 3. Backtest V1
    _bars(60)
    from backtest.execution import ExecutionHistory, create_snapshot, save_history
    from backtest.models.config import BacktestConfig

    ir1 = compile_to_ir(SOURCE_A)
    cfg = BacktestConfig(
        symbol="TEST", timeframe="15m", start_date="2026-01-01", end_date="2026-01-31"
    )
    snap1 = create_snapshot(rec.id, v1.version_id, v1.source_hash, ir1, pa, cfg, data_dir=tmp_path)
    hist1 = ExecutionHistory(snapshot=snap1, events=[], signals=[{"index": 0, "kind": "BUY"}])
    save_history(hist1, data_dir=tmp_path)
    # 4. Create Research record (dataset + experiment)
    from strategy.research.dataset import ResearchDataset
    from strategy.research.experiment import create_experiment
    from strategy.research.storage import save_experiment

    ds1 = ResearchDataset.from_histories(rec.id, v1.version_id, [hist1], parameters=pa)
    assert ds1.version_id == v1.version_id
    exp1 = create_experiment(
        rec.id, v1.version_id, [snap1.execution_id], hypothesis_text="V1 hypothesis"
    )
    save_experiment(exp1, data_dir=tmp_path)
    # 5. Create Evidence
    ev1 = create_evidence(
        rec.id,
        v1.version_id,
        source_type="execution",
        source_id=snap1.execution_id,
        metric="expectancy",
        value=0.4,
        experiment_id=exp1.experiment_id,
    )
    save_evidence(ev1, data_dir=tmp_path)
    # 6. Create Decision
    dec1 = create_decision(
        rec.id,
        v1.version_id,
        discovery_id="DISC-V1",
        validation_id="VAL-V1",
        evidence_ids=[ev1.evidence_id],
        status="VALIDATED",
        rationale="V1 validated",
    )
    save_decision(dec1, data_dir=tmp_path)
    # 7. Modify Strategy -> Create V2
    # Simulate edit in library
    from strategy.language.storage import update_strategy

    update_strategy(rec.id, new_code=SOURCE_B, data_dir=tmp_path)
    sb, hb, vb, pb = _ir_and_hash(SOURCE_B)
    v2 = create_version(
        rec.id,
        SOURCE_B,
        ir_version=vb,
        ir_hash=hb,
        ir_snapshot=sb,
        parameters=pb,
        data_dir=tmp_path,
    )
    assert v2.version_id != v1.version_id
    assert v2.parent_version_id == v1.version_id
    # 8. Backtest V2
    ir2 = compile_to_ir(SOURCE_B)
    snap2 = create_snapshot(rec.id, v2.version_id, v2.source_hash, ir2, pb, cfg, data_dir=tmp_path)
    hist2 = ExecutionHistory(snapshot=snap2, events=[], signals=[{"index": 1, "kind": "SELL"}])
    save_history(hist2, data_dir=tmp_path)
    # 9. Create Research record for V2
    ds2 = ResearchDataset.from_histories(rec.id, v2.version_id, [hist2], parameters=pb)
    exp2 = create_experiment(
        rec.id, v2.version_id, [snap2.execution_id], hypothesis_text="V2 hypothesis"
    )
    save_experiment(exp2, data_dir=tmp_path)
    # 10. Create Evidence for V2
    ev2 = create_evidence(
        rec.id,
        v2.version_id,
        source_type="execution",
        source_id=snap2.execution_id,
        metric="expectancy",
        value=0.9,
        experiment_id=exp2.experiment_id,
    )
    save_evidence(ev2, data_dir=tmp_path)

    # ── Verifications (spec Problem 18) ──
    assert v1 != v2
    assert v1.source != v2.source
    # V1 source unchanged after V2
    assert load_version(rec.id, v1.version_id, data_dir=tmp_path).source == SOURCE_A  # type: ignore[union-attr]
    assert load_version(rec.id, v2.version_id, data_dir=tmp_path).source == SOURCE_B  # type: ignore[union-attr]
    # V1 execution points to V1, V2 to V2
    from backtest.execution import load_history

    h1 = load_history(snap1.execution_id, data_dir=tmp_path)
    h2 = load_history(snap2.execution_id, data_dir=tmp_path)
    assert h1.snapshot.version_id == v1.version_id
    assert h2.snapshot.version_id == v2.version_id
    # V1 research points to V1, V2 to V2
    assert ds1.version_id == v1.version_id
    assert ds2.version_id == v2.version_id
    assert exp1.version_id == v1.version_id
    assert exp2.version_id == v2.version_id
    # V1 evidence points to V1, V2 to V2
    from strategy.research.evidence import load_evidence

    assert load_evidence(ev1.evidence_id, data_dir=tmp_path).version_id == v1.version_id  # type: ignore[union-attr]
    assert load_evidence(ev2.evidence_id, data_dir=tmp_path).version_id == v2.version_id  # type: ignore[union-attr]
    # Lineage is correct (forward and backward)
    g = load_lineage(data_dir=tmp_path)
    # Forward from strategy reaches both versions, both executions, both evidence, decision
    fwd = g.trace_forward("STRATEGY", rec.id)
    assert ("VERSION", v1.version_id) in fwd
    assert ("VERSION", v2.version_id) in fwd
    assert ("EXECUTION", snap1.execution_id) in fwd
    assert ("EXECUTION", snap2.execution_id) in fwd
    assert ("EVIDENCE", ev1.evidence_id) in fwd
    assert ("EVIDENCE", ev2.evidence_id) in fwd
    assert ("DECISION", dec1.decision_id) in fwd
    # Backward from ev1 reaches V1, strategy
    back = g.trace_backward("EVIDENCE", ev1.evidence_id)
    assert ("VERSION", v1.version_id) in back
    assert ("STRATEGY", rec.id) in back
    # Backward from ev2 reaches V2
    back2 = g.trace_backward("EVIDENCE", ev2.evidence_id)
    assert ("VERSION", v2.version_id) in back2
    # Replay V1 and V2 still work (historical restore)
    src_r1 = restore_version_source(rec.id, v1.version_id, data_dir=tmp_path)
    src_r2 = restore_version_source(rec.id, v2.version_id, data_dir=tmp_path)
    assert src_r1 == SOURCE_A
    assert src_r2 == SOURCE_B
    # Compile restored and verify hashes
    assert hashlib.sha256(compile_to_ir(src_r1).to_json().encode()).hexdigest() == v1.ir_hash
    assert hashlib.sha256(compile_to_ir(src_r2).to_json().encode()).hexdigest() == v2.ir_hash
