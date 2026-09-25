"""CI shard contract — every scripts/tests file runs in exactly one CI site.

Prevents silent omission/duplication as suites shard across parallel jobs:
the union of pytest file targets in ci.yml (build-test extras + shard
jobs) plus run_tests.py PARTS coverage must equal every test_*.py file on
disk, with no file covered twice. Local serial runs (run_tests.py PARTS)
stay intact and are part of the contract.

The ci.yml subset parser below is deliberately dependency-free (stdlib
only): the architecture gate forbids undeclared third-party imports.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPTS_DIR = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import run_tests  # noqa: E402

SHARD_JOBS = ("test-shard-stateful", "test-shard-pure")
TEST_FILE_RE = re.compile(r"scripts/tests/(test_\w+\.py)")


def _ci_jobs() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """Minimal ci.yml parse: (job -> run-block texts, job -> needs).

    Understands only what the contract needs: two-space job keys, `needs:`
    lists, `- run:` / `- run: >-` step blocks with deeper-indented
    continuations. Stdlib only (the architecture gate forbids undeclared
    third-party imports).
    """
    runs: dict[str, list[str]] = {}
    needs: dict[str, list[str]] = {}
    current: str | None = None
    in_jobs = False
    block: list[str] | None = None
    for raw in (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        stripped = line.strip()
        if indent == 0 and stripped == "jobs:":
            in_jobs = True
            continue
        if not in_jobs:
            continue
        if indent == 2 and stripped.endswith(":"):
            current = stripped[:-1]
            runs[current] = []
            needs[current] = []
            block = None
            continue
        if current is None:
            continue
        if indent == 4 and stripped.startswith("needs:"):
            listed = stripped[len("needs:") :].strip().strip("[]")
            needs[current] = [part.strip() for part in listed.split(",") if part.strip()]
            block = None
            continue
        if indent == 6 and stripped.startswith("- run:"):
            block = [stripped[len("- run:") :].strip()]
            runs[current].append(" ".join(block))
            continue
        if block is not None and indent > 6 and stripped:
            block.append(stripped)
            runs[current][-1] = " ".join(block)
            continue
        if indent <= 6:
            block = None
    return runs, needs


def _ci_pytest_files() -> dict[str, list[str]]:
    """Map CI job -> scripts/tests files it executes directly via pytest."""
    runs, _ = _ci_jobs()
    found: dict[str, list[str]] = {}
    for name, blocks in runs.items():
        files: list[str] = []
        for block in blocks:
            if "pytest" not in block:
                continue
            files.extend(TEST_FILE_RE.findall(block))
        if files:
            found[name] = files
    return found


def _on_disk() -> list[str]:
    return sorted(p.name for p in (SCRIPTS_DIR / "tests").glob("test_*.py"))


def test_shard_jobs_exist() -> None:
    _, needs = _ci_jobs()
    runs, _ = _ci_jobs()
    for name in SHARD_JOBS:
        assert name in runs, f"shard job missing from ci.yml: {name}"
    for name in (*SHARD_JOBS, "build-test", "quality", "validators"):
        assert name in needs.get("gate", []), f"gate must wait for {name}"


def test_every_file_covered_exactly_once() -> None:
    text = "\n".join(
        raw.split("#", 1)[0]
        for raw in (ROOT / ".github" / "workflows" / "ci.yml")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    bare = re.findall(r"pytest\s+scripts/tests(?![/\w])", text)
    assert bare == [], f"directory-form pytest would duplicate shards: {bare}"
    covered = _ci_pytest_files()
    seen: dict[str, str] = {}
    for job, files in covered.items():
        for filename in files:
            assert filename not in seen, f"{filename} covered twice ({seen[filename]}, {job})"
            seen[filename] = job
    assert sorted(seen) == _on_disk(), (
        f"coverage gap: missing={sorted(set(_on_disk()) - set(seen))} "
        f"unknown={sorted(set(seen) - set(_on_disk()))}"
    )


def test_mutators_share_one_serial_shard() -> None:
    covered = _ci_pytest_files()
    owners = {filename: job for job, files in covered.items() for filename in files}
    assert owners["test_validate_scope.py"] == owners["test_failure_intel.py"]


def test_desktop_runs_beside_its_binary() -> None:
    covered = _ci_pytest_files()
    assert covered.get("build-test") == ["test_desktop.py"]


def test_local_serial_path_intact() -> None:
    assert "scripts/tests" in run_tests.PARTS
