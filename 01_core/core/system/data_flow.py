"""Data flow model — inputs and outputs of every component.

Input → Component → Output. Workflows express concrete data paths over
these relationships; the model itself is general and domain-agnostic.
"""

from core.contracts.manifest import ComponentManifest
from core.system.workflow import Workflow, WorkflowRegistry


class DataFlowModel:
    """Component input/output declarations and workflow data paths."""

    def __init__(
        self,
        manifests: tuple[ComponentManifest, ...],
        workflows: WorkflowRegistry | None = None,
    ) -> None:
        self._inputs = {
            manifest.identity.name: manifest.inputs for manifest in manifests if manifest.inputs
        }
        self._outputs = {
            manifest.identity.name: manifest.outputs for manifest in manifests if manifest.outputs
        }
        self._workflows = workflows

    def inputs(self, component: str) -> tuple[str, ...]:
        """Inputs declared by one component."""
        return self._inputs.get(component, ())

    def outputs(self, component: str) -> tuple[str, ...]:
        """Outputs declared by one component."""
        return self._outputs.get(component, ())

    def components(self) -> tuple[str, ...]:
        """Components with declared data inputs or outputs, sorted."""
        return tuple(sorted(set(self._inputs) | set(self._outputs)))

    def data_path(self, workflow: Workflow) -> tuple[tuple[str, str, str], ...]:
        """The data path of a workflow: (step, capability, output) triples."""
        return tuple(
            (step.id, step.capability.value, step.outputs[0])
            for step in workflow.steps
            if step.outputs
        )

    def workflow_paths(self) -> tuple[tuple[str, tuple[tuple[str, str, str], ...]], ...]:
        """Data paths of every registered workflow, ordered by workflow id."""
        if self._workflows is None:
            return ()
        return tuple((workflow.id, self.data_path(workflow)) for workflow in self._workflows.list())
