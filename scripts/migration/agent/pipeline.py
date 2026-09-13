"""Autonomous pipeline: analyze → sandbox → wire → verify → promote.

Each stage records evidence on the unit manifest. Production is written
only after the isolated sandbox gate passes; any post-wire gate failure
restores the pre-wire snapshots and rolls back canonical authority.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from .. import promotion as _promotion
from .. import rollback as _rollback
from .. import validator as _validator
from ..config import ROOT
from ..manifest_store import append_evidence, load_all, load_manifest, save_manifest
from ..models import MigrationManifest
from . import analyzer as _analyzer
from . import generator_oracle as _oracle_gen
from . import generator_rust as _rust_gen
from . import kernels as _kernels
from . import repair as _repair
from . import sandbox as _sandbox
from . import wiring as _wiring
from .models import AgentOutcome, AttemptRecord
from .order import blocked_order
from .spec import eval_kernel, pack_env

MAX_SANDBOX_ATTEMPTS = 5

RISK_TESTS = [
    "python",
    "-m",
    "pytest",
    "07_risk/risk/tests",
    "-q",
    "--no-header",
    "-p",
    "no:cacheprovider",
]


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record(manifest: MigrationManifest, entry: str) -> MigrationManifest:
    return append_evidence(manifest, f"{_stamp()} AGENT {entry}")


def _save(manifest: MigrationManifest) -> None:
    save_manifest(manifest)


def _run(argv: list[str], cwd: Path, timeout_s: int = 600) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    return proc.returncode, ((proc.stdout or "") + "\n" + (proc.stderr or ""))[-4000:]


def _cargo(args: list[str]) -> tuple[int, str]:
    if shutil.which("cargo") is None:
        return 2, "cargo toolchain missing"
    return _run(["cargo", *args, "--manifest-path", "rust/Cargo.toml"], ROOT)


def _dll_lock_diagnosis() -> str:
    """Identify processes likely holding the cdylib (evidence, no action)."""
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/FO", "TABLE"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        lines = [
            line.strip() for line in (proc.stdout or "").splitlines() if "python" in line.lower()
        ]
        return f"python holders: {len(lines)} ({'; '.join(lines[:4])})"
    except Exception as exc:
        return f"diagnosis unavailable: {exc}"


def _cargo_build_with_retry(logs: list[str]) -> tuple[int, str]:
    """Release build with bounded retries on Windows DLL-lock contention."""
    import time as _time

    logs.append("build started")
    attempt = 0
    last = ""
    while attempt < 4:
        attempt += 1
        code, log = _cargo(["build", "--release", "-p", "vayren-core"])
        last = log
        if code == 0:
            return code, f"built after {attempt} attempt(s)"
        if "failed to remove file" in log and "vayren_core" in log:
            diagnosis = _dll_lock_diagnosis()
            logs.append(f"build attempt {attempt}: cdylib locked — {diagnosis}")
            _time.sleep(30)
            continue
        return code, log
    return 1, last + "\ncdylib still locked after retries"


def run_unit(unit_id: str, dry_run: bool = False) -> AgentOutcome:
    """Run the full autonomous loop for one unit."""
    stages: list[AttemptRecord] = []
    evidence: list[str] = []

    def note(stage: str, ok: bool, detail: str = "") -> None:
        stages.append(AttemptRecord(stage, len(stages) + 1, ok, detail[:300]))
        evidence.append(f"{stage}: {'ok' if ok else 'FAILED'} {detail[:200]}".strip())

    manifests = load_all()
    manifest = manifests.get(unit_id)
    if manifest is None:
        return AgentOutcome(unit_id, "FAILED", tuple(stages), ("unknown unit",))

    report = _analyzer.analyze(unit_id)
    manifest = _record(manifest, f"ANALYZE migratable={report.migratable}: {report.reason}")
    _save(manifest)
    if not report.migratable:
        note("ANALYZE", False, report.reason)
        return AgentOutcome(unit_id, "BLOCKED", tuple(stages), tuple(evidence))
    note("ANALYZE", True, report.reason)
    if dry_run:
        return AgentOutcome(unit_id, "BLOCKED", tuple(stages), tuple(evidence) + ("dry-run",))
    if unit_id != "risk.engine.evaluate":
        note("SPEC", False, "no generator for this pattern yet")
        return AgentOutcome(unit_id, "BLOCKED", tuple(stages), tuple(evidence))

    try:
        spec = _analyzer.extract_risk_spec()
    except Exception as exc:
        note("SPEC", False, f"extraction failed: {exc}")
        return AgentOutcome(unit_id, "BLOCKED", tuple(stages), tuple(evidence))
    spec_dict = spec.to_dict()
    _wiring.evidence_dir(unit_id).joinpath("spec.json").write_text(
        json.dumps(spec_dict, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    manifest = _record(
        manifest,
        f"SPEC {len(spec.checks)} kernel checks, orchestration={','.join(spec.orchestration)}",
    )
    _save(manifest)
    note("SPEC", True, f"{len(spec.checks)} checks")

    # Sandbox vectors: golden + seeded fuzz envs with pinned masks.
    # NOTE: import models directly — importing `risk` would load the
    # (possibly just-wired) engine + cdylib and lock vayren_core.dll on
    # Windows, blocking the cargo rebuild below.
    from risk.models import RiskPolicy, RiskRequest

    vectors: list[tuple[dict, int]] = []
    for case in _kernels.build_risk_golden():
        policy = RiskPolicy(
            **{**case["policy"], "allowed_symbols": tuple(case["policy"]["allowed_symbols"])}
        )
        request = RiskRequest(**case["request"])
        env = pack_env(spec_dict, policy, request)
        vectors.append((env, eval_kernel(spec_dict, env)))
    import random as _random

    rng = _random.Random(31337)
    for trial in range(90):
        policy_d = dict(_kernels.build_risk_golden()[trial % 29]["policy"])
        request_d = dict(_kernels.build_risk_golden()[trial % 29]["request"])
        request_d["intent_id"] = f"sandbox-{trial}"
        request_d["quantity"] = rng.uniform(-50.0, 1100.0)
        request_d["price"] = rng.uniform(1.0, 400.0)
        policy = RiskPolicy(**{**policy_d, "allowed_symbols": tuple(policy_d["allowed_symbols"])})
        request = RiskRequest(**request_d)
        env = pack_env(spec_dict, policy, request)
        vectors.append((env, eval_kernel(spec_dict, env)))
    note("VECTORS", True, f"{len(vectors)} pinned vectors")

    kernel_rs = _rust_gen.render_kernel(spec_dict)
    harness_rs = _rust_gen.render_sandbox_harness(spec_dict, vectors)
    gate = _sandbox.run_sandbox_gate(
        kernel_rs, harness_rs, MAX_SANDBOX_ATTEMPTS, _repair.repair_candidate
    )
    manifest = _record(
        manifest, f"SANDBOX ok={gate.ok} attempts={gate.attempts} repairs={list(gate.repairs)}"
    )
    _save(manifest)
    if not gate.ok:
        note("SANDBOX", False, gate.detail)
        return AgentOutcome(unit_id, "BLOCKED", tuple(stages), tuple(evidence) + (gate.log_tail,))
    note("SANDBOX", True, gate.detail)

    # Wire production (snapshots first).
    wire_files = [
        _wiring.RISK_RS,
        _wiring.LIB_RS,
        _wiring.BRIDGE_PY,
        _wiring.ENGINE_PY,
        _wiring.PARITY_TEST,
        _wiring.ORACLE_DIR / "risk_engine_oracle_v1.py",
        _wiring.ORACLE_DIR / "__init__.py",
        # The WIRE stage calls add_retention_entry(), which mutates this file.
        # Snapshot it too, or a rollback restores the code but orphans the
        # retention entry — leaving the repo half-migrated and failing
        # validate_language_ownership.
        _wiring.RETENTION_PATH,
    ]
    manifest = _record(manifest, f"SNAPSHOT {len(wire_files)} files")
    _wiring.snapshot(unit_id, wire_files)
    _save(manifest)

    def restore(note_text: str) -> AgentOutcome:
        restored = _wiring.restore(unit_id)
        fresh = load_manifest(unit_id)
        if fresh is not None:
            _save(_record(fresh, f"RESTORE {note_text}: {restored}"))
        note("RESTORE", True, note_text)
        return AgentOutcome(unit_id, "BLOCKED", tuple(stages), tuple(evidence))

    code, log = _run(["ruff", "format", "--check", "07_risk/risk/engine.py"], ROOT)
    if code != 0:
        note("WIRE", False, "engine.py has pre-existing format drift; refusing to touch")
        return restore("pre-existing format drift in engine.py")

    try:
        engine_src = _wiring.ENGINE_PY.read_text(encoding="utf-8")
        oracle_digest = _oracle_gen.snapshot_hash(engine_src)
        kernel_names = [c["name"] for c in spec_dict["checks"]]
        bit_names = [f"BIT_{name.upper()}" for name in kernel_names]
        new_lib = _wiring.patch_lib_rs(spec_dict)
        bridge_src = _wiring.render_bridge(spec_dict, kernel_names)
        new_engine = _wiring.patch_engine(engine_src, kernel_names, bit_names)
        oracle_src = _oracle_gen.render_oracle_module(engine_src, oracle_digest)
        parity_test = _oracle_gen.render_parity_test(unit_id, oracle_digest)
        golden_json = json.dumps(_kernels.build_risk_golden(), indent=2, sort_keys=True) + "\n"
    except Exception as exc:
        note("GENERATE", False, f"{exc}")
        return restore(f"generation failed: {exc}")
    note("GENERATE", True, "risk.rs + bridge + engine patch + oracle + test")

    _wiring.write_text(_wiring.RISK_RS, kernel_rs)
    _wiring.write_text(_wiring.LIB_RS, new_lib)
    _wiring.write_text(_wiring.BRIDGE_PY, bridge_src)
    _wiring.write_text(_wiring.ENGINE_PY, new_engine)
    _wiring.write_text(_wiring.ORACLE_DIR / "__init__.py", "")
    _wiring.write_text(_wiring.ORACLE_DIR / "risk_engine_oracle_v1.py", oracle_src)
    _wiring.write_text(_wiring.PARITY_TEST, parity_test)
    golden_path = ROOT / "90_brain" / "migration" / "golden" / "risk_engine_evaluate.json"
    _wiring.write_text(golden_path, golden_json)
    _wiring.add_retention_entry(
        "07_risk/risk/native_checks.py",
        {
            "state": "TEMPORARILY_RETAINED",
            "domain": "RISK",
            "reason": "Agent-migrated ctypes bridge for the risk policy kernel. "
            "No logic: packs spec-ordered env, calls vy_risk_kernel. "
            "Strings/state stay in engine.py.",
            "migration_target": "rust/vayren-core/src/risk.rs",
            "migration_condition": "Kernel proven; bridge retained as the FFI "
            "projection (same pattern as native_metrics/native_aggregate).",
        },
    )
    manifest = _record(manifest, "WIRE production files written")
    _save(manifest)
    note("WIRE", True, "7 files + retention entry")

    code, log = _run(
        ["ruff", "check", "07_risk/risk/native_checks.py", "07_risk/risk/engine.py"],
        ROOT,
    )
    if code != 0:
        note("LINT", False, log[-300:])
        return restore(f"ruff check on wired files failed: {log[-300:]}")
    # Format only agent-created files; engine.py stays hand-shaped + verified.
    code, log = _run(
        [
            "ruff",
            "format",
            "07_risk/risk/native_checks.py",
            "07_risk/risk/tests/test_risk_kernel_parity.py",
            "scripts/migration/agent/oracles/risk_engine_oracle_v1.py",
        ],
        ROOT,
    )
    if code != 0:
        note("LINT", False, log[-300:])
        return restore(f"ruff format on generated files failed: {log[-300:]}")
    note("LINT", True, "ruff clean on wired files")

    # engine.py is formatted in place (it was format-clean pre-patch, so the
    # formatter only normalizes the spliced regions), then re-verified
    # structurally — formatting can never change check semantics silently.
    code, log = _run(["ruff", "format", "07_risk/risk/engine.py"], ROOT)
    if code != 0:
        note("LINT", False, log[-300:])
        return restore(f"ruff format on engine.py failed: {log[-300:]}")
    if not _wiring.verify_engine_shape(
        _wiring.ENGINE_PY.read_text(encoding="utf-8"), list(spec_dict["check_order"])
    ):
        note("LINT", False, "engine shape changed by formatting")
        return restore("engine shape verification failed after format")
    note("FORMAT", True, "engine.py canonical + shape verified")

    # rustfmt applies only to the two agent-owned files (lib.rs is
    # format-clean pre-patch, so nothing else moves); the workspace check
    # afterwards proves it. TEST/VERIFY re-prove semantics post-format.
    for path in (_wiring.RISK_RS, _wiring.LIB_RS):
        code, log = _run(["rustfmt", "--edition", "2021", str(path)], ROOT)
        if code != 0:
            note("FMT", False, log[-300:])
            return restore(f"rustfmt failed on {path.name}: {log[-300:]}")
    code, log = _run(
        ["cargo", "fmt", "--manifest-path", "rust/Cargo.toml", "--all", "--", "--check"],
        ROOT,
    )
    if code != 0:
        note("FMT", False, log[-300:])
        return restore(f"cargo fmt check failed on wired files: {log[-300:]}")
    note("FMT", True, "workspace fmt clean")
    code, build_notes = _cargo_build_with_retry([])
    if code != 0:
        note("BUILD", False, build_notes[-300:])
        return restore(f"cargo build failed: {build_notes[-300:]}")
    note("BUILD", True, build_notes[-200:])

    code, log = _run(
        [
            "python",
            "-m",
            "pytest",
            "07_risk/risk/tests",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        ROOT,
    )
    if code != 0:
        note("TEST", False, log[-500:])
        return restore(f"risk tests failed: {log[-500:]}")
    note("TEST", True, "risk suite green")

    from .. import engine as _verify_engine

    summary = _verify_engine.verify(shadow_count=60)
    state = summary["states"].get(unit_id, "?")
    manifest = load_manifest(unit_id) or manifest
    manifest = _record(manifest, f"VERIFY state={state}")
    _save(manifest)
    if state not in (
        "INTEGRATED",
        "RUST_CANONICAL",
        "PYTHON_DEPRECATED",
        "PYTHON_QUARANTINED",
        "PYTHON_REMOVED",
        "FINAL_VERIFIED",
        "MIGRATED",
    ):
        note("VERIFY", False, f"state={state}, expected at least INTEGRATED")
        return restore(f"verify did not reach INTEGRATED (state={state})")
    note("VERIFY", True, f"{state} with fresh parity+shadow")

    if state != "INTEGRATED":
        # Already canonical (e.g. deterministic re-run): re-proven above,
        # nothing left to promote — report honestly instead of failing.
        gate_code, _ = _validator.validate_all()
        if gate_code != 0:
            note("POSTCHECK", False, "gate red on canonical re-run")
            return AgentOutcome(unit_id, "FAILED", tuple(stages), tuple(evidence))
        note("POSTCHECK", True, "canonical standing re-verified")
        return AgentOutcome(unit_id, "CANONICAL", tuple(stages), tuple(evidence))

    ok, message = _promotion.promote(unit_id, actor="migration-agent")
    manifest = load_manifest(unit_id) or manifest
    manifest = _record(manifest, f"PROMOTE ok={ok}: {message[:200]}")
    _save(manifest)
    if not ok:
        note("PROMOTE", False, message)
        return restore(f"promotion refused: {message}")
    note("PROMOTE", True, message)

    gate_code, gate_report = _validator.validate_all()
    risk_code, risk_log = _run(
        [
            "python",
            "-m",
            "pytest",
            "07_risk/risk/tests",
            "-q",
            "--no-header",
            "-p",
            "no:cacheprovider",
        ],
        ROOT,
    )
    if gate_code != 0 or risk_code != 0:
        fail_detail = f"gate={gate_code} risk={risk_code} {risk_log[-300:]}"
        rb_ok, rb_msg = _rollback.rollback(
            unit_id, f"post-promotion validation failed: {fail_detail}"
        )
        _wiring.restore(unit_id)
        manifest = load_manifest(unit_id) or manifest
        _save(_record(manifest, f"ROLLBACK ok={rb_ok}: {rb_msg[:200]}"))
        note("POSTCHECK", False, fail_detail)
        return AgentOutcome(unit_id, "FAILED", tuple(stages), tuple(evidence))
    note("POSTCHECK", True, "gate + risk suite green after promotion")
    return AgentOutcome(unit_id, "PROMOTED", tuple(stages), tuple(evidence))


def run_all(dry_run: bool = False, only: list[str] | None = None) -> list[AgentOutcome]:
    """Run the agent over BLOCKED units in exact dependency order."""
    outcomes: list[AgentOutcome] = []
    # Explicitly requested units run regardless of current state
    # (continue, retry or re-verify); the pipeline re-proves everything.
    targets = [uid for uid in only if uid] if only else [entry.unit_id for entry in blocked_order()]
    for unit_id in targets:
        try:
            outcomes.append(run_unit(unit_id, dry_run=dry_run))
        except Exception as exc:  # noqa: BLE001 — autonomy continues with evidence
            outcomes.append(AgentOutcome(unit_id, "FAILED", (), (f"pipeline crashed: {exc}",)))
    return outcomes


__all__ = ["run_unit", "run_all"]
