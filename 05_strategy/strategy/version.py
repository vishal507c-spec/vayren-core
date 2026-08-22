"""Strategy Version Graph — immutable, generic, strategy-agnostic.

Each version is immutable and references its parent, source hash, and IR.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StrategyVersion:
    """Immutable version of a strategy.

    Attributes:
        strategy_id: Stable strategy identifier (e.g., "OBR").
        version_id: Stable version identifier (UUID).
        parent_version_id: Parent version or None for v1.
        source_hash: SHA256 of canonical source.
        ir_hash: SHA256 of canonical IR JSON (if available).
        ir_version: IR schema version.
        created_at: ISO timestamp.
        metadata: Arbitrary generic metadata.
    """

    strategy_id: str
    version_id: str
    parent_version_id: str | None
    source_hash: str
    ir_hash: str
    ir_version: int
    created_at: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> StrategyVersion:
        return StrategyVersion(
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            parent_version_id=data.get("parent_version_id"),
            source_hash=str(data["source_hash"]),
            ir_hash=str(data.get("ir_hash", "")),
            ir_version=int(data.get("ir_version", 1)),
            created_at=str(data["created_at"]),
            metadata=dict(data.get("metadata", {})),
        )


def _hash_text(text: str) -> str:
    canonical = "\n".join(line.rstrip() for line in text.strip().splitlines())
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _version_dir(data_dir: Path | str | None, strategy_id: str) -> Path:
    base = Path(data_dir) if data_dir and Path(data_dir).is_dir() else Path.cwd() / ".vayren"
    # Store versions alongside strategies: data_dir/strategy_versions/<strategy_id>/
    # Fallback to data_dir if not a directory
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "strategy_versions" / strategy_id
    else:
        d = Path.cwd() / ".vayren" / "strategy_versions" / strategy_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_path(data_dir: Path | str | None, strategy_id: str, version_id: str) -> Path:
    return _version_dir(data_dir, strategy_id) / f"{version_id}.json"


def create_version(
    strategy_id: str,
    source: str,
    ir_version: int = 1,
    ir_hash: str = "",
    parent_version_id: str | None = None,
    data_dir: Path | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StrategyVersion:
    """Create a new immutable version for `strategy_id`."""
    source_hash = _hash_text(source)
    # ir_hash is hash of IR if provided, else empty
    if not ir_hash and source:
        # Use source_hash as fallback for ir_hash if no IR yet
        ir_hash = source_hash
    version_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    version = StrategyVersion(
        strategy_id=strategy_id,
        version_id=version_id,
        parent_version_id=parent_version_id,
        source_hash=source_hash,
        ir_hash=ir_hash,
        ir_version=ir_version,
        created_at=now,
        metadata=dict(metadata or {}),
    )
    # Persist
    path = _version_path(data_dir, strategy_id, version_id)
    path.write_text(version.to_json(), encoding="utf-8")
    return version


def save_version(version: StrategyVersion, data_dir: Path | str | None = None) -> Path:
    path = _version_path(data_dir, version.strategy_id, version.version_id)
    path.write_text(version.to_json(), encoding="utf-8")
    return path


def load_version(strategy_id: str, version_id: str, data_dir: Path | str | None = None) -> StrategyVersion | None:
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
    # Sort by created_at
    versions.sort(key=lambda v: v.created_at)
    return versions


def get_version_graph(strategy_id: str, data_dir: Path | str | None = None) -> dict[str, list[str]]:
    """Return parent -> [children] mapping for the version graph."""
    versions = list_versions(strategy_id, data_dir)
    graph: dict[str, list[str]] = {}
    for v in versions:
        parent = v.parent_version_id or "__root__"
        graph.setdefault(parent, []).append(v.version_id)
        graph.setdefault(v.version_id, [])
    return graph
