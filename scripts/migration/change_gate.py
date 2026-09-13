"""Wrong-language change gate (§7): technical enforcement, not advice.

Every changed Python file is checked against the ownership policy and the
migration state system. A Rust-owned behavior implemented in Python FAILS;
so does new Qt UI outside the Slint path. Allowed Python (strategy,
research, AI, bridges, oracles, tests, explicitly retained glue) passes.

Exit semantics: 0 PASS (warnings allowed), 1 FAIL, 2 ERROR.
"""

from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import ROOT
from .manifest_store import load_all
from .scanner import _classify
from .validator import BRIDGES

CANONICAL_FAMILY = (
    "INTEGRATED",
    "RUST_CANONICAL",
    "PYTHON_DEPRECATED",
    "PYTHON_QUARANTINED",
    "PYTHON_REMOVED",
    "FINAL_VERIFIED",
    "MIGRATED",
)

QT_BASES = frozenset(
    {
        "QWidget",
        "QDialog",
        "QMainWindow",
        "QFrame",
        "QLabel",
        "QPushButton",
        "QTableWidget",
        "QTableView",
        "QListWidget",
        "QTreeWidget",
        "QTabWidget",
        "QMenu",
        "QMenuBar",
        "QToolBar",
        "QComboBox",
        "QLineEdit",
        "QTextEdit",
        "QSplitter",
        "QStackedWidget",
        "QScrollArea",
        "QGroupBox",
        "QWizard",
        "QFileDialog",
        "QMessageBox",
    }
)

ORACLE_DIRS = ("scripts/migration/agent/oracles/",)


@dataclass(frozen=True)
class GateViolation:
    file: str
    rule: str
    message: str


@dataclass
class GateReport:
    verdict: str = "PASS"
    violations: list[GateViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debt_created: int = 0
    files_checked: int = 0
    details: list[str] = field(default_factory=list)

    def fail(self, file: str, rule: str, message: str) -> None:
        self.verdict = "FAIL"
        self.violations.append(GateViolation(file, rule, message))

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "violations": [v.__dict__ for v in self.violations],
            "warnings": list(self.warnings),
            "python_migration_debt_created": self.debt_created,
            "files_checked": self.files_checked,
            "details": list(self.details),
        }


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 2, str(exc)
    return proc.returncode, proc.stdout or ""


def git_changed(root: Path = ROOT, ref: str = "HEAD") -> dict[str, list[str]]:
    """Worktree changes vs ref: tracked modifications + untracked additions."""
    code, out = _git(root, "rev-parse", "--is-inside-work-tree")
    if code != 0:
        raise OSError("not a git work tree: " + out.strip()[:200])
    _, modified = _git(root, "diff", "--name-only", ref)
    _, untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    _, deleted = _git(root, "diff", "--name-only", "--diff-filter=D", ref)
    deleted_set = {line.strip() for line in deleted.splitlines() if line.strip()}
    modified_list = [
        line.strip()
        for line in modified.splitlines()
        if line.strip() and line.strip() not in deleted_set
    ]
    return {
        "modified": sorted(modified_list),
        "added": sorted(line.strip() for line in untracked.splitlines() if line.strip()),
        "deleted": sorted(deleted_set),
    }


def _top_level_defs(text: str) -> tuple[set[str], set[str]]:
    """Top-level (function, class) names defined in a module."""
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set(), set()
    funcs, classes = set(), set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        elif isinstance(node, ast.ClassDef):
            classes.add(node.name)
    return funcs, classes


def added_definitions(old_text: str, new_text: str) -> tuple[set[str], set[str]]:
    old_funcs, old_classes = _top_level_defs(old_text)
    new_funcs, new_classes = _top_level_defs(new_text)
    return new_funcs - old_funcs, new_classes - old_classes


def added_qt_subclasses(old_text: str, new_text: str) -> set[str]:
    """Qt widget subclasses introduced by the change (new UI surfaces)."""

    def qt_bases(text: str) -> dict[str, list[str]]:
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return {}
        found: dict[str, list[str]] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = []
            for base in node.bases:
                if isinstance(base, ast.Name):
                    bases.append(base.id)
                elif isinstance(base, ast.Attribute):
                    bases.append(base.attr)
            found[node.name] = bases
        return found

    old, new = qt_bases(old_text), qt_bases(new_text)
    out = set()
    for name, bases in new.items():
        if name in old:
            continue
        if any(base in QT_BASES or base.startswith("Q") for base in bases):
            out.add(name)
    return out


def _read_text(root: Path, rel: str) -> str:
    try:
        return (root / rel).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""


def _head_text(root: Path, rel: str, ref: str = "HEAD") -> str:
    code, out = _git(root, "show", f"{ref}:{rel}")
    return out if code == 0 else ""


def _policy_rules(root: Path) -> list[dict]:
    try:
        policy = json.loads(
            (root / "90_brain" / "ownership_policy.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return []
    rules = policy.get("rules", [])
    return rules if isinstance(rules, list) else []


def _retention(root: Path) -> dict:
    try:
        data = json.loads(
            (root / "90_brain" / "language_retention.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return {}
    files = data.get("files", {})
    return files if isinstance(files, dict) else {}


def _is_bridge(rel: str) -> bool:
    return Path(rel).name.startswith("native_") and rel.endswith(".py")


def _is_oracle(rel: str) -> bool:
    return rel.startswith(ORACLE_DIRS)


def _is_test(rel: str) -> bool:
    return "/tests/" in rel or Path(rel).name.startswith("test_") or rel.endswith("conftest.py")


def _unit_states() -> dict[str, str]:
    try:
        return {uid: manifest.state for uid, manifest in load_all().items()}
    except Exception:
        return {}


def _covering_units(rel: str, states: dict[str, str]) -> list[tuple[str, str]]:
    """Units covering a file: (unit_id, state). Bridges map to their unit."""
    from .registry import seed_units

    out: list[tuple[str, str]] = []
    for unit in seed_units():
        if unit.python_source == rel:
            out.append((unit.unit_id, states.get(unit.unit_id, "UNTRACKED")))
    for unit_id, paths in BRIDGES.items():
        if rel in paths and all(uid != unit_id for uid, _ in out):
            out.append((unit_id, states.get(unit_id, "UNTRACKED")))
    return out


def check_changes(
    root: Path = ROOT,
    ref: str = "HEAD",
    unit_states: dict[str, str] | None = None,
) -> GateReport:
    """Run the wrong-language change gate over the worktree diff."""
    report = GateReport()
    try:
        changed = git_changed(root, ref)
    except OSError as exc:
        report.verdict = "ERROR"
        report.details.append(str(exc))
        return report
    rules = _policy_rules(root)
    retention = _retention(root)
    states = unit_states if unit_states is not None else _unit_states()

    candidates = [
        p for p in changed["modified"] + changed["added"] if p.endswith((".py", ".rs", ".slint"))
    ]
    for rel in candidates:
        if rel.endswith(".rs") or rel.endswith(".slint"):
            report.files_checked += 1
            report.details.append(f"PASS {rel}: canonical {rel.rsplit('.', 1)[-1]} file")
            continue
        domain = _classify(rel, rules)
        if domain in ("TEST", "TOOLING", "UNCLASSIFIED", "EXCLUDED", "UNKNOWN"):
            continue
        rule = next(
            (
                rule
                for rule in rules
                if any(rel.startswith(prefix) for prefix in rule.get("directory_prefixes", []))
                and not any(rel.startswith(exc) for exc in rule.get("excluded_subpaths", []))
            ),
            None,
        )
        required = str(rule.get("required_language", "")) if rule else ""
        if required == "PYTHON":
            continue
        report.files_checked += 1
        is_new = rel in changed["added"]
        old_text = "" if is_new else _head_text(root, rel, ref)
        new_text = _read_text(root, rel)
        added_funcs, added_classes = added_definitions(old_text, new_text)
        added = added_funcs | added_classes

        if required == "RUST_SLINT":
            if is_new and rel not in retention and not _is_test(rel):
                report.fail(
                    rel,
                    "new-python-ui",
                    "New Python UI surface must be Slint (RUST_SLINT owns new UI).",
                )
                continue
            qt_new = added_qt_subclasses(old_text, new_text)
            if qt_new:
                report.fail(
                    rel,
                    "new-qt-widget",
                    f"New Qt widgets {sorted(qt_new)} must be Slint; "
                    "Python UI is forbidden for new surfaces.",
                )
                continue
            if added:
                report.warnings.append(
                    f"{rel}: +{len(added)} definitions in retained Qt file (tracked)."
                )
            report.details.append(f"PASS {rel}: retained UI transition code")
            continue

        if required == "RUST":
            if is_new and rel not in retention and not _is_test(rel) and not _is_oracle(rel):
                report.fail(
                    rel,
                    "new-python-in-rust-domain",
                    "New Python file in a Rust-owned domain without a retention entry.",
                )
                continue
            if _is_bridge(rel) or _is_test(rel) or _is_oracle(rel):
                report.details.append(f"PASS {rel}: bridge/oracle/test exception (§8)")
                continue
            covering = _covering_units(rel, states)
            canonical = [uid for uid, state in covering if state in CANONICAL_FAMILY]
            if canonical and added:
                report.fail(
                    rel,
                    "canonical-python-extension",
                    f"ARCHITECTURE VIOLATION: {sorted(added)} added to Python "
                    f"file of canonical {canonical[0]}; implement in Rust.",
                )
                continue
            if added:
                report.debt_created += len(added)
                report.warnings.append(
                    f"{rel}: +{len(added)} Python definitions in Rust-owned "
                    "domain (migration debt, retained glue)."
                )
            report.details.append(f"PASS {rel}: retained glue within policy")
            continue

        report.details.append(f"PASS {rel}: domain {domain} requires {required or 'nothing'}")
    return report


__all__ = [
    "CANONICAL_FAMILY",
    "GateReport",
    "GateViolation",
    "added_definitions",
    "added_qt_subclasses",
    "check_changes",
    "git_changed",
]
