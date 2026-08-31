"""AI Engineering + Evolution layer — deterministic, provider-agnostic.

AI proposes. VAYREN validates. The deterministic runtime executes.
This layer is a pure model/observation layer: it contains no LLM calls and
changes no runtime behavior. AI stays optional.
"""

from core.ai.boundary import (
    ALLOWED_ACTIONS,
    FORBIDDEN_ACTIONS,
    ActionKind,
    AiBoundary,
    BoundaryDecision,
    BoundaryViolation,
)
from core.ai.change_simulation import ChangeSimulation, simulate_plan
from core.ai.context import SECTIONS, AiContext, ContextBuilder, ContextRequest
from core.ai.intent import (
    Intent,
    IntentKind,
    IntentValidationError,
    IntentValidationResult,
    classify,
    parse_intent,
    validate_intent,
)
from core.ai.memory.engineering import Decision, EngineeringEntry, EngineeringMemory
from core.ai.memory.performance import (
    LOWER_IS_BETTER,
    METRICS,
    PerformanceMemory,
    PerformanceRecord,
)
from core.ai.optimization import Candidate, OptimizationError, OptimizationStudy, RankedResult
from core.ai.plan import (
    Plan,
    PlanChange,
    PlanChangeKind,
    PlanRisk,
    PlanValidationError,
    PlanValidationResult,
    Rollback,
    plan_risk,
    risk_rank,
    validate_plan,
)
from core.ai.plan_validator import PlanValidator, Policy
from core.ai.providers import AiProvider, AiProviderRegistry, OfflineProvider
from core.ai.sandbox import Sandbox, SandboxDeployment, SandboxError, SandboxStage

__all__ = [
    "Intent",
    "IntentKind",
    "IntentValidationError",
    "IntentValidationResult",
    "classify",
    "parse_intent",
    "validate_intent",
    "Plan",
    "PlanChange",
    "PlanChangeKind",
    "PlanRisk",
    "PlanValidationError",
    "PlanValidationResult",
    "Rollback",
    "plan_risk",
    "risk_rank",
    "validate_plan",
    "Policy",
    "PlanValidator",
    "ChangeSimulation",
    "simulate_plan",
    "Sandbox",
    "SandboxStage",
    "SandboxDeployment",
    "SandboxError",
    "ActionKind",
    "AiBoundary",
    "BoundaryDecision",
    "BoundaryViolation",
    "ALLOWED_ACTIONS",
    "FORBIDDEN_ACTIONS",
    "EngineeringMemory",
    "EngineeringEntry",
    "Decision",
    "PerformanceMemory",
    "PerformanceRecord",
    "METRICS",
    "LOWER_IS_BETTER",
    "Candidate",
    "OptimizationStudy",
    "OptimizationError",
    "RankedResult",
    "AiProvider",
    "AiProviderRegistry",
    "OfflineProvider",
    "ContextBuilder",
    "ContextRequest",
    "AiContext",
    "SECTIONS",
]
