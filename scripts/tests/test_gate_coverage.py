"""Gate coverage — every tests/ dir must be executed by the gate.

Regression test for the 2026-09-05 finding: 92 strategy/backtest/research
tests were silently skipped because no test runner knew about their
directories. This test fails if any tests/ directory under the product
chapters (or scripts tooling) is missing from BOTH `pyproject.testpaths`
(default `pytest` / `make test`) AND `scripts/run_tests.py` PARTS
(partitioned runner / CI).
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent

# Product chapters whose tests/ dirs must be covered. 99_archive is a legacy
# snapshot (not shipped, not validated) and is deliberately excluded.
CHAPTERS = (
    "00_app",
    "01_core",
    "02_data",
    "03_market",
    "05_strategy",
    "06_backtest",
    "07_risk",
    "08_execution",
    "09_broker",
)


def _expected_tests_dirs() -> list[Path]:
    dirs: list[Path] = []
    for chapter in CHAPTERS:
        for tests_dir in sorted((ROOT / chapter).rglob("tests")):
            if tests_dir.is_dir() and list(tests_dir.glob("test_*.py")):
                dirs.append(tests_dir)
    for extra in ("scripts/forensics/tests", "scripts/tests"):
        p = ROOT / extra
        if p.is_dir():
            dirs.append(p)
    return sorted(dirs)


def _testpaths() -> list[str]:
    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    return list(data["tool"]["pytest"]["ini_options"]["testpaths"])


def _run_tests_parts() -> tuple[list[str], list[str]]:
    """Return (directory parts, file-glob-covered directories) from PARTS.

    Parses `scripts/run_tests.py` via AST (no import, no CWD dependence).
    """
    tree = ast.parse((ROOT / "scripts" / "run_tests.py").read_text(encoding="utf-8"))
    dirs: list[str] = []
    file_covered: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "PARTS":
            value = node.value
            assert isinstance(value, ast.Tuple), "PARTS must stay a tuple literal"
            for elt in value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    dirs.append(elt.value)
                elif isinstance(elt, ast.Starred):
                    # Shape: *sorted(glob.glob("<dir>/test_*.py"))
                    call = elt.value
                    if isinstance(call, ast.Call) and getattr(call.func, "id", "") == "sorted":
                        assert len(call.args) == 1 and isinstance(call.args[0], ast.Call)
                        call = call.args[0]
                    assert isinstance(call, ast.Call), "starred PARTS must stay glob.glob(...)"
                    pattern = call.args[0]
                    assert isinstance(pattern, ast.Constant), "glob pattern must be a literal"
                    file_covered.append(str(Path(str(pattern.value)).parent))
            break
    assert dirs, "PARTS not found in scripts/run_tests.py"
    return dirs, file_covered


def test_every_tests_dir_in_default_testpaths() -> None:
    testpaths = [t.replace("\\", "/") for t in _testpaths()]
    missing = [
        d.relative_to(ROOT).as_posix()
        for d in _expected_tests_dirs()
        if not any(
            d.relative_to(ROOT).as_posix() == t
            or d.relative_to(ROOT).as_posix().startswith(t + "/")
            for t in testpaths
        )
    ]
    assert not missing, f"tests/ dirs missing from pyproject testpaths: {missing}"


def test_every_tests_dir_in_partitioned_runner() -> None:
    dirs, file_covered = _run_tests_parts()
    missing = []
    for d in _expected_tests_dirs():
        rel = str(d.relative_to(ROOT)).replace("\\", "/")
        if rel in dirs or any(rel == p or rel.startswith(p + "/") for p in dirs):
            continue
        if rel in [c.replace("\\", "/") for c in file_covered]:
            continue
        missing.append(rel)
    assert not missing, f"tests/ dirs missing from scripts/run_tests.py PARTS: {missing}"


def test_testpaths_entries_exist() -> None:
    testpaths = _testpaths()
    missing = [t for t in testpaths if not (ROOT / t).is_dir()]
    assert not missing, f"pyproject testpaths point at missing dirs: {missing}"


def test_pyright_include_covers_all_domains() -> None:
    """Pyright must type-check every product domain + scripts.

    Regression test: chart/strategy/backtest were silently excluded from
    pyright scope while errors rotted there (22 found 2026-09-05).
    """
    with open(ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    include = data["tool"]["pyright"]["include"]
    expected = [
        "00_app/app",
        "01_core/core",
        "02_data/data",
        "03_market/market",
        "05_strategy/strategy",
        "06_backtest/backtest",
        "07_risk/risk",
        "08_execution/execution",
        "09_broker/broker",
        "scripts",
    ]
    missing = [e for e in expected if e not in include]
    assert not missing, f"pyproject pyright include missing: {missing}"
