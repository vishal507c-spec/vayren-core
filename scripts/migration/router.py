"""Pre-coding architecture router: request -> BEHAVIOR -> canonical language.

The router runs BEFORE any source file is touched and classifies a request by
BEHAVIOR OWNERSHIP, never by where the current implementation happens to live.

Routing priority (fixed, generic — never a per-screen special case):

  1. explicit canonical ownership   (the behavior class the request names)
  2. domain ownership               (concrete domain noun, e.g. risk/sqlite)
  3. behavioral ownership           (presentation -> Slint; compute/state -> Rust;
                                      strategy/research/AI -> Python)
  4. existing migration state       (retained Qt surfaces = LEGACY GLUE, not
                                      a canonical Python target)
  5. existing file location         (annotation/fallback ONLY)

An existing Python file never overrides canonical ownership: a UI request that
merely mentions (or lives in) a Python Qt file still routes to Slint. Python is
surfaced for a UI task only as explicitly-labelled legacy glue/bridge.

Uncertain input yields ARCHITECTURE DECISION REQUIRED — never a guess and never
a silent Python fallback.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .config import LIVE_CRITICAL_PREFIXES, MIGRATION_DIR, RETENTION_PATH
from .manifest_store import load_all
from .registry import seed_units
from .scanner import _classify, _load_policy_rules

ARCHITECTURE_MANIFEST_PATH = MIGRATION_DIR / "architecture_manifest.json"

CANONICAL_UI_PATH = "rust/vayren-shell/"
# New native UI never lives in these retained Qt directories (constitution §3).
LEGACY_QT_UI_DIRS = (
    "04_chart/chart/widgets/",
    "04_chart/chart/windows/",
    "00_app/app/ui/",
    "02_data/data/ui/",
    "06_backtest/backtest/ui/",
    "05_strategy/strategy/ui/",
)

# --- behavior cue vocabulary (word-boundary regexes; generic) ---------------

# Presentation / visual / interaction behavior -> NATIVE_UI (Slint).
PRESENTATION_CUES: tuple[str, ...] = (
    "ui",
    "ux",
    "redesign",
    "rebuild ui",
    "visual",
    "layout",
    "style",
    "styling",
    "theme",
    "spacing",
    "typography",
    "font",
    "responsive",
    "dpi",
    "scaling",
    "component",
    "widget",
    "panel",
    "screen",
    "view",
    "dialog",
    "modal",
    "navigation",
    "tab",
    "sidebar",
    "toolbar",
    "menu",
    "button",
    "table",
    "form",
    "interaction",
    "hover",
    "click",
    "polish",
    "render",
    "frontend",
    "presentation",
    "control",
    "look and feel",
    "restyle",
    "skin",
    "aesthetic",
    "zoom",
    "pan",
    "scroll",
    "drag",
    "resize",
)

# Compute / state / performance behavior -> Rust.
RUST_LOGIC_CUES: tuple[str, ...] = (
    "math",
    "kernel",
    "aggregation",
    "aggregate",
    "state machine",
    "order lifecycle",
    "order state",
    "order execution",
    "execution",
    "throughput",
    "latency",
    "performance",
    "optimize",
    "optimise",
    "memory",
    "concurrency",
    "threading",
    "thread",
    "dispatch",
    "event bus",
    "eventbus",
    "storage",
    "persistence",
    "sqlite",
    "database",
    "candle",
    "ohlcv",
    "backtest",
    "metrics",
    "sharpe",
    "drawdown",
    "equity curve",
    "risk validation",
    "risk check",
    "risk gate",
    "risk calculation",
    "risk engine",
    "risk",
    "session lifecycle",
    "viewport math",
    "viewport",
    "zoom",
    "pan",
    "coordinate transform",
    "indicator calculation",
    "order book",
    "orderbook",
    "broker execution",
    "trading face",
    "aggregation kernel",
    "numerical",
    "compute",
)

# Strategy / research / AI logic behavior -> Python (canonical, engine-owned).
PYTHON_LOGIC_CUES: tuple[str, ...] = (
    "trading strategy",
    "strategy logic",
    "strategy calculation",
    "strategy signal",
    "strategy rule",
    "signal logic",
    "entry signal",
    "exit signal",
    "create a strategy",
    "create new strategy",
    "new trading strategy",
    "write a strategy",
    "backtest strategy",
    "optimize strategy",
    "walk forward",
    "pine",
    "machine learning",
    "ai model",
    "ml model",
    "deep learning",
    "feature engineering",
    "train model",
    "experiment",
    "research",
    "notebook",
    "statistical",
)

# Concrete domain nouns -> (domain, language). Longer/more specific first so
# "sqlite storage" beats the generic "optimize".
DOMAIN_NOUNS: tuple[tuple[str, str, str], ...] = (
    ("event bus", "CORE", "rust"),
    ("eventbus", "CORE", "rust"),
    ("sqlite storage", "MARKET_DATA", "rust"),
    ("sqlite", "MARKET_DATA", "rust"),
    ("candle storage", "MARKET_DATA", "rust"),
    ("ohlcv", "MARKET_DATA", "rust"),
    ("storage", "MARKET_DATA", "rust"),
    ("persistence", "MARKET_DATA", "rust"),
    ("database", "MARKET_DATA", "rust"),
    ("timeframe aggregation", "MARKET_DATA", "rust"),
    ("aggregation", "MARKET_DATA", "rust"),
    ("data download", "DATA_PROCESSING", "rust"),
    ("download engine", "DATA_PROCESSING", "rust"),
    ("download worker", "DATA_PROCESSING", "rust"),
    ("order lifecycle", "EXECUTION", "rust"),
    ("order state", "EXECUTION", "rust"),
    ("order execution", "EXECUTION", "rust"),
    ("order book", "EXECUTION", "rust"),
    ("session lifecycle", "EXECUTION", "rust"),
    ("broker execution", "EXECUTION", "rust"),
    ("execution", "EXECUTION", "rust"),
    ("risk validation", "RISK", "rust"),
    ("risk calculation", "RISK", "rust"),
    ("risk engine", "RISK", "rust"),
    ("risk", "RISK", "rust"),
    ("backtest engine", "BACKTEST", "rust"),
    ("backtest", "BACKTEST", "rust"),
    ("sharpe", "BACKTEST", "rust"),
    ("drawdown", "BACKTEST", "rust"),
    ("equity curve", "BACKTEST", "rust"),
    ("metrics", "BACKTEST", "rust"),
    ("viewport math", "PRESENTATION_MODEL", "rust"),
    ("viewport", "PRESENTATION_MODEL", "rust"),
    ("coordinate transform", "PRESENTATION_MODEL", "rust"),
    ("indicator calculation", "PRESENTATION_MODEL", "rust"),
    ("chart", "PRESENTATION_MODEL", "rust"),
    ("trading strategy", "STRATEGY", "python"),
    ("strategy logic", "STRATEGY", "python"),
    ("strategy calculation", "STRATEGY", "python"),
    ("strategy signal", "STRATEGY", "python"),
    ("pine", "STRATEGY", "python"),
    ("walk forward", "STRATEGY", "python"),
    ("machine learning", "AI_ADJACENT", "python"),
    ("ai model", "AI_ADJACENT", "python"),
    ("ml model", "AI_ADJACENT", "python"),
    ("deep learning", "AI_ADJACENT", "python"),
    ("experiment", "RESEARCH", "python"),
    ("research", "RESEARCH", "python"),
    ("notebook", "RESEARCH", "python"),
    ("statistical", "RESEARCH", "python"),
)

# Product surface names. These are UI SCREENS, never logic domains: a surface
# word (e.g. "strategy lab") must not masquerade as its data/logic domain.
SURFACE_PHRASES: tuple[str, ...] = (
    "strategy lab",
    "strategy workspace",
    "strategy workstation",
    "the lab",
    "research lab",
    "command center",
)

_FILE_TOKEN = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:py|rs|slint)\b|(?:\d\d_[a-z]+/[A-Za-z0-9_./-]+)")

# Words that qualify an application-state/performance concern (Rust secondary on
# a presentation request).
STATE_CUES: tuple[str, ...] = (
    "state",
    "sync",
    "synchronization",
    "cache",
    "persist",
    "session",
    "application state",
    "live data",
    "refresh",
    "stream",
)


@dataclass(frozen=True)
class LayerRouting:
    layer: str
    language: str
    paths: tuple[str, ...] = ()
    role: str = "canonical"  # canonical | legacy_glue


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
    python_role: str = "none"  # none | canonical | legacy_glue

    def to_dict(self) -> dict:
        return {
            "request": self.request,
            "detected_domain": self.detected_domain,
            "detected_behavior": self.detected_behavior,
            "canonical_layer": self.canonical_layer,
            "canonical_language": self.canonical_language,
            "python_role": self.python_role,
            "layers": [
                {
                    "layer": layer.layer,
                    "language": layer.language,
                    "paths": list(layer.paths),
                    "role": layer.role,
                }
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


# --- helpers -----------------------------------------------------------------


def _has_word(text: str, cue: str) -> bool:
    if " " in cue or "-" in cue:
        return cue in text
    return re.search(rf"\b{re.escape(cue)}\b", text) is not None


def _any_cue(text: str, cues: tuple[str, ...]) -> bool:
    return any(_has_word(text, cue) for cue in cues)


def _scrub_surfaces(text: str) -> str:
    scrubbed = text
    for phrase in SURFACE_PHRASES:
        scrubbed = scrubbed.replace(phrase, " ")
    return scrubbed


def _domain_rule(domain: str, rules: list[dict]) -> dict | None:
    for rule in rules:
        if str(rule.get("domain", "")) == domain:
            return rule
    return None


def _domain_paths(domain: str, rules: list[dict]) -> tuple[str, ...]:
    rule = _domain_rule(domain, rules)
    if rule is None:
        return ()
    return tuple(str(prefix) for prefix in rule.get("directory_prefixes", []))


def _covering_units(domain: str, rules: list[dict]) -> list:
    prefixes = _domain_paths(domain, rules)
    return [
        unit
        for unit in seed_units()
        if any(unit.python_source.startswith(prefix) for prefix in prefixes)
    ]


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


def _retention_ui_files() -> dict[str, dict]:
    """Retained Qt UI files (path -> entry) keyed by derived surface tokens.

    Derived from ``language_retention.json`` — never a hand-maintained list.
    """
    try:
        data = json.loads(RETENTION_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[str, dict] = {}
    files = data.get("files", {})
    if not isinstance(files, dict):
        return {}
    for path, entry in files.items():
        meta = entry if isinstance(entry, dict) else {"state": "TEMPORARILY_RETAINED"}
        if str(meta.get("domain", "")) != "NATIVE_UI":
            continue
        if not str(path).endswith(".py"):
            continue
        out[str(path)] = dict(meta)
    return out


_GENERIC_TOKENS = frozenset(
    {
        "workspace",
        "panel",
        "view",
        "screen",
        "table",
        "form",
        "button",
        "menu",
        "bar",
        "widget",
        "window",
        "component",
        "dialog",
    }
)


def _matching_glue_files(text: str) -> list[tuple[str, dict]]:
    """Existing retained Qt files matched by a SCREEN-SPECIFIC token.

    Generic tokens (workspace/panel/...) never match alone — otherwise a
    Strategy Lab request would falsely list every workspace file. Ranked by
    specificity so the most exact screen leads.
    """
    scored: list[tuple[int, str, dict]] = []
    for path, entry in _retention_ui_files().items():
        stem = path.rsplit("/", 1)[-1].removesuffix(".py")
        tokens = {tok for tok in re.split(r"[_/]", stem) if len(tok) > 3}
        if "strategy_lab" in path:
            tokens |= {"strategy lab", "lab", "strategy"}
        matched = {tok for tok in tokens if tok in text}
        specific = {tok for tok in matched if tok not in _GENERIC_TOKENS}
        if not specific:
            continue
        score = len(specific) + (2 if any(" " in tok for tok in specific) else 0)
        scored.append((score, path, entry))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [(path, entry) for _score, path, entry in scored]


def _domain_from_nouns(text: str) -> tuple[str, str] | None:
    for noun, domain, language in DOMAIN_NOUNS:
        if _has_word(text, noun):
            return domain, language
    return None


def _file_domain_votes(text: str, files: list[str], rules: list[dict]) -> list[tuple[str, str]]:
    votes: list[tuple[str, str]] = []
    mentioned = list(dict.fromkeys(_FILE_TOKEN.findall(text)))
    mentioned += [p for p in files if p not in mentioned]
    for token in mentioned:
        rel = token.lstrip("./").replace("\\", "/")
        domain = _classify(rel, rules)
        if domain in ("TEST", "TOOLING", "UNCLASSIFIED", "EXCLUDED", "UNKNOWN"):
            continue
        rule = _domain_rule(domain, rules)
        required = str(rule.get("required_language", "")) if rule else ""
        if required == "RUST_SLINT":
            votes.append((domain, "slint"))
        elif required == "RUST":
            votes.append((domain, "rust"))
        elif required == "PYTHON":
            votes.append((domain, "python"))
    return votes


# --- the router --------------------------------------------------------------


def route_request(request: str, files: list[str] | None = None) -> TaskRecord:
    """Classify a request by behavior BEFORE any file is touched."""
    text = (request or "").strip()
    lowered = text.lower()
    rules = _load_policy_rules()

    # Behavior signals are computed on surface-scrubbed text so a product
    # screen name (e.g. "strategy lab") can never vote as its logic domain.
    logic_text = _scrub_surfaces(lowered)
    presentation = _any_cue(lowered, PRESENTATION_CUES)
    rust_logic = _any_cue(logic_text, RUST_LOGIC_CUES)
    python_logic = _any_cue(logic_text, PYTHON_LOGIC_CUES)
    state_cue = _any_cue(lowered, STATE_CUES)

    file_votes = _file_domain_votes(lowered, files or [], rules)
    noun_domain = _domain_from_nouns(logic_text) or _domain_from_nouns(lowered)

    # ---- Tier 1/3: presentation behavior owns the screen -> Slint ----------
    if presentation:
        return _route_presentation(
            text,
            lowered,
            logic_text,
            rules,
            noun_domain,
            rust_logic or state_cue,
            python_logic,
        )

    # ---- Tier 2: pure logic/compute behavior --------------------------------
    if python_logic and not rust_logic:
        py_domain = "STRATEGY" if "strateg" in logic_text else "RESEARCH"
        return _route_single(text, py_domain, "python", rules, file_votes)
    if rust_logic:
        domain = (
            noun_domain[0]
            if noun_domain and noun_domain[1] == "rust"
            else (noun_domain[0] if noun_domain else _domain_from_rust_generic(text))
        )
        return _route_single(text, domain, "rust", rules, file_votes)

    # ---- Tier 5: no behavior signal — fall back to file location only -------
    if file_votes:
        domain, language = file_votes[0]
        return _route_single(text, domain, language, rules, file_votes)

    return TaskRecord(
        request=text,
        detected_domain="UNKNOWN",
        detected_behavior=text[:120],
        canonical_layer="UNKNOWN",
        canonical_language="unknown",
        confidence="low",
        decision_required=True,
        notes=(
            "ARCHITECTURE DECISION REQUIRED: no behavior signal; "
            "do not implement in Python by default.",
        ),
        validation_required=("validators",),
    )


def _domain_from_rust_generic(text: str) -> str:
    # A compute/state request on a UI surface with no concrete engine noun is a
    # Rust-owned application backend concern.
    if "chart" in text:
        return "PRESENTATION_MODEL"
    return "CORE"


def _route_presentation(
    text: str,
    lowered: str,
    logic_text: str,
    rules: list[dict],
    noun_domain: tuple[str, str] | None,
    rust_secondary: bool,
    python_logic: bool,
) -> TaskRecord:
    allowed = [CANONICAL_UI_PATH]
    forbidden: list[str] = []
    layers = [LayerRouting("NATIVE_UI", "slint", (CANONICAL_UI_PATH,), "canonical")]
    allowed_forbidden_qt = ", ".join(f"{d} (new Qt UI forbidden)" for d in LEGACY_QT_UI_DIRS)
    forbidden.append(allowed_forbidden_qt)

    notes: list[str] = []
    if rust_secondary:
        r_domain = noun_domain[0] if noun_domain and noun_domain[1] == "rust" else "CORE"
        r_paths = _domain_paths(r_domain, rules) or ("rust/vayren-core/src/",)
        layers.append(LayerRouting(r_domain, "rust", tuple(r_paths), "canonical"))
        notes.append(
            "UI backend state/performance is Rust-owned: put view-model/state "
            "logic in Rust, keep Slint declarative."
        )
    if python_logic:
        p_domain = "STRATEGY" if "strateg" in logic_text else "RESEARCH"
        layers.append(LayerRouting(p_domain, "python", _domain_paths(p_domain, rules), "canonical"))
        notes.append(
            "A Python layer is canonical only for the strategy/research ENGINE "
            "(05_strategy/), never for this UI surface."
        )

    # Existing retained Qt surface for this screen = LEGACY GLUE, not canonical.
    glue = _matching_glue_files(lowered) or [
        (path, entry)
        for path, entry in _retention_ui_files().items()
        if any(d in path for d in LEGACY_QT_UI_DIRS) and _mentions_surface(lowered, path)
    ]
    python_role = "canonical" if python_logic else "legacy_glue"
    for path, _entry in glue:
        forbidden.append(f"{path} (LEGACY GLUE / existing Qt — not a canonical target)")
    if glue:
        reason = entry_reason(glue[0][1])
        notes.append(
            "PYTHON = LEGACY GLUE / BRIDGE: the existing Qt surface "
            f"({glue[0][0]}) is retained only ({reason}); new UI must be "
            f"implemented in {CANONICAL_UI_PATH} (Slint), never in Python."
        )
    else:
        notes.append(
            f"PYTHON not canonical for this UI work; native UI is Rust+Slint ({CANONICAL_UI_PATH})."
        )

    domains = {layer.layer for layer in layers}
    split = len({layer.language for layer in layers}) > 1
    return TaskRecord(
        request=text,
        detected_domain="+".join(sorted(domains)),
        detected_behavior=text[:120],
        canonical_layer="SPLIT" if split else "NATIVE_UI",
        canonical_language="split" if split else "slint",
        layers=tuple(layers),
        allowed_paths=tuple(dict.fromkeys(allowed)),
        forbidden_paths=tuple(dict.fromkeys(forbidden)),
        dependencies=(),
        migration_status=tuple(
            f"{path.split('/')[-1]}={entry.get('state', 'TEMPORARILY_RETAINED')}"
            for path, entry in glue
        ),
        validation_required=("slint layout tests", "responsive/DPI render", "validators"),
        confidence="high",
        notes=tuple(notes),
        python_role=python_role,
    )


def _mentions_surface(lowered: str, path: str) -> bool:
    stem = path.rsplit("/", 1)[-1].removesuffix(".py")
    tokens = [tok for tok in re.split(r"[_/]", stem) if len(tok) > 3]
    return any(tok in lowered for tok in tokens)


def entry_reason(entry: dict) -> str:
    reason = str(entry.get("reason", "")).strip()
    return reason or "retained until the Slint migration covers it"


def _route_single(
    text: str, domain: str, language: str, rules: list[dict], file_votes: list[tuple[str, str]]
) -> TaskRecord:
    paths = _domain_paths(domain, rules)
    if language == "slint":
        paths = (CANONICAL_UI_PATH,)
    units = _covering_units(domain, rules)
    unit_ids = tuple(unit.unit_id for unit in units)
    forbidden: list[str] = []
    if language != "python":
        for unit in units:
            if unit.python_source:
                forbidden.append(f"{unit.python_source} (implement in {language} instead)")
    if language == "slint":
        forbidden.append(", ".join(f"{d} (new Qt UI forbidden)" for d in LEGACY_QT_UI_DIRS))
    has_verb = bool(file_votes) or _domain_from_nouns(text.lower())
    return TaskRecord(
        request=text,
        detected_domain=domain,
        detected_behavior=text[:120],
        canonical_layer=domain,
        canonical_language=language,
        layers=(LayerRouting(domain, language, tuple(paths), "canonical"),),
        allowed_paths=tuple(paths),
        forbidden_paths=tuple(dict.fromkeys(forbidden)),
        dependencies=tuple(dict.fromkeys(dep for unit in units for dep in unit.dependencies)),
        migration_status=tuple(f"{u}={_unit_state(u)}" for u in unit_ids),
        validation_required=_validation_for(unit_ids),
        confidence="high" if has_verb else "medium",
        notes=(),
        python_role="canonical" if language == "python" else "none",
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


def build_screens_manifest(rules: list[dict] | None = None) -> dict:
    """Per-screen canonical ownership derived from policy + retention (§8).

    Native UI screens are canonical Slint (secondary Rust for backed state);
    every existing Qt Python surface is recorded as LEGACY GLUE, never canonical.
    """
    rules = rules or _load_policy_rules()
    screens: dict[str, dict] = {}
    for path, entry in sorted(_retention_ui_files().items()):
        key = path.rsplit("/", 1)[-1].removesuffix(".py")
        screens[key] = {
            "domain": "NATIVE_UI",
            "canonical_language": "slint",
            "secondary_language": "rust",
            "canonical_path": CANONICAL_UI_PATH,
            "python": {
                "role": "legacy_glue",
                "file": path,
                "state": str(entry.get("state", "TEMPORARILY_RETAINED")),
                "reason": entry_reason(entry),
                "migration_target": str(entry.get("migration_target", CANONICAL_UI_PATH)),
            },
            "forbidden_as_canonical": [path],
        }
    return screens


def build_architecture_manifest() -> dict:
    """Machine-readable manifest, derived — never hand-maintained."""
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
    return {"version": 2, "units": entries, "screens": build_screens_manifest(rules)}


def write_architecture_manifest() -> str:
    ARCHITECTURE_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHITECTURE_MANIFEST_PATH.write_text(
        json.dumps(build_architecture_manifest(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return str(ARCHITECTURE_MANIFEST_PATH)


__all__ = [
    "PRESENTATION_CUES",
    "RUST_LOGIC_CUES",
    "PYTHON_LOGIC_CUES",
    "SURFACE_PHRASES",
    "ROUTING_TABLE",
    "TaskRecord",
    "LayerRouting",
    "route_request",
    "build_architecture_manifest",
    "build_screens_manifest",
    "write_architecture_manifest",
    "ARCHITECTURE_MANIFEST_PATH",
]

# Backwards-compatible alias (some callers import ROUTING_TABLE): behavior-cue
# pairs retained conceptually but the router no longer votes on bare nouns.
ROUTING_TABLE: tuple[tuple[str, str, str], ...] = tuple(
    (noun, domain, language) for noun, domain, language in DOMAIN_NOUNS
)
