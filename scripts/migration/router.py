"""Pre-coding architecture router: request -> domain -> language -> paths.

The router runs BEFORE any source file is touched. It classifies the user
request into an architectural domain, selects the canonical language from
the ownership table (§4), resolves allowed/forbidden paths, attaches the
migration state of covering units, and states validation requirements.

Uncertain input yields ARCHITECTURE DECISION REQUIRED — never a guess and
never a silent Python fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config import LIVE_CRITICAL_PREFIXES, MIGRATION_DIR
from .manifest_store import load_all
from .registry import seed_units
from .scanner import _classify, _load_policy_rules

ARCHITECTURE_MANIFEST_PATH = MIGRATION_DIR / "architecture_manifest.json"

# Behavior phrase -> (domain, canonical language). Mirrors prompt §4 plus
# the §27 examples. Longer, more specific phrases are matched first.
ROUTING_TABLE: tuple[tuple[str, str, str], ...] = (
    # Rust — core/engine/data
    ("event bus", "CORE", "rust"),
    ("eventbus", "CORE", "rust"),
    ("order lifecycle", "EXECUTION", "rust"),
    ("order state", "EXECUTION", "rust"),
    ("session lifecycle", "EXECUTION", "rust"),
    ("order execution", "EXECUTION", "rust"),
    ("risk validation", "RISK", "rust"),
    ("risk check", "RISK", "rust"),
    ("risk gate", "RISK", "rust"),
    ("risk calculation", "RISK", "rust"),
    ("risk configuration model", "RISK", "rust"),
    ("risk engine", "RISK", "rust"),
    ("market data", "MARKET_DATA", "rust"),
    ("candle storage", "MARKET_DATA", "rust"),
    ("timeframe aggregation", "MARKET_DATA", "rust"),
    ("data download", "DATA_PROCESSING", "rust"),
    ("download engine", "DATA_PROCESSING", "rust"),
    ("download worker", "DATA_PROCESSING", "rust"),
    ("sqlite storage", "MARKET_DATA", "rust"),
    ("sqlite", "MARKET_DATA", "rust"),
    ("database", "MARKET_DATA", "rust"),
    ("storage", "MARKET_DATA", "rust"),
    ("persistence", "MARKET_DATA", "rust"),
    ("backtest engine", "BACKTEST", "rust"),
    ("backtest", "BACKTEST", "rust"),
    ("drawdown", "BACKTEST", "rust"),
    ("sharpe", "BACKTEST", "rust"),
    ("equity curve", "BACKTEST", "rust"),
    ("viewport math", "PRESENTATION_MODEL", "rust"),
    ("chart math", "PRESENTATION_MODEL", "rust"),
    ("coordinate transform", "PRESENTATION_MODEL", "rust"),
    ("zoom math", "PRESENTATION_MODEL", "rust"),
    ("pan math", "PRESENTATION_MODEL", "rust"),
    ("aggregation", "MARKET_DATA", "rust"),
    ("orderbook", "EXECUTION", "rust"),
    ("order book", "EXECUTION", "rust"),
    ("broker execution", "EXECUTION", "rust"),
    ("trading face", "EXECUTION", "rust"),
    ("paper trading engine", "EXECUTION", "rust"),
    ("state machine", "EXECUTION", "rust"),
    ("concurrency", "CORE", "rust"),
    ("threading", "CORE", "rust"),
    ("thread", "CORE", "rust"),
    ("performance", "BACKTEST", "rust"),
    ("metrics kernel", "BACKTEST", "rust"),
    ("numerical kernel", "BACKTEST", "rust"),
    ("calculation", "BACKTEST", "rust"),
    ("indicator calculation", "PRESENTATION_MODEL", "rust"),
    ("native backend", "CORE", "rust"),
    ("risk", "RISK", "rust"),
    ("execution", "EXECUTION", "rust"),
    ("order", "EXECUTION", "rust"),
    ("session", "EXECUTION", "rust"),
    ("broker", "EXECUTION", "rust"),
    ("core", "CORE", "rust"),
    ("engine", "CORE", "rust"),
    ("chart", "PRESENTATION_MODEL", "rust"),
    # Slint — native UI
    ("settings screen", "NATIVE_UI", "slint"),
    ("risk settings", "NATIVE_UI", "slint"),
    ("user interface", "NATIVE_UI", "slint"),
    ("layout", "NATIVE_UI", "slint"),
    ("component", "NATIVE_UI", "slint"),
    ("panel", "NATIVE_UI", "slint"),
    ("window", "NATIVE_UI", "slint"),
    ("dialog", "NATIVE_UI", "slint"),
    ("form", "NATIVE_UI", "slint"),
    ("button", "NATIVE_UI", "slint"),
    ("table", "NATIVE_UI", "slint"),
    ("menu", "NATIVE_UI", "slint"),
    ("toolbar", "NATIVE_UI", "slint"),
    ("screen", "NATIVE_UI", "slint"),
    ("bindings", "NATIVE_UI", "slint"),
    ("animation", "NATIVE_UI", "slint"),
    ("interaction", "NATIVE_UI", "slint"),
    ("indicator controls", "NATIVE_UI", "slint"),
    ("chart presentation", "NATIVE_UI", "slint"),
    ("visual presentation", "NATIVE_UI", "slint"),
    ("zoom", "NATIVE_UI", "slint"),
    ("pan", "NATIVE_UI", "slint"),
    ("indicator", "NATIVE_UI", "slint"),
    ("ui", "NATIVE_UI", "slint"),
    # Python — strategy/research/AI
    ("trading strategy", "STRATEGY", "python"),
    ("new strategy", "STRATEGY", "python"),
    ("strategies", "STRATEGY", "python"),
    ("strategy", "STRATEGY", "python"),
    ("research", "RESEARCH", "python"),
    ("machine learning", "AI_ADJACENT", "python"),
    ("experiment", "RESEARCH", "python"),
    ("notebook", "RESEARCH", "python"),
    ("pine", "STRATEGY", "python"),
    ("ai", "AI_ADJACENT", "python"),
    ("ml", "AI_ADJACENT", "python"),
)

_FILE_TOKEN = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:py|rs|slint)\b|(?:\d\d_[a-z]+/[A-Za-z0-9_./-]+)")


@dataclass(frozen=True)
class LayerRouting:
    layer: str
    language: str
    paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class TaskRecord:
    request: str
    detected_domain: str
    detected_behavior: str
    canonical_layer: str
    canonical_language: str
    layers: tuple[LayerRouting, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    migration_status: tuple[str, ...] = ()
    validation_required: tuple[str, ...] = ()
    confidence: str = "low"
    decision_required: bool = False
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "request": self.request,
            "detected_domain": self.detected_domain,
            "detected_behavior": self.detected_behavior,
            "canonical_layer": self.canonical_layer,
            "canonical_language": self.canonical_language,
            "layers": [
                {"layer": layer.layer, "language": layer.language, "paths": list(layer.paths)}
                for layer in self.layers
            ],
            "allowed_paths": list(self.allowed_paths),
            "forbidden_paths": list(self.forbidden_paths),
            "dependencies": list(self.dependencies),
            "migration_status": list(self.migration_status),
            "validation_required": list(self.validation_required),
            "confidence": self.confidence,
            "decision_required": self.decision_required,
            "notes": list(self.notes),
        }


def _domain_rule(domain: str, rules: list[dict]) -> dict | None:
    for rule in rules:
        if str(rule.get("domain", "")) == domain:
            return rule
    return None


def _domain_paths(domain: str, rules: list[dict]) -> tuple[str, ...]:
    rule = _domain_rule(domain, rules)
    if rule is None:
        return ()
    prefixes = rule.get("directory_prefixes", [])
    return tuple(str(prefix) for prefix in prefixes)


def _covering_units(domain: str, rules: list[dict]) -> list:
    prefixes = _domain_paths(domain, rules)
    units = []
    for unit in seed_units():
        if any(unit.python_source.startswith(prefix) for prefix in prefixes):
            units.append(unit)
    return units


def _validation_for(unit_ids: tuple[str, ...]) -> tuple[str, ...]:
    if any(unit_id.startswith(LIVE_CRITICAL_PREFIXES) for unit_id in unit_ids):
        return (
            "unit tests",
            "golden tests",
            "differential parity",
            "failure-path tests",
            "regression tests",
            "shadow validation",
            "explicit promotion",
        )
    if unit_ids:
        return ("unit tests", "parity where applicable", "validators")
    return ("validators",)


def route_request(request: str, files: list[str] | None = None) -> TaskRecord:
    """Classify a request before any file is touched. Never guesses."""
    text = (request or "").strip()
    lowered = text.lower()
    rules = _load_policy_rules()

    mentioned = list(dict.fromkeys(_FILE_TOKEN.findall(text)))
    if files:
        mentioned.extend(path for path in files if path not in mentioned)

    # Strongest signal first: explicitly mentioned files.
    file_votes: list[tuple[str, str]] = []
    for token in mentioned:
        rel = token.lstrip("./").replace("\\", "/")
        domain = _classify(rel, rules)
        if domain in ("TEST", "TOOLING", "UNCLASSIFIED", "EXCLUDED", "UNKNOWN"):
            continue
        rule = _domain_rule(domain, rules)
        language = str(rule.get("required_language", "")).lower() if rule else ""
        if language in ("rust", "rust_slint", "python"):
            file_votes.append((domain, "slint" if language == "rust_slint" else language))

    # Behavior-phrase votes; longer phrases win on overlap.
    votes: list[tuple[str, str]] = []
    for phrase, domain, language in sorted(ROUTING_TABLE, key=lambda row: -len(row[0])):
        if phrase in lowered:
            votes.append((domain, language))

    combined = file_votes + votes
    if not combined:
        return TaskRecord(
            request=text,
            detected_domain="UNKNOWN",
            detected_behavior=text[:120],
            canonical_layer="UNKNOWN",
            canonical_language="unknown",
            confidence="low",
            decision_required=True,
            notes=(
                "ARCHITECTURE DECISION REQUIRED: no domain signal; "
                "do not implement in Python by default.",
            ),
            validation_required=("validators",),
        )

    by_language: dict[str, list[tuple[str, str]]] = {}
    for domain, language in combined:
        by_language.setdefault(language, []).append((domain, language))

    if len(by_language) > 1:
        layers = []
        allowed: list[str] = []
        forbidden: list[str] = []
        deps: list[str] = []
        statuses: list[str] = []
        present = [language for language in ("rust", "slint", "python") if language in by_language]
        for language in present:
            domain = by_language[language][0][0]
            paths = _domain_paths(domain, rules)
            units = _covering_units(domain, rules)
            layers.append(LayerRouting(layer=domain, language=language, paths=paths))
            allowed.extend(paths)
            deps.extend(dep for unit in units for dep in unit.dependencies)
            statuses.extend(f"{unit.unit_id}={_unit_state(unit.unit_id)}" for unit in units)
        all_units = tuple(
            unit.unit_id
            for language in present
            for unit in _covering_units(by_language[language][0][0], rules)
        )
        return TaskRecord(
            request=text,
            detected_domain="+".join(sorted({domain for domain, _ in combined})),
            detected_behavior=text[:120],
            canonical_layer="SPLIT",
            canonical_language="split",
            layers=tuple(layers),
            allowed_paths=tuple(dict.fromkeys(allowed)),
            forbidden_paths=tuple(dict.fromkeys(forbidden)),
            dependencies=tuple(dict.fromkeys(deps)),
            migration_status=tuple(dict.fromkeys(statuses)),
            validation_required=_validation_for(all_units),
            confidence="high" if file_votes else "medium",
            notes=("Mixed feature: split by layer; never force one language.",),
        )

    language = next(iter(by_language))
    # Primary domain: file mention wins, else most specific (longest) phrase.
    domain = file_votes[0][0] if file_votes else by_language[language][0][0]
    paths = _domain_paths(domain, rules)
    units = _covering_units(domain, rules)
    unit_ids = tuple(unit.unit_id for unit in units)
    forbidden: list[str] = []
    for unit in units:
        if unit.python_source and language != "python":
            forbidden.append(unit.python_source + " (implement in " + language + " instead)")
    if language == "slint":
        forbidden.append(
            "04_chart/chart/widgets/, 04_chart/chart/windows/, 00_app/app/ui/ (new Qt UI forbidden)"
        )
    confidence = "high" if (file_votes or sum(1 for _ in combined) >= 2) else "medium"
    return TaskRecord(
        request=text,
        detected_domain=domain,
        detected_behavior=text[:120],
        canonical_layer=domain,
        canonical_language=language,
        layers=(LayerRouting(layer=domain, language=language, paths=paths),),
        allowed_paths=paths,
        forbidden_paths=tuple(forbidden),
        dependencies=tuple(dict.fromkeys(dep for unit in units for dep in unit.dependencies)),
        migration_status=tuple(f"{unit.unit_id}={_unit_state(unit.unit_id)}" for unit in units),
        validation_required=_validation_for(unit_ids),
        confidence=confidence,
        notes=(),
    )


def _unit_state(unit_id: str) -> str:
    try:
        manifests = load_all()
    except Exception:
        return "UNKNOWN"
    manifest = manifests.get(unit_id)
    return manifest.state if manifest is not None else "UNTRACKED"


def _canonical_for_unit(unit, rules: list[dict]) -> tuple[str, str, bool]:
    """Return (canonical_language, canonical_path, python_allowed)."""
    domain = None
    required = ""
    for rule in rules:
        for prefix in rule.get("directory_prefixes", []):
            if unit.python_source.startswith(prefix):
                domain = str(rule.get("domain", ""))
                required = str(rule.get("required_language", ""))
                break
        if domain:
            break
    if required == "PYTHON":
        return "python", unit.python_source, True
    if required == "RUST_SLINT":
        return "slint", "rust/vayren-shell/", False
    if unit.rust_target:
        return "rust", unit.rust_target, False
    if required == "RUST":
        return "rust", "", False
    return "python", unit.python_source, True


def build_architecture_manifest() -> dict:
    """Machine-readable manifest per §21, derived — never hand-maintained."""
    from .validator import BRIDGES

    rules = _load_policy_rules()
    try:
        manifests = load_all()
    except Exception:
        manifests = {}
    bridge_by_unit: dict[str, tuple[str, ...]] = {
        unit: tuple(paths) for unit, paths in BRIDGES.items()
    }
    entries: dict[str, dict] = {}
    for unit in seed_units():
        language, path, python_allowed = _canonical_for_unit(unit, rules)
        manifest = manifests.get(unit.unit_id)
        entries[unit.unit_id] = {
            "domain": next(
                (
                    str(rule.get("domain", ""))
                    for rule in rules
                    for prefix in rule.get("directory_prefixes", [])
                    if unit.python_source.startswith(prefix)
                ),
                "UNKNOWN",
            ),
            "canonical_language": language,
            "canonical_path": path,
            "python_allowed": python_allowed,
            "allowed_paths": [path] if path else [],
            "forbidden_paths": [unit.python_source] if language != "python" else [],
            "migration_state": manifest.state if manifest is not None else "UNTRACKED",
            "dependencies": list(unit.dependencies),
            "bridge": list(bridge_by_unit.get(unit.unit_id, ())),
            "oracle": (
                "scripts/migration/agent/oracles/risk_engine_oracle_v1.py"
                if unit.unit_id == "risk.engine.evaluate"
                else ""
            ),
            "criticality": "live-critical" if unit.live_critical else "standard",
            "validation_requirements": list(_validation_for((unit.unit_id,))),
        }
    return {"version": 1, "units": entries}


def write_architecture_manifest() -> str:
    ARCHITECTURE_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHITECTURE_MANIFEST_PATH.write_text(
        json.dumps(build_architecture_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(ARCHITECTURE_MANIFEST_PATH)


__all__ = [
    "ROUTING_TABLE",
    "TaskRecord",
    "LayerRouting",
    "route_request",
    "build_architecture_manifest",
    "write_architecture_manifest",
    "ARCHITECTURE_MANIFEST_PATH",
]
