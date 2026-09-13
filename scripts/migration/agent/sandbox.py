"""Isolated sandbox: candidate Rust is proven before production is touched.

The sandbox is a throwaway cargo crate in the system temp dir. It compiles
the generated kernel plus a differential test harness (oracle vs candidate
over golden + fuzz inputs) and runs it. Production files are never written
from here — wiring happens only after the sandbox gate passes.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SandboxResult:
    ok: bool
    stage: str
    detail: str = ""
    log_tail: str = ""
    attempts: int = 1
    repairs: tuple = field(default_factory=tuple)


def _run(argv: list[str], cwd: Path, timeout_s: int = 420) -> tuple[int, str]:
    proc = subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
    )
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return proc.returncode, combined[-4000:]


def _has_cargo() -> bool:
    return shutil.which("cargo") is not None


def run_sandbox_gate(
    kernel_rs: str,
    harness_rs: str,
    max_attempts: int = 5,
    repair=None,  # type: ignore[no-untyped-def]
) -> SandboxResult:
    """Compile + test the candidate in isolation, repairing mechanically."""
    if not _has_cargo():
        return SandboxResult(False, "sandbox", "cargo toolchain missing — INCONCLUSIVE, not PASS")
    tmp = Path(tempfile.mkdtemp(prefix="vayren-agent-"))
    repairs: list[str] = []
    try:
        crate = tmp / "kernel_probe"
        code, log = _run(["cargo", "new", "--lib", "--name", "kernel_probe", "kernel_probe"], tmp)
        if code != 0:
            return SandboxResult(False, "sandbox-init", "cargo new failed", log)
        src = crate / "src"
        (src / "risk_kernel.rs").write_text(kernel_rs, encoding="utf-8")
        (src / "lib.rs").write_text("pub mod risk_kernel;\n", encoding="utf-8")
        (crate / "tests" / "diff.rs").parent.mkdir(parents=True, exist_ok=True)
        (crate / "tests" / "diff.rs").write_text(harness_rs, encoding="utf-8")
        current = kernel_rs
        for attempt in range(1, max_attempts + 1):
            code, log = _run(["cargo", "test", "--quiet"], crate)
            if code == 0:
                return SandboxResult(
                    True,
                    "sandbox",
                    f"probe passed (attempt {attempt})",
                    log,
                    attempt,
                    tuple(repairs),
                )
            if repair is None:
                return SandboxResult(
                    False, "sandbox-test", "probe failed, no repair", log, attempt, tuple(repairs)
                )
            fixed, note = repair(current, log)
            if fixed is None:
                return SandboxResult(
                    False, "sandbox-test", f"unrepairable: {note}", log, attempt, tuple(repairs)
                )
            repairs.append(note)
            current = fixed
            (src / "risk_kernel.rs").write_text(current, encoding="utf-8")
        return SandboxResult(
            False, "sandbox-test", "repair budget exhausted", log, max_attempts, tuple(repairs)
        )
    except subprocess.TimeoutExpired:
        return SandboxResult(False, "sandbox", "cargo timed out", "", 1, tuple(repairs))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


__all__ = ["SandboxResult", "run_sandbox_gate"]
