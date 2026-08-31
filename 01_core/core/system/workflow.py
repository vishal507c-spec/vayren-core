"""Workflow model — deterministic composition of capabilities.

A workflow declares its inputs, steps (each bound to a capability),
outputs, and constraints. This is the declarative description only; no
execution engine is built yet.
"""

import re
from dataclasses import dataclass

from core.contracts.capability import CapabilityId

_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True)
class WorkflowStep:
    """One step of a workflow, bound to a capability."""

    id: str
    capability: CapabilityId
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not _ID_PATTERN.match(self.id):
            msg = f"Invalid workflow step id: {self.id!r}"
            raise ValueError(msg)


@dataclass(frozen=True)
class Workflow:
    """A deterministic composition of capabilities."""

    id: str
    description: str = ""
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    steps: tuple[WorkflowStep, ...] = ()
    constraints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _ID_PATTERN.match(self.id):
            msg = f"Invalid workflow id: {self.id!r}"
            raise ValueError(msg)
        seen: set[str] = set()
        for step in self.steps:
            if step.id in seen:
                msg = f"Duplicate workflow step id: {step.id}"
                raise ValueError(msg)
            seen.add(step.id)


class WorkflowRegistry:
    """Named registry of workflows."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        """Register a workflow; duplicate ids are rejected."""
        if workflow.id in self._workflows:
            msg = f"Workflow already registered: {workflow.id}"
            raise ValueError(msg)
        self._workflows[workflow.id] = workflow

    def get(self, workflow_id: str) -> Workflow:
        """Return a workflow by id, or raise KeyError."""
        if workflow_id not in self._workflows:
            msg = f"Workflow not found: {workflow_id}"
            raise KeyError(msg)
        return self._workflows[workflow_id]

    def list(self) -> tuple[Workflow, ...]:
        """All registered workflows, ordered by id."""
        return tuple(self._workflows[workflow_id] for workflow_id in sorted(self._workflows))

    def find_by_capability(self, capability: CapabilityId | str) -> tuple[Workflow, ...]:
        """Workflows whose steps reference the given capability, ordered by id."""
        target = str(capability)
        return tuple(
            workflow
            for workflow in self.list()
            if any(step.capability.value == target for step in workflow.steps)
        )

    def has(self, workflow_id: str) -> bool:
        """True if a workflow with this id is registered."""
        return workflow_id in self._workflows

    def __contains__(self, workflow_id: str) -> bool:
        return self.has(workflow_id)

    def __len__(self) -> int:
        return len(self._workflows)
