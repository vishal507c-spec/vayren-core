"""Neuroscience-inspired adaptive layer — engineering architecture only.

No claim is made that this reproduces brain function. Each module earns
its place architecturally: attention (load shedding with counts), working
memory (bounded context), long-term memory (persisted diagnostics),
surprise (expectation monitoring), confidence (execution posture).

Hard boundary: adaptive output is ADVISORY (preferences, posture,
diagnostics). It can never modify risk limits, the kill switch, account
permissions, live enablement, or maximum exposure — no code path exists
from here to any of those.
"""

from execution.adaptive.attention import (
    AttentionConfig,
    AttentionDecision,
    AttentionFilter,
    AttentionSnapshot,
)
from execution.adaptive.confidence import (
    ConfidenceInput,
    ConfidencePolicy,
    ExecutionPosture,
    ExpectationWindow,
    SurpriseMonitor,
)
from execution.adaptive.memory import Incident, LongTermMemory, WorkingMemory

__all__ = [
    "AttentionConfig",
    "AttentionDecision",
    "AttentionFilter",
    "AttentionSnapshot",
    "ExpectationWindow",
    "SurpriseMonitor",
    "ConfidenceInput",
    "ConfidencePolicy",
    "ExecutionPosture",
    "WorkingMemory",
    "LongTermMemory",
    "Incident",
]
