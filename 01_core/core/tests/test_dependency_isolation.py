"""Dependency isolation — the universal foundation must be self-contained.

The foundation depends only on the Python standard library and on core
itself: no chapters, no UI framework, no third-party SDKs.
"""

import ast
from pathlib import Path

import pytest

from core.contracts.capability import CapabilityDecl, CapabilityId
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.manifest import ComponentManifest
from core.registry.component_registry import ComponentRegistry

CORE_DIR = Path(__file__).resolve().parents[2] / "core"

BANNED_TOP_LEVELS = {"app", "market", "chart", "PySide6", "httpx", "requests", "numpy", "pandas"}

FOUNDATION_MODULES = [
    "ai/__init__.py",
    "ai/boundary.py",
    "ai/change_simulation.py",
    "ai/context.py",
    "ai/intent.py",
    "ai/memory/__init__.py",
    "ai/memory/engineering.py",
    "ai/memory/performance.py",
    "ai/optimization.py",
    "ai/plan.py",
    "ai/plan_validator.py",
    "ai/providers.py",
    "ai/sandbox.py",
    "contracts/__init__.py",
    "contracts/capability.py",
    "contracts/component.py",
    "contracts/contract.py",
    "contracts/health.py",
    "contracts/manifest.py",
    "registry/__init__.py",
    "registry/capability_registry.py",
    "registry/component_registry.py",
]


@pytest.mark.parametrize("relative", FOUNDATION_MODULES)
def test_foundation_module_imports_only_stdlib_and_core(relative: str) -> None:
    tree = ast.parse((CORE_DIR / relative).read_text(encoding="utf-8"))
    top_levels: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_levels.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_levels.add(node.module.split(".")[0])
    banned = top_levels & BANNED_TOP_LEVELS
    assert banned == set(), f"{relative} imports banned top-level packages: {banned}"


def test_kernel_operates_with_plain_objects() -> None:
    registry = ComponentRegistry()
    manifest = ComponentManifest(
        identity=ComponentId("probe"),
        version=ComponentVersion.parse("1.0.0"),
        type="probe",
        capabilities=(CapabilityDecl(id=CapabilityId("probe.run")),),
    )
    registry.register(manifest, implementations={"probe.run": object()})
    provider = registry.providers("probe.run")[0]
    assert provider.component.name == "probe"
    assert provider.implementation is not None
