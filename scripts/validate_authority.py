"""Authority + contract + drift enforcement (Phase 6).

Turns architecture rules into deterministic CI invariants: single registries,
single authority per domain symbol, single selection writers, valid public
contracts (``__all__``), bridge import purity, UI token discipline, and
route/policy contract-drift detection. Reuses existing sources (ownership
policy, task routes, retention) instead of duplicating truth.

Every failure uses the AI-actionable format (§11). Pure check functions take
explicit file maps so tests run on tmp fixtures without touching the tree.

Usage: python scripts/validate_authority.py [--json]
Exit: 0 PASS, 1 FAIL, 2 ERROR.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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

# Canonical single registries: class -> defining file (exactly one site).
REGISTRIES = {
    "BrokerRegistry": "09_broker/broker/registry.py",
    "StrategyRegistry": "05_strategy/strategy/registry.py",
    "AiProviderRegistry": "01_core/core/ai/providers.py",
}

# Canonical single authorities: symbol -> defining file (exactly one site).
AUTHORITIES = {
    "ZerodhaProvider": "02_data/data/provider/zerodha/adapter.py",
    "FyersProvider": "02_data/data/provider/fyers/adapter.py",
    "StrategyRegistry": "05_strategy/strategy/registry.py",
    "BrokerRegistry": "09_broker/broker/registry.py",
    "DownloadSettings": "02_data/data/settings.py",
    "BrokerSelection": "09_broker/broker/selection.py",
    "StrategyRuntime": "05_strategy/strategy/runtime.py",
    "build_provider": "02_data/data/provider/factory.py",
    "default_registry": "09_broker/broker/registry.py",
    "compile_strategy": "05_strategy/strategy/language/compiler.py",
    "skeleton_record": "09_broker/broker/adapters/skeleton/__init__.py",
    "BrokerSelectionService": "00_app/app/services/broker_selection_service.py",
}

# Canonical registry seeding points: files allowed to call `.register(`.
REGISTER_WRITERS = {
    "02_data/data/provider/factory.py",
    "02_data/data/provider/zerodha/live_activation.py",
    "09_broker/broker/registry.py",
}

# Canonical selection writers: files allowed to construct BrokerSelection.
# (SelectionStore.load re-materializes on read — a reader, listed here as the
# persistence half of the single write path through the service.)
SELECTION_WRITERS = {
    "09_broker/broker/selection_store.py",
    "00_app/app/services/broker_selection_service.py",
}

# Package surfaces treated as public contracts: must keep __all__.
REQUIRED_ALL = {
    "01_core/core/__init__.py",
    "03_market/market/__init__.py",
    "05_strategy/strategy/__init__.py",
    "09_broker/broker/__init__.py",
}

# Bridge import exceptions beyond stdlib + core + own-domain package.
BRIDGE_IMPORT_EXCEPTIONS = {
    ("06_backtest/backtest/native_validation.py", "strategy"),
}

# Module-level map names that may only live in the canonical registry file.
REGISTRY_MAP_PATTERN = re.compile(r"(REGISTRY|_PROVIDERS|_ADAPTERS|_PLUGINS|_VENUES)$")
REGISTRY_MAP_OWNER = "09_broker/broker/registry.py"

# Forbidden imports/calls inside */native_*.py bridges.
BRIDGE_DENIED_MODULES = {
    "socket",
    "subprocess",
    "threading",
    "sqlite3",
    "requests",
    "httpx",
    "websockets",
    "websocket",
    "urllib",
}
BRIDGE_DENIED_CALLS = {"open", "socket", "Popen", "urlopen"}

# UI token authority: hex color literals live ONLY here.
PALETTE_FILE = "rust/vayren-shell/ui/palette.slint"
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6,8}\b")
TOKEN_DEF = re.compile(r"out\s+property\s+<\w+>\s+([A-Za-z_][\w-]*)\s*:")

# Route symbols with a documented reason to bypass ancestor-__all__.
# build_provider: data.provider surface is intentionally contract-only so the
# engine import never pulls provider SDKs (see provider/__init__ docstring).
DOCUMENTED_SYMBOL_EXCEPTIONS = {
    ("02_data/data/provider/factory.py", "build_provider"),
}

# Top import name owned by each chapter dir (for same-domain bridge imports).
CHAPTER_PACKAGE = {
    "00_app": "app",
    "01_core": "core",
    "02_data": "data",
    "03_market": "market",
    "05_strategy": "strategy",
    "06_backtest": "backtest",
    "07_risk": "risk",
    "08_execution": "execution",
    "09_broker": "broker",
}


@dataclass
class AuthorityViolation:
    rule: str
    domain: str
    file: str
    symbol: str
    reason: str
    canonical: str
    fix: str

    def render(self) -> str:
        return (
            f"  - {self.file}: [{self.rule}] {self.symbol} — {self.reason} "
            f"(canonical: {self.canonical}; fix: {self.fix})"
        )


def _is_test(rel: str) -> bool:
    return "/tests/" in rel or Path(rel).name.startswith("test_")


def _parse(source: str) -> ast.AST | None:
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.ImportFrom, ast.Import)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def _all_names(tree: ast.AST) -> set[str] | None:
    if not isinstance(tree, ast.Module):
        return None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "__all__"
            and isinstance(node.value, (ast.List, ast.Tuple))
        ):
            names: set[str] = set()
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    names.add(elt.value)
            return names
    return None


def check_registries(files: dict[str, str]) -> list[AuthorityViolation]:
    """Single registry per authority; no shadow maps; canonical writers only."""
    out: list[AuthorityViolation] = []
    sites: dict[str, list[str]] = {}
    for rel, source in files.items():
        if _is_test(rel):
            continue
        tree = _parse(source)
        if not isinstance(tree, ast.Module):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name in REGISTRIES:
                sites.setdefault(node.name, []).append(rel)
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and REGISTRY_MAP_PATTERN.search(target.id)
                    and rel != REGISTRY_MAP_OWNER
                ):
                    out.append(
                        AuthorityViolation(
                            rule="shadow-registry-map",
                            domain="REGISTRY",
                            file=rel,
                            symbol=target.id,
                            reason="registry-like map outside the canonical registry file",
                            canonical=REGISTRY_MAP_OWNER,
                            fix=f"move the map into {REGISTRY_MAP_OWNER} or delete it",
                        )
                    )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
            ):
                if rel not in REGISTER_WRITERS:
                    out.append(
                        AuthorityViolation(
                            rule="unauthorized-registry-writer",
                            domain="REGISTRY",
                            file=rel,
                            symbol=".register()",
                            reason="registry mutation outside canonical seeding points",
                            canonical=", ".join(sorted(REGISTER_WRITERS)),
                            fix="register via canonical seeding or extend REGISTER_WRITERS",
                        )
                    )
                break
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in REGISTRIES
                and rel != REGISTRIES[node.func.id]
            ):
                out.append(
                    AuthorityViolation(
                        rule="duplicate-registry-instance",
                        domain="REGISTRY",
                        file=rel,
                        symbol=f"{node.func.id}()",
                        reason="registry constructed outside its canonical holder",
                        canonical=REGISTRIES[node.func.id],
                        fix="use default_registry()/StrategyRegistry via the canonical module",
                    )
                )
    for name, canonical in REGISTRIES.items():
        found = sorted(sites.get(name, []))
        if not found:
            out.append(
                AuthorityViolation(
                    rule="missing-registry",
                    domain="REGISTRY",
                    file=canonical,
                    symbol=name,
                    reason="canonical registry class not defined",
                    canonical=canonical,
                    fix=f"restore class {name} in {canonical}",
                )
            )
        elif found != [canonical]:
            out.append(
                AuthorityViolation(
                    rule="duplicate-registry",
                    domain="REGISTRY",
                    file=", ".join(found),
                    symbol=name,
                    reason="registry class defined in multiple files",
                    canonical=canonical,
                    fix=f"keep exactly one definition in {canonical}",
                )
            )
    return out


def check_authorities(files: dict[str, str]) -> list[AuthorityViolation]:
    """One canonical definition site per authority symbol (§3 output format)."""
    out: list[AuthorityViolation] = []
    sites: dict[str, list[str]] = {}
    for rel, source in files.items():
        if _is_test(rel):
            continue
        tree = _parse(source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and node.name in AUTHORITIES
            ):
                sites.setdefault(node.name, []).append(rel)
    for name, canonical in AUTHORITIES.items():
        found = sorted(set(sites.get(name, [])))
        if not found:
            out.append(
                AuthorityViolation(
                    rule="missing-authority",
                    domain="AUTHORITY",
                    file=canonical,
                    symbol=name,
                    reason="canonical authority symbol not defined",
                    canonical=canonical,
                    fix=f"restore {name} in {canonical}",
                )
            )
        elif found != [canonical]:
            out.append(
                AuthorityViolation(
                    rule="duplicate-authority",
                    domain="AUTHORITY",
                    file=", ".join(found),
                    symbol=name,
                    reason="same authority implemented in multiple files",
                    canonical=canonical,
                    fix=f"keep exactly one implementation in {canonical}",
                )
            )
    return out


def check_selection_writers(files: dict[str, str]) -> list[AuthorityViolation]:
    """BrokerSelection has one write path; no direct `.provider =` writes."""
    out: list[AuthorityViolation] = []
    for rel, source in files.items():
        if _is_test(rel):
            continue
        tree = _parse(source)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "BrokerSelection"
                and rel not in SELECTION_WRITERS
            ):
                out.append(
                    AuthorityViolation(
                        rule="unauthorized-selection-writer",
                        domain="SELECTION",
                        file=rel,
                        symbol="BrokerSelection()",
                        reason="selection constructed outside the single write path",
                        canonical=", ".join(sorted(SELECTION_WRITERS)),
                        fix="construct via BrokerSelectionService or extend writers",
                    )
                )
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "provider":
                        out.append(
                            AuthorityViolation(
                                rule="direct-provider-write",
                                domain="SELECTION",
                                file=rel,
                                symbol=".provider =",
                                reason="provider field written directly instead of via selection",
                                canonical="00_app/app/services/broker_selection_service.py",
                                fix="route the write through BrokerSelectionService",
                            )
                        )
    return out


def check_contracts(files: dict[str, str]) -> list[AuthorityViolation]:
    """__all__ entries resolve; contractual surfaces keep __all__."""
    out: list[AuthorityViolation] = []
    for rel, source in files.items():
        if _is_test(rel):
            continue
        tree = _parse(source)
        if tree is None:
            continue
        exported = _all_names(tree)
        if exported is not None:
            defined = _defined_names(tree)
            for name in sorted(exported - defined):
                out.append(
                    AuthorityViolation(
                        rule="stale-export",
                        domain="CONTRACT",
                        file=rel,
                        symbol=name,
                        reason="__all__ entry with no matching definition/import",
                        canonical=rel,
                        fix=f"remove '{name}' from __all__ or restore its definition",
                    )
                )
        if rel in REQUIRED_ALL and exported is None:
            out.append(
                AuthorityViolation(
                    rule="missing-all",
                    domain="CONTRACT",
                    file=rel,
                    symbol="__all__",
                    reason="public package interface lost its __all__",
                    canonical=rel,
                    fix=f"restore __all__ in {rel}",
                )
            )
    return out


def check_bridges(files: dict[str, str]) -> list[AuthorityViolation]:
    """native_* bridges: restricted imports, no IO/threading/network."""
    out: list[AuthorityViolation] = []
    stdlib = set(sys.stdlib_module_names)
    for rel, source in files.items():
        if _is_test(rel) or Path(rel).name.startswith("test_"):
            continue
        if Path(rel).name.startswith("native_") is False:
            continue
        tree = _parse(source)
        if tree is None:
            continue
        chapter = rel.split("/")[0]
        own_package = CHAPTER_PACKAGE.get(chapter, "")
        allowed_roots = stdlib | {"core", own_package}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots = [node.module.split(".")[0]]
            else:
                continue
            for root in roots:
                if root in allowed_roots:
                    continue
                if (rel, root) in BRIDGE_IMPORT_EXCEPTIONS:
                    continue
                if (
                    root in BRIDGE_DENIED_MODULES
                    or root not in stdlib
                    and root
                    not in {
                        "core",
                        own_package,
                    }
                ):
                    out.append(
                        AuthorityViolation(
                            rule="bridge-import-violation",
                            domain="BRIDGE",
                            file=rel,
                            symbol=root,
                            reason="bridge imports outside stdlib/core/own-domain",
                            canonical="marshal-delegate-return via core.native only",
                            fix="move the dependency out of the bridge",
                        )
                    )
                    break
        for node in ast.walk(tree):
            bad: str | None = None
            if isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top in BRIDGE_DENIED_MODULES:
                    bad = top
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] in BRIDGE_DENIED_MODULES:
                        bad = alias.name
            elif isinstance(node, ast.Call):
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name in BRIDGE_DENIED_CALLS:
                    bad = name
            if bad:
                out.append(
                    AuthorityViolation(
                        rule="bridge-io-violation",
                        domain="BRIDGE",
                        file=rel,
                        symbol=str(bad),
                        reason="IO/threading/network inside the FFI boundary",
                        canonical="pure marshal-delegate-return",
                        fix="move IO out of the bridge into the owning domain",
                    )
                )
                break
    return out


def check_ui_tokens(slint_files: dict[str, str]) -> list[AuthorityViolation]:
    """Hex color literals only in palette.slint; token names unique."""
    out: list[AuthorityViolation] = []
    for rel, text in slint_files.items():
        if rel != PALETTE_FILE and HEX_COLOR.search(text):
            out.append(
                AuthorityViolation(
                    rule="ui-token-violation",
                    domain="NATIVE_UI",
                    file=rel,
                    symbol=HEX_COLOR.search(text).group(0),  # type: ignore[union-attr]
                    reason="hard-coded design token outside the palette",
                    canonical=PALETTE_FILE,
                    fix="use a VayrenPalette role or add the token to palette.slint first",
                )
            )
    palette = slint_files.get(PALETTE_FILE, "")
    names = TOKEN_DEF.findall(palette)
    seen: set[str] = set()
    for name in names:
        if name in seen:
            out.append(
                AuthorityViolation(
                    rule="duplicate-token",
                    domain="NATIVE_UI",
                    file=PALETTE_FILE,
                    symbol=name,
                    reason="palette token defined twice",
                    canonical=PALETTE_FILE,
                    fix=f"keep exactly one definition of '{name}'",
                )
            )
        seen.add(name)
    return out


def check_route_contracts(routes: list[dict], files: dict[str, str]) -> list[AuthorityViolation]:
    """Route symbols resolve in an ancestor package __all__ (contract drift).

    Scoped to the two domains whose package surface is contractual
    (05_strategy, 09_broker): other domains intentionally consume symbols via
    module paths (e.g. data.provider keeps SDKs out of its __init__), and
    Rust symbols have no __init__ mechanism at all.
    """
    out: list[AuthorityViolation] = []
    contract_domains = ("05_strategy/", "09_broker/")
    all_maps: dict[str, set[str]] = {}
    for rel, source in files.items():
        if _is_test(rel):
            continue
        tree = _parse(source)
        if tree is None or not rel.endswith("__init__.py"):
            continue
        exported = _all_names(tree)
        if exported is not None:
            all_maps[rel] = exported
    for route in routes:
        key = str(route.get("key", ""))
        for mod_path, names in route.get("symbols", {}).items():
            if not mod_path.startswith(contract_domains):
                continue
            parts = Path(mod_path).parts
            ancestors = ["/".join(parts[: i + 1]) + "/__init__.py" for i in range(len(parts) - 1)]
            if (mod_path, names[0] if names else "") in DOCUMENTED_SYMBOL_EXCEPTIONS or any(
                (mod_path, n) in DOCUMENTED_SYMBOL_EXCEPTIONS for n in names
            ):
                continue
            union = set()
            for ancestor in ancestors:
                union |= all_maps.get(ancestor, set())
            for name in names:
                if name not in union:
                    out.append(
                        AuthorityViolation(
                            rule="contract-drift",
                            domain="CONTRACT",
                            file=mod_path,
                            symbol=name,
                            reason=f"route '{key}' symbol missing from package __all__",
                            canonical="nearest ancestor package __init__",
                            fix=f"export '{name}' via package __all__ or update task_routes.json",
                        )
                    )
    return out


def check_route_languages(routes: list[dict], policy_rules: list[dict]) -> list[AuthorityViolation]:
    """Route language must agree with ownership policy (drift detection).

    Multi-language routes (+ boundary note, enforced by validate_routes) and
    retained-glue files (tracked in language_retention.json) are exempt —
    only single-language routes contradicting the policy fail here.
    """
    try:
        retention = json.loads(
            (ROOT / "90_brain" / "language_retention.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        retention = {}
    retained = set(retention.get("files", {}).keys())

    def required_language(rel: str) -> str | None:
        for rule in policy_rules:
            for prefix in rule.get("directory_prefixes", ()):
                if rel.startswith(prefix):
                    if any(rel.startswith(sub) for sub in rule.get("excluded_subpaths", ())):
                        return None
                    return rule.get("required_language")
        return None

    out: list[AuthorityViolation] = []
    for route in routes:
        key = str(route.get("key", ""))
        language = str(route.get("language", ""))
        if "+" in language or language not in ("PYTHON", "RUST", "RUST_SLINT"):
            continue
        for rel in route.get("primary_files", []):
            if not rel.endswith(".py"):
                continue  # Rust files: governed by Rust-side checks, not the policy map
            expected = required_language(rel)
            if expected is None or expected in ("PYTHON", "SAME_AS_PARENT", "TEST", "TOOLING"):
                if language != "PYTHON":
                    out.append(
                        AuthorityViolation(
                            rule="route-language-drift",
                            domain="CONTRACT",
                            file=rel,
                            symbol=key,
                            reason=f"route claims {language} but policy says Python-owned",
                            canonical="90_brain/ownership_policy.json",
                            fix="align the route language with the policy or file a policy change",
                        )
                    )
                continue
            if expected in ("RUST", "RUST_SLINT") and language == "PYTHON":
                primaries = [f for f in route.get("primary_files", []) if f.endswith(".py")]
                if primaries and all(f in retained for f in primaries):
                    continue
                out.append(
                    AuthorityViolation(
                        rule="route-language-drift",
                        domain="CONTRACT",
                        file=rel,
                        symbol=key,
                        reason=f"route claims PYTHON but policy requires {expected}",
                        canonical="90_brain/ownership_policy.json",
                        fix="align the route language with the policy or file a policy change",
                    )
                )
    return out


def collect_product_files(root: Path = ROOT) -> dict[str, str]:
    """All non-test product .py sources (single scan shared by every rule)."""
    files: dict[str, str] = {}
    for chapter in CHAPTERS:
        base = root / chapter
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if _is_test(rel) or "__pycache__" in path.parts:
                continue
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    return files


def collect_slint_files(root: Path = ROOT) -> dict[str, str]:
    """All .slint sources under rust/."""
    files: dict[str, str] = {}
    base = root / "rust"
    if base.is_dir():
        for path in sorted(base.rglob("*.slint")):
            rel = path.relative_to(root).as_posix()
            try:
                files[rel] = path.read_text(encoding="utf-8")
            except OSError:
                continue
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate architecture authority invariants")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        files = collect_product_files()
        slint = collect_slint_files()
        routes_path = ROOT / "90_brain" / "task_routes.json"
        routes_data = json.loads(routes_path.read_text(encoding="utf-8"))
        policy_path = ROOT / "90_brain" / "ownership_policy.json"
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError) as exc:
        print(f"Authority validation ERROR: {exc}")
        return 2
    violations: list[AuthorityViolation] = []
    violations.extend(check_registries(files))
    violations.extend(check_authorities(files))
    violations.extend(check_selection_writers(files))
    violations.extend(check_contracts(files))
    violations.extend(check_bridges(files))
    violations.extend(check_ui_tokens(slint))
    violations.extend(check_route_contracts(routes_data.get("routes", []), files))
    route_list = routes_data.get("routes", [])
    violations.extend(check_route_languages(route_list, policy.get("rules", [])))
    if args.json:
        payload = {
            "ok": not violations,
            "violations": [v.__dict__ for v in violations],
        }
        print(json.dumps(payload, indent=2))
    elif violations:
        print("ARCHITECTURE FAIL")
        print("")
        for violation in violations[:30]:
            print(f"rule: {violation.rule}")
            print(f"domain: {violation.domain}")
            print(f"file: {violation.file}")
            print(f"symbol: {violation.symbol}")
            print(f"reason: {violation.reason}")
            print(f"canonical: {violation.canonical}")
            print(f"fix: {violation.fix}")
            print("")
    else:
        print(
            f"Authority validation PASSED "
            f"({len(files)} python files, {len(slint)} slint files, "
            f"{len(routes_data.get('routes', []))} routes checked)"
        )
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
