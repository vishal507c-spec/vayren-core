"""Wrong-language change gate (hard gate, no advisory PASS).

Fails when a change adds an undeclared third-party import or adds a
Python file in a Rust-owned domain without a retention entry. Standard
library, first-party chapter roots and declared ``pyproject`` dependencies
(including dev/test tooling) pass. Test files inherit their parent
module's language and are exempt from retention.

Usage: python scripts/validate_architecture_gate.py [--ref HEAD] [--json]
Exit: 0 PASS (warnings allowed), 1 FAIL, 2 ERROR.
"""

from __future__ import annotations

import argparse
import ast
import importlib.metadata
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FIRST_PARTY_ROOTS = frozenset(
    {
        "app",
        "core",
        "data",
        "market",
        "strategy",
        "backtest",
        "risk",
        "execution",
        "broker",
        "scripts",
        "conftest",
    }
)

# Conventional test tooling that is importable in this repo's test
# environment but not pinned as a direct dependency.
TEST_TOOLING = frozenset({"pytest", "hypothesis", "faker", "anyio", "coverage"})


@dataclass
class Violation:
    file: str
    rule: str
    message: str


@dataclass
class GateReport:
    verdict: str = "PASS"
    files_checked: int = 0
    violations: list[Violation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)
    debt_created: int = 0

    def to_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "files_checked": self.files_checked,
            "violations": [v.__dict__ for v in self.violations],
            "warnings": self.warnings,
            "details": self.details,
            "debt_created": self.debt_created,
        }


# NEW LANGUAGE = DENIED (Phase 3 governance). Programming-language extensions
# outside this allowed set fail the gate when introduced as new/changed files.
# Data/config/doc extensions are not languages and always pass; extensionless
# paths (Makefile, LICENSE) pass. Diff-scoped, so the existing tree is
# unaffected — only newly introduced languages trip it.
ALLOWED_LANGUAGE_EXTENSIONS = frozenset({".py", ".rs", ".slint"})

# Known programming-language extensions that are NOT authorized in this repo.
# Anything here as a new/changed file is an unapproved language introduction
# (Phase 3 §2/§5: explicit allowlist change + validator update required first).
UNAUTHORIZED_LANGUAGE_EXTENSIONS = frozenset(
    {
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        ".ts",
        ".tsx",
        ".vue",
        ".svelte",
        ".go",
        ".java",
        ".kt",
        ".kts",
        ".scala",
        ".cs",
        ".rb",
        ".php",
        ".swift",
        ".dart",
        ".r",
        ".jl",
        ".lua",
        ".pl",
        ".zig",
        ".nim",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cc",
        ".cxx",
    }
)


def check_new_file_extension(rel: str) -> Violation | None:
    """Pure new-language guard (Phase 3 §2/§5): unauthorized programming-language
    extension on a new/changed file → Violation; allowed language, data/config/
    doc extension, or extensionless path → None (no false positives)."""
    suffix = Path(rel).suffix.lower()
    if not suffix or suffix in ALLOWED_LANGUAGE_EXTENSIONS:
        return None
    if suffix in UNAUTHORIZED_LANGUAGE_EXTENSIONS:
        return Violation(
            file=rel,
            rule="unauthorized-language",
            message=(
                f"new file uses unauthorized programming language ({suffix}). "
                "Allowed languages: Python (.py), Rust (.rs), Slint (.slint). "
                "A new language needs an explicit allowlist change first."
            ),
        )
    return None


def _git_changed(ref: str) -> tuple[list[str], list[str]]:
    """Changed-tracked and untracked .py files (repo-relative posix)."""

    def run(*args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    tracked = [
        line for line in run("diff", "--name-only", ref).splitlines() if line.endswith(".py")
    ]
    untracked = [
        line
        for line in run("ls-files", "--others", "--exclude-standard").splitlines()
        if line.endswith(".py")
    ]
    return tracked, untracked


def _git_changed_all(ref: str) -> list[str]:
    """All changed-tracked + untracked paths (any extension) for the
    new-language guard. Respects .gitignore via --exclude-standard."""

    def run(*args: str) -> str:
        proc = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc.stdout

    tracked = run("diff", "--name-only", ref).splitlines()
    untracked = run("ls-files", "--others", "--exclude-standard").splitlines()
    return tracked + untracked


def _declared_distributions() -> set[str]:
    """Normalized distribution names from pyproject dependencies."""
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data.get("project", {})
    raw: list[str] = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        raw.extend(extra)

    def normalize(name: str) -> str:
        return (
            name.split(";")[0]
            .split("[")[0]
            .split("=")[0]
            .split("<")[0]
            .split(">")[0]
            .split("!")[0]
            .strip()
            .lower()
            .replace("_", "-")
            .replace(".", "-")
        )

    return {normalize(item) for item in raw if normalize(item)}


def _load_policy() -> tuple[list[dict], dict]:
    policy_path = ROOT / "90_brain" / "ownership_policy.json"
    retention_path = ROOT / "90_brain" / "language_retention.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    retention = json.loads(retention_path.read_text(encoding="utf-8"))
    return policy.get("rules", []), retention.get("files", {})


def _required_language(rel: str, rules: list[dict]) -> str | None:
    """First-match ownership rule (exact same semantics as
    validate_language_ownership._classify_file: first rule whose prefix
    matches owns the path, unless the path falls under that rule's excluded
    subpaths — then no rule owns it. Test/AI files therefore never inherit
    their parent domain's required language."""
    for rule in rules:
        for prefix in rule.get("directory_prefixes", ()):
            if rel.startswith(prefix):
                excluded = rule.get("excluded_subpaths", ())
                if any(rel.startswith(sub) for sub in excluded):
                    return None
                return rule.get("required_language")
    return None


def _is_first_party_root(root: str, local_modules: set[str]) -> bool:
    """Pure check: stdlib, chapter roots, test tooling, or a bare module name
    that resolves to a file inside scripts/ (path-insertion imports in tests).
    Anything else needs a declared distribution (checked by the caller)."""
    return (
        not root
        or root in sys.stdlib_module_names
        or root in FIRST_PARTY_ROOTS
        or root in TEST_TOOLING
        or root in local_modules
    )


def check_changes(ref: str = "HEAD") -> GateReport:
    """Scan changed .py files for undeclared imports and unretained code."""
    report = GateReport()
    try:
        tracked, untracked = _git_changed(ref)
        rules, retained = _load_policy()
        declared = _declared_distributions()
        try:
            dist_map = importlib.metadata.packages_distributions()
        except Exception:  # noqa: BLE001
            dist_map = {}
    except Exception as exc:  # noqa: BLE001
        report.verdict = "ERROR"
        report.details.append(str(exc))
        return report
    # Repo-local bare modules (scripts tooling imported by path-insertion in
    # tests, e.g. `from validate_architecture_gate import ...`) are first-party,
    # not third-party: resolve them against files actually present in scripts/.
    local_modules = {path.stem for path in (ROOT / "scripts").rglob("*.py")}
    try:
        all_changed = _git_changed_all(ref)
    except Exception as exc:  # noqa: BLE001
        report.verdict = "ERROR"
        report.details.append(str(exc))
        return report
    for rel in all_changed:
        if not (ROOT / rel).is_file():
            continue
        violation = check_new_file_extension(rel)
        if violation is not None:
            report.verdict = "FAIL"
            report.violations.append(violation)
            report.files_checked += 1
    changed = [(path, False) for path in tracked] + [(path, True) for path in untracked]
    for rel, is_new in changed:
        path = ROOT / rel
        if not path.is_file():
            continue
        report.files_checked += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError) as exc:
            report.verdict = "ERROR"
            report.details.append(f"{rel}: cannot parse ({exc})")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    continue  # relative import: first-party by construction
                module = node.module or ""
            elif isinstance(node, ast.Import):
                module = node.names[0].name if node.names else ""
            else:
                continue
            root = module.split(".")[0]
            if _is_first_party_root(root, local_modules):
                continue
            if root in TEST_TOOLING:
                continue
            providers = dist_map.get(root, [])
            if any(d.lower().replace("_", "-") in declared for d in providers):
                continue
            report.verdict = "FAIL"
            report.violations.append(
                Violation(
                    file=rel,
                    rule="undeclared-third-party-import",
                    message=f"third-party import not declared in pyproject: {module}",
                )
            )
            break
        if not is_new or "/tests/" in rel.replace("\\", "/"):
            continue
        required = _required_language(rel.replace("\\", "/"), rules)
        if required in ("RUST", "RUST_SLINT") and rel.replace("\\", "/") not in retained:
            report.verdict = "FAIL"
            report.violations.append(
                Violation(
                    file=rel,
                    rule="unretained-rust-domain",
                    message=(
                        f"new file in {required}-owned domain without a "
                        "language_retention.json entry"
                    ),
                )
            )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate change language ownership")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = check_changes(ref=args.ref)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif report.verdict == "PASS":
        print(f"Architecture gate PASSED ({report.files_checked} files checked)")
        for warning in report.warnings[:20]:
            print(f"  warn: {warning}")
        if report.debt_created:
            print(f"  migration debt created: +{report.debt_created} definitions")
    elif report.verdict == "ERROR":
        print("Architecture gate ERROR:")
        for detail in report.details:
            print(f"  - {detail}")
    else:
        print("Architecture gate FAILED:")
        for violation in report.violations:
            print(f"  - {violation.file}: [{violation.rule}] {violation.message}")
    return {"PASS": 0, "FAIL": 1}.get(report.verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
