"""Lineage Graph — generic, traceable, forward+backward."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Lock
from typing import Any

_LINEAGE_LOCK = Lock()


@dataclass(frozen=True)
class LineageNode:
    node_type: str  # STRATEGY, VERSION, EXECUTION, RESEARCH_RUN, HYPOTHESIS, EXPERIMENT, DISCOVERY, VALIDATION, EVIDENCE, DECISION  # noqa: E501
    node_id: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> LineageNode:
        return LineageNode(node_type=str(data["node_type"]), node_id=str(data["node_id"]))


@dataclass(frozen=True)
class LineageEdge:
    parent_type: str
    parent_id: str
    child_type: str
    child_id: str
    relationship: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> LineageEdge:
        return LineageEdge(
            parent_type=str(data["parent_type"]),
            parent_id=str(data["parent_id"]),
            child_type=str(data["child_type"]),
            child_id=str(data["child_id"]),
            relationship=str(data["relationship"]),
        )


class LineageGraph:
    """Generic lineage graph — in-memory, forward/backward traversal."""

    def __init__(self) -> None:
        self._nodes: dict[tuple[str, str], LineageNode] = {}
        self._edges: list[LineageEdge] = []
        self._forward: dict[tuple[str, str], list[tuple[str, str]]] = {}
        self._backward: dict[tuple[str, str], list[tuple[str, str]]] = {}

    def add_node(self, node_type: str, node_id: str) -> LineageNode:
        key = (node_type, node_id)
        if key not in self._nodes:
            self._nodes[key] = LineageNode(node_type=node_type, node_id=node_id)
        return self._nodes[key]

    def add_edge(
        self,
        parent_type: str,
        parent_id: str,
        child_type: str,
        child_id: str,
        relationship: str = "derived_from",
    ) -> LineageEdge:
        self.add_node(parent_type, parent_id)
        self.add_node(child_type, child_id)
        edge = LineageEdge(
            parent_type=parent_type,
            parent_id=parent_id,
            child_type=child_type,
            child_id=child_id,
            relationship=relationship,
        )
        # Avoid duplicates
        if edge not in self._edges:
            self._edges.append(edge)
            self._forward.setdefault((parent_type, parent_id), []).append((child_type, child_id))
            self._backward.setdefault((child_type, child_id), []).append((parent_type, parent_id))
        return edge

    def forward(self, node_type: str, node_id: str) -> list[tuple[str, str]]:
        """What did this produce?"""
        return list(self._forward.get((node_type, node_id), []))

    def trace_forward(self, node_type: str, node_id: str) -> list[tuple[str, str]]:
        """All descendants (BFS)."""
        visited: set[tuple[str, str]] = set()
        queue: list[tuple[str, str]] = [(node_type, node_id)]
        result: list[tuple[str, str]] = []
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            for child in self._forward.get(cur, []):
                if child not in visited:
                    result.append(child)
                    queue.append(child)
        return result

    def trace_backward(self, node_type: str, node_id: str) -> list[tuple[str, str]]:
        """All ancestors (BFS) — for Evidence → Strategy trace."""
        visited: set[tuple[str, str]] = set()
        queue: list[tuple[str, str]] = [(node_type, node_id)]
        result: list[tuple[str, str]] = []
        while queue:
            cur = queue.pop(0)
            if cur in visited:
                continue
            visited.add(cur)
            for parent in self._backward.get(cur, []):
                if parent not in visited:
                    result.append(parent)
                    queue.append(parent)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "edges": [e.to_dict() for e in self._edges],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> LineageGraph:
        g = LineageGraph()
        for n in data.get("nodes", []):
            g.add_node(n["node_type"], n["node_id"])
        for e in data.get("edges", []):
            g.add_edge(
                e["parent_type"], e["parent_id"], e["child_type"], e["child_id"], e["relationship"]
            )
        return g

    @staticmethod
    def from_json(text: str) -> LineageGraph:
        return LineageGraph.from_dict(json.loads(text))


def _lineage_path(data_dir: Path | str | None) -> Path:
    if data_dir and Path(data_dir).is_dir():
        p = Path(data_dir) / "research" / "lineage.json"
    else:
        p = Path.cwd() / ".vayren" / "research" / "lineage.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_lineage(graph: LineageGraph, data_dir: Path | str | None = None) -> Path:
    """Persist the graph atomically (tmp file + replace) under a lock."""
    p = _lineage_path(data_dir)
    payload = graph.to_json()
    with _LINEAGE_LOCK:
        fd, tmp_name = tempfile.mkstemp(dir=str(p.parent), prefix=".lineage-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, p)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_name)
            raise
    return p


def load_lineage(data_dir: Path | str | None = None) -> LineageGraph:
    """Load the graph; corrupt content raises (never a silent empty graph)."""
    p = _lineage_path(data_dir)
    if not p.exists():
        return LineageGraph()
    with _LINEAGE_LOCK:
        text = p.read_text(encoding="utf-8")
    try:
        return LineageGraph.from_json(text)
    except Exception as exc:
        raise ValueError(f"corrupt lineage file {p} — refusing silent loss") from exc
