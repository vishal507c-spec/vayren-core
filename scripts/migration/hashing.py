"""Content hashing and freshness: stale evidence never counts as PASS."""

from __future__ import annotations

import hashlib
from pathlib import Path

from .config import ROOT


def sha256_file(path: Path) -> str:
    """Hex digest of a file; missing files hash as the empty sentinel."""
    if not path.is_file():
        return "missing"
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_paths(rel_paths: list[str]) -> str:
    """Stable combined hash over several repo-relative paths."""
    digest = hashlib.sha256()
    for rel in sorted(rel_paths):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(sha256_file(ROOT / rel).encode("utf-8"))
        digest.update(b"\x00")
    return digest.hexdigest()


def short_hash(full: str) -> str:
    return full[:12] if len(full) >= 12 else full


__all__ = ["sha256_file", "sha256_text", "hash_paths", "short_hash"]
