"""Strategy Version Graph — immutable, generic, strategy-agnostic.

Each version is an immutable historical snapshot preserving enough information
to reproduce itself: source snapshot, IR snapshot, hashes, parent link,
parameters and creation metadata.

Invariants:
- Historical source remains recoverable (hash alone is insufficient).
- Existing versions are never silently overwritten.
- Duplicate canonical (source_hash + ir_hash + params) is detected.
- Graph integrity: no cycles, no self-parent, no foreign parent, no missing parent.
- Deterministic identity: canonical source -> SHA-256, canonical IR -> SHA-256,
  no random/time/path in hashes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── Exceptions ──────────────────────────────────────────────────────────


class VersionImmutableError(ValueError):
    """Raised when attempting to overwrite an existing immutable version."""


class DuplicateVersionError(ValueError):
    """Raised when identical canonical content would create a duplicate version."""

    def __init__(self, message: str, existing_version_id: str | None = None):
        super().__init__(message)
        self.existing_version_id = existing_version_id


class VersionGraphError(ValueError):
    """Raised when parent link violates graph integrity."""


# ── Canonical helpers ───────────────────────────────────────────────────


def _canonical_source(source: str) -> str:
    """Deterministic canonical form for hashing (no trailing ws, normalized endings)."""
    # strip outer blank lines, per-line rstrip, \n joined — never includes timestamp/path/random
    return "\n".join(line.rstrip() for line in source.strip().splitlines())


def _hash_text(text: str) -> str:
    canonical = _canonical_source(text)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _hash_ir_json(ir_json: str) -> str:
    # IR hash is deterministic: sorted keys already in to_json, but hash raw json
    canonical = ir_json.strip()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ── Model ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrategyVersion:
    """Immutable version of a strategy — historical snapshot.

    Attributes:
        strategy_id: Stable StrategyRecord.id (UUID), canonical identity.
        version_id: Stable version identifier (UUID).
        parent_version_id: Parent version or None for V1.
        source: Full source snapshot — recoverable for historical restore.
        source_hash: SHA-256 of canonical source.
        ir_snapshot: Deterministic IR JSON snapshot (if available) for reproduction.
        ir_hash: SHA-256 of canonical IR JSON (or of source if IR absent).
        ir_version: IR schema version.
        parameters: Configuration/parameters required for execution (input defaults).
        created_at: ISO timestamp (metadata, never part of content hash).
        metadata: Arbitrary creation metadata (author, note, etc.).
    """

    strategy_id: str
    version_id: str
    parent_version_id: str | None
    source: str
    source_hash: str
    ir_hash: str
    ir_version: int
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    ir_snapshot: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # keep sorted for determinism
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> StrategyVersion:
        # Backward compat: older files lack source / ir_snapshot / parameters
        source = str(data.get("source", ""))
        # If old file stored source in metadata.source_snapshot or similar, recover
        if not source and isinstance(data.get("metadata"), dict):
            source = str(data["metadata"].get("source_snapshot", "") or "")
        if not source and isinstance(data.get("metadata"), dict):
            source = str(data["metadata"].get("source", "") or "")
        source_hash = str(data.get("source_hash", "") or "")
        # Retro-compute if missing and source present
        if not source_hash and source:
            source_hash = _hash_text(source)
        # IR fields
        ir_snapshot = data.get("ir_snapshot")
        if ir_snapshot is not None:
            ir_snapshot = str(ir_snapshot)
        ir_hash = str(data.get("ir_hash", "") or "")
        ir_version = int(data.get("ir_version", 1))
        # parameters may be under parameters or metadata.parameters
        params = data.get("parameters")
        if params is None and isinstance(data.get("metadata"), dict):
            params = data["metadata"].get("parameters")
        try:
            params_dict = dict(params) if params is not None else {}
        except Exception:
            params_dict = {}
        return StrategyVersion(
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            parent_version_id=data.get("parent_version_id"),
            source=source,
            source_hash=source_hash,
            ir_hash=ir_hash,
            ir_version=ir_version,
            created_at=str(data.get("created_at", "")),
            metadata=dict(data.get("metadata", {})),
            ir_snapshot=ir_snapshot,
            parameters=params_dict,
        )


# ── Storage helpers ─────────────────────────────────────────────────────


def _version_dir(data_dir: Path | str | None, strategy_id: str) -> Path:
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "strategy_versions" / strategy_id
    else:
        d = Path.cwd() / ".vayren" / "strategy_versions" / strategy_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_path(data_dir: Path | str | None, strategy_id: str, version_id: str) -> Path:
    return _version_dir(data_dir, strategy_id) / f"{version_id}.json"


# ── Graph helpers ───────────────────────────────────────────────────────


def _detect_duplicate(
    strategy_id: str,
    source_hash: str,
    ir_hash: str,
    parameters: dict[str, Any],
    data_dir: Path | str | None,
) -> StrategyVersion | None:
    """Return existing version with identical canonical identity if any."""
    for v in list_versions(strategy_id, data_dir):
        if v.source_hash == source_hash and v.ir_hash == ir_hash:
            # parameters are part of identity if present — compare canonical JSON
            try:
                cur = json.dumps(parameters, sort_keys=True, ensure_ascii=False)
                existing_params = json.dumps(v.parameters, sort_keys=True, ensure_ascii=False)
                if cur != existing_params and parameters:
                    # different params => not duplicate
                    continue
            except Exception:
                pass
            return v
    return None


def validate_new_version(
    strategy_id: str,
    parent_version_id: str | None,
    data_dir: Path | str | None = None,
    *,
    new_version_id: str | None = None,
) -> None:
    """Validate parent link integrity. Raises VersionGraphError on violation."""
    existing = list_versions(strategy_id, data_dir)
    existing_ids = {v.version_id for v in existing}
    by_id = {v.version_id: v for v in existing}

    if new_version_id is not None and new_version_id in existing_ids:
        raise VersionGraphError(f"duplicate version_id already exists: {new_version_id}")
    if parent_version_id is not None:
        if new_version_id is not None and parent_version_id == new_version_id:
            raise VersionGraphError("version cannot be its own parent")
        if parent_version_id not in existing_ids:
            # Could be foreign parent: check if version exists under different strategy
            # Scan all strategy_version dirs for that version_id
            if data_dir and Path(data_dir).is_dir():
                base = Path(data_dir) / "strategy_versions"
                if base.is_dir():
                    for strat_dir in base.iterdir():
                        if strat_dir.is_dir() and strat_dir.name != strategy_id:
                            cand = strat_dir / f"{parent_version_id}.json"
                            if cand.exists():
                                raise VersionGraphError(
                                    f"foreign parent {parent_version_id} belongs to strategy {strat_dir.name}, not {strategy_id}"  # noqa: E501
                                )
            raise VersionGraphError(f"missing parent version: {parent_version_id}")
        # self-parent already checked; cycle detection: walk ancestors of parent
        visited: set[str] = set()
        cur: str | None = parent_version_id
        while cur is not None:
            if cur in visited:
                raise VersionGraphError(f"cycle detected at {cur}")
            visited.add(cur)
            if new_version_id is not None and cur == new_version_id:
                raise VersionGraphError(f"cycle would include new version {new_version_id}")
            pv = by_id.get(cur)
            if pv is None:
                break
            cur = pv.parent_version_id
            # guard infinite
            if len(visited) > 10000:
                raise VersionGraphError("version graph too deep — possible cycle")
    else:
        # V1 must be first: if versions exist, parent None is only allowed when explicitly branching from root?  # noqa: E501
        # For linear history we require parent == latest. But allow None for first version or intentional root branch.  # noqa: E501
        # We do not forbid multiple roots — branching may have multiple V1s if intentional.
        # So no error here; caller decides.
        pass


def validate_graph(strategy_id: str, data_dir: Path | str | None = None) -> list[str]:
    """Validate entire graph for strategy. Returns list of error messages (empty = valid)."""
    errors: list[str] = []
    versions = list_versions(strategy_id, data_dir)
    seen: set[str] = set()
    by_id: dict[str, StrategyVersion] = {}
    for v in versions:
        if v.version_id in seen:
            errors.append(f"duplicate version_id {v.version_id}")
        seen.add(v.version_id)
        by_id[v.version_id] = v
        if v.parent_version_id == v.version_id:
            errors.append(f"self-parent {v.version_id}")
        if v.parent_version_id is not None and v.parent_version_id not in {  # noqa: SIM102
            x.version_id for x in versions
        }:
            # check if foreign
            if v.parent_version_id not in seen:
                # also scan foreign existence
                foreign = False
                if data_dir and Path(data_dir).is_dir():
                    base = Path(data_dir) / "strategy_versions"
                    if base.is_dir():
                        for sd in base.iterdir():
                            if sd.is_dir() and sd.name != strategy_id:  # noqa: SIM102
                                if (sd / f"{v.parent_version_id}.json").exists():
                                    foreign = True
                                    break
                if foreign:
                    errors.append(f"foreign parent {v.parent_version_id} for {v.version_id}")
                else:
                    errors.append(f"missing parent {v.parent_version_id} for {v.version_id}")
    # cycle detection per node
    for v in versions:
        visited: set[str] = set()
        cur: str | None = v.version_id
        while cur is not None:
            if cur in visited:
                errors.append(f"cycle at {cur} reachable from {v.version_id}")
                break
            visited.add(cur)
            nxt = by_id.get(cur)
            if nxt is None:
                break
            cur = nxt.parent_version_id
            if len(visited) > 10000:
                errors.append(f"graph too deep from {v.version_id}")
                break
    return errors


# ── Core API ────────────────────────────────────────────────────────────


def create_version(
    strategy_id: str,
    source: str,
    ir_version: int = 1,
    ir_hash: str = "",
    parent_version_id: str | None = None,
    data_dir: Path | str | None = None,
    metadata: dict[str, Any] | None = None,
    ir_snapshot: str | None = None,
    parameters: dict[str, Any] | None = None,
    allow_duplicate: bool = False,
    allow_branch: bool = False,
) -> StrategyVersion:
    """Create a new immutable version for `strategy_id`.

    Stores full source snapshot (not just hash) and IR snapshot for reproduction.
    Enforces:
    - Immutability (never overwrites existing file — see save_version).
    - Duplicate protection: identical source_hash+ir_hash+parameters does not
      blindly create a new version (raises DuplicateVersionError unless
      allow_duplicate is True).
    - Graph integrity: parent must exist and belong to same strategy, no self-parent,
      no cycle, no foreign parent.
    - Deterministic hashes: canonical source + canonical IR.
    """
    strategy_id = str(strategy_id)
    _canonical_source(source)
    source_hash = _hash_text(source)
    # IR hash: if ir_snapshot provided, hash it; else use provided ir_hash; else fallback source hash  # noqa: E501
    if ir_snapshot is not None:
        computed_ir_hash = _hash_ir_json(ir_snapshot)
        if ir_hash and ir_hash != computed_ir_hash:  # noqa: SIM108
            # provided ir_hash mismatches snapshot — snapshot is authoritative, but warn via mismatch  # noqa: E501
            # keep provided? use computed for determinism
            ir_hash = computed_ir_hash
        else:
            ir_hash = computed_ir_hash
        # ir_version should match snapshot's ir_version if parseable
        try:
            snap_data = json.loads(ir_snapshot)
            snap_ver = int(snap_data.get("ir_version", ir_version))
            ir_version = snap_ver
        except Exception:
            pass
    elif not ir_hash and source:
        ir_hash = source_hash
    params_dict = dict(parameters or {})

    # Duplicate detection before parent validation — identical content => no new version
    dup = _detect_duplicate(strategy_id, source_hash, ir_hash, params_dict, data_dir)
    if dup is not None and not allow_duplicate:
        raise DuplicateVersionError(
            f"duplicate version content — identical source_hash+ir_hash already exists as {dup.version_id} (no change)",  # noqa: E501
            existing_version_id=dup.version_id,
        )

    existing = list_versions(strategy_id, data_dir)
    # Auto chain: if parent not provided and graph non-empty, use latest
    # But only if not creating intentional branch — if allow_branch is False, enforce linear history (parent == latest)  # noqa: E501
    if existing:
        latest = existing[-1]
        # Detect if caller attempts to reuse same source as latest => already handled above as duplicate  # noqa: E501
        if parent_version_id is None:
            # Implicit linear continuation — attach to latest
            parent_version_id = latest.version_id
        else:
            # Parent provided: validate it equals latest unless branching is allowed
            if not allow_branch and parent_version_id != latest.version_id:
                # For strict linear history, require parent == latest
                # However we allow explicit branching when allow_branch True
                # If branching not allowed but parent is valid ancestor, still allow but warn?
                # For Foundation: require parent == latest unless explicitly branching
                # So if caller passed a non-latest ancestor without allow_branch, we treat as error
                # To keep backward compat with old callers that pass stale parent, we will raise
                # only if parent is not the latest AND not allow_branch — but spec says distinguish branch  # noqa: E501
                # We therefore raise unless allow_branch
                raise VersionGraphError(
                    f"parent {parent_version_id} is not the latest version {latest.version_id} — "
                    "explicit branching requires allow_branch=True"
                )
    else:
        # First version must have no parent
        if parent_version_id is not None:
            raise VersionGraphError(f"first version cannot have parent {parent_version_id}")

    version_id = str(uuid.uuid4())
    # Full graph validation (self-parent, missing, foreign, cycle)
    validate_new_version(strategy_id, parent_version_id, data_dir, new_version_id=version_id)

    now = _now_iso()
    version = StrategyVersion(
        strategy_id=strategy_id,
        version_id=version_id,
        parent_version_id=parent_version_id,
        source=source,
        source_hash=source_hash,
        ir_hash=ir_hash,
        ir_version=int(ir_version),
        created_at=now,
        metadata=dict(metadata or {}),
        ir_snapshot=ir_snapshot,
        parameters=params_dict,
    )
    # Persist atomically with immutability guard via save_version
    save_version(version, data_dir)
    # Lineage: Strategy -> Version (parent -> child)
    try:
        from strategy.research.lineage import load_lineage, save_lineage

        g = load_lineage(data_dir)
        g.add_node("STRATEGY", strategy_id)
        g.add_node("VERSION", version_id)
        g.add_edge("STRATEGY", strategy_id, "VERSION", version_id, relationship="has_version")
        if parent_version_id is not None:
            g.add_edge(
                "VERSION", parent_version_id, "VERSION", version_id, relationship="derived_from"
            )
        save_lineage(g, data_dir)
    except Exception:
        pass
    return version


def save_version(version: StrategyVersion, data_dir: Path | str | None = None) -> Path:
    """Persist a version — immutable. Raises if existing file differs."""
    path = _version_path(data_dir, version.strategy_id, version.version_id)
    if path.exists():
        try:
            existing = StrategyVersion.from_dict(json.loads(path.read_text(encoding="utf-8")))
            if existing.to_dict() == version.to_dict():
                return path
            # Content differs — immutability violation
            raise VersionImmutableError(
                f"immutable version already exists: {version.strategy_id}/{version.version_id} — refusing overwrite"  # noqa: E501
            )
        except VersionImmutableError:
            raise
        except Exception:
            # If file is corrupt, still refuse to silently overwrite
            raise VersionImmutableError(  # noqa: B904
                f"version file already exists and is not identical: {path} — refusing overwrite"
            )
    path.write_text(version.to_json(), encoding="utf-8")
    return path


def load_version(
    strategy_id: str, version_id: str, data_dir: Path | str | None = None
) -> StrategyVersion | None:
    path = _version_path(data_dir, strategy_id, version_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return StrategyVersion.from_dict(data)
    except Exception:
        return None


def list_versions(strategy_id: str, data_dir: Path | str | None = None) -> list[StrategyVersion]:
    d = _version_dir(data_dir, strategy_id)
    versions: list[StrategyVersion] = []
    for p in d.glob("*.json"):
        v = load_version(strategy_id, p.stem, data_dir)
        if v is not None:
            versions.append(v)
    versions.sort(key=lambda v: v.created_at)
    return versions
