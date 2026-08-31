"""Component graph — which component depends on which.

Lightweight, domain-specific graph of component dependencies built from
manifests. Supports dependency/dependent lookup, transitive closure, and
cycle detection. No generic graph framework.
"""

from collections import deque

from core.contracts.manifest import ComponentManifest


class ComponentGraph:
    """Component dependency graph: forward (dependencies) and reverse (dependents) edges."""

    def __init__(self, manifests: tuple[ComponentManifest, ...]) -> None:
        self._names: tuple[str, ...] = ()
        self._dependencies: dict[str, tuple[str, ...]] = {}
        self._optional: dict[str, tuple[str, ...]] = {}
        self._dependents: dict[str, tuple[str, ...]] = {}
        referenced = {manifest.identity.name for manifest in manifests}
        hard: dict[str, list[str]] = {name: [] for name in referenced}
        optional: dict[str, list[str]] = {name: [] for name in referenced}
        for manifest in manifests:
            name = manifest.identity.name
            hard[name] = [dep.name for dep in manifest.dependencies]
            optional[name] = [dep.name for dep in manifest.optional_dependencies]
        for edges in hard.values():
            referenced.update(edges)
        for edges in optional.values():
            referenced.update(edges)
        for name in hard:
            hard[name] = [dep for dep in hard[name] if dep in referenced]
            optional[name] = [dep for dep in optional[name] if dep in referenced]
        dependents: dict[str, set[str]] = {name: set() for name in referenced}
        for name, edges in hard.items():
            for dep in edges:
                dependents[dep].add(name)
        for name, edges in optional.items():
            for dep in edges:
                dependents[dep].add(name)
        self._names = tuple(sorted(referenced))
        self._dependencies = {name: tuple(sorted(edges)) for name, edges in hard.items()}
        self._optional = {name: tuple(sorted(edges)) for name, edges in optional.items()}
        self._dependents = {name: tuple(sorted(deps)) for name, deps in dependents.items()}

    def names(self) -> tuple[str, ...]:
        """Every node in the graph: registered components and referenced dependencies."""
        return self._names

    def dependencies(self, name: str) -> tuple[str, ...]:
        """Components this component depends on (hard edges, sorted)."""
        return self._dependencies.get(name, ())

    def optional_dependencies(self, name: str) -> tuple[str, ...]:
        """Components this component optionally depends on (sorted)."""
        return self._optional.get(name, ())

    def dependents(self, name: str) -> tuple[str, ...]:
        """Components that directly depend on this component (sorted)."""
        return self._dependents.get(name, ())

    def transitive_dependents(self, name: str) -> tuple[str, ...]:
        """All components affected transitively when this component changes (sorted)."""
        return self._transitive(name, forward=False)

    def transitive_dependencies(self, name: str) -> tuple[str, ...]:
        """All components this component depends on, directly or transitively (sorted)."""
        return self._transitive(name, forward=True)

    def find_cycle(self) -> tuple[str, ...] | None:
        """Return the first dependency cycle found, or None when the graph is acyclic."""
        outgoing = {name: self._dependencies.get(name, ()) for name in self._names}
        indegree = dict.fromkeys(self._names, 0)
        for edges in outgoing.values():
            for dep in edges:
                indegree[dep] += 1
        queue = deque(sorted(name for name, degree in indegree.items() if degree == 0))
        removed: set[str] = set()
        while queue:
            node = queue.popleft()
            removed.add(node)
            for dep in outgoing[node]:
                indegree[dep] -= 1
                if indegree[dep] == 0 and dep not in removed:
                    queue.append(dep)
        leftover = sorted(set(self._names) - removed)
        remaining = set(leftover)
        for start in leftover:
            path: list[str] = []
            seen: dict[str, int] = {}
            node = start
            while node in remaining:
                if node in seen:
                    cycle = tuple(path[seen[node] :])
                    return cycle if len(cycle) > 1 else None
                seen[node] = len(path)
                path.append(node)
                next_nodes = [dep for dep in outgoing[node] if dep in remaining]
                if not next_nodes:
                    break
                node = next_nodes[0]
        return None

    def _transitive(self, name: str, forward: bool) -> tuple[str, ...]:
        result: set[str] = set()
        frontier = {name}
        while frontier:
            expanded: set[str] = set()
            for node in frontier:
                neighbors = self.dependencies(node) if forward else self.dependents(node)
                for neighbor in neighbors:
                    if neighbor != name and neighbor not in result:
                        result.add(neighbor)
                        expanded.add(neighbor)
            frontier = expanded
        return tuple(sorted(result))
