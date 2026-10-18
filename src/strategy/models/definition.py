"""StrategyDefinition — immutable description of a registered strategy."""

import math
from dataclasses import dataclass, replace

from strategy.models.parameters import StrategyParameters

#: The only two operating modes (AI_ENTRY.md / design doc §1). A strategy may
#: declare a subset; the default tuple means "usable in both".
EXECUTION_MODE_LABELS: tuple[str, ...] = ("BACKTEST", "LIVE")


def _labels(value: object, field_name: str) -> tuple[str, ...]:
    """Normalise a declared label tuple, or explain exactly what is wrong.

    Accepts any iterable of non-empty strings (a JSON round-trip hands back a
    list); anything else raises with the offending value named, so an invalid
    declaration is an actionable diagnostic — never a silently dropped one.
    """
    if isinstance(value, str) or not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"{field_name} must be a sequence of labels, got {value!r}")
    labels: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name} entries must be non-empty strings, got {item!r}")
        labels.append(item.strip())
    return tuple(dict.fromkeys(labels))


@dataclass(frozen=True)
class StrategyDefinition:
    """A strategy entry in the registry: identity, kind, parameters, allocation.

    A definition never contains trading logic itself; ``kind`` names the
    registered logic implementation that turns bars into signals.
    ``allocation_pct`` is the configurable portfolio weight (0-100) used by
    a future orchestrator; it does not scale backtest results by itself.

    Attributes:
        id: Stable unique identifier (kebab-case, e.g. ``"sma-crossover"``).
        name: Display name (e.g. ``"SMA Crossover"``).
        version: Strategy version string (e.g. ``"1.0"``).
        kind: Registered logic kind (e.g. ``"sma_crossover"``).
        params: Strategy parameters handed to the logic at run time.
        allocation_pct: Configurable allocation weight in percent (0-100).
        enabled: Whether the strategy participates in runs.

    Contract declarations (additive, every one defaulted so existing
    constructors and stored records keep their exact meaning):

        supported_timeframes: Declared servable timeframes. Empty means "not
            declared" — validation then falls back to what the logic reports.
        required_warmup_bars: Declared minimum leading bars. ``0`` means "not
            declared"; engines use ``max(declared, logic.warmup())``.
        required_capabilities: Broker/data capability names this strategy
            needs before it may trade. Empty means "not declared".
        supported_modes: Subset of :data:`EXECUTION_MODE_LABELS` this
            strategy may run in. Default is both.
        last_error: Diagnostic text for a failed registration/instantiation;
            paired with ``runtime_state == "ERROR"``. Empty means healthy.
    """

    id: str
    name: str
    version: str
    kind: str
    params: StrategyParameters
    allocation_pct: float = 100.0
    enabled: bool = True
    status: str = "ACTIVE"
    timeframe: str = ""
    direction: str = ""
    symbols: tuple[str, ...] = ()
    runtime_state: str = "IDLE"
    description: str = ""
    metadata: tuple[tuple[str, str], ...] = ()
    source_code: str = ""
    supported_timeframes: tuple[str, ...] = ()
    required_warmup_bars: int = 0
    required_capabilities: tuple[str, ...] = ()
    supported_modes: tuple[str, ...] = EXECUTION_MODE_LABELS
    last_error: str = ""

    @property
    def symbol_count(self) -> int:
        """Total symbols in the strategy universe."""
        return len(self.symbols)

    def __post_init__(self) -> None:
        """Reject empty identity fields and out-of-range allocations."""
        for field_name in ("id", "name", "kind"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"StrategyDefinition {field_name} must be a non-empty string")
        try:
            allocation = float(self.allocation_pct)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"allocation_pct must be a number, got {self.allocation_pct!r}"
            ) from exc
        if not math.isfinite(allocation) or not 0.0 <= allocation <= 100.0:
            raise ValueError(f"allocation_pct must be within [0, 100], got {allocation!r}")
        object.__setattr__(
            self, "supported_timeframes", _labels(self.supported_timeframes, "supported_timeframes")
        )
        object.__setattr__(
            self,
            "required_capabilities",
            _labels(self.required_capabilities, "required_capabilities"),
        )
        warmup = self.required_warmup_bars
        if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
            raise ValueError(f"required_warmup_bars must be a non-negative integer, got {warmup!r}")
        modes = _labels(self.supported_modes, "supported_modes")
        unknown = [mode for mode in modes if mode not in EXECUTION_MODE_LABELS]
        if unknown:
            raise ValueError(
                f"supported_modes contains unsupported mode(s) {unknown}; "
                f"valid modes are {list(EXECUTION_MODE_LABELS)}"
            )
        if not modes:
            raise ValueError("supported_modes must declare at least one mode")
        object.__setattr__(self, "supported_modes", modes)

    def with_params(self, params: StrategyParameters) -> "StrategyDefinition":
        """Return a copy with replaced parameters."""
        return replace(self, params=params)

    def with_enabled(self, enabled: bool) -> "StrategyDefinition":
        """Return a copy with a new enabled flag."""
        return replace(self, enabled=enabled)

    def with_allocation(self, allocation_pct: float) -> "StrategyDefinition":
        """Return a copy with a new allocation weight."""
        return replace(self, allocation_pct=allocation_pct)

    def with_symbols(self, symbols: tuple[str, ...]) -> "StrategyDefinition":
        """Return a copy with replaced symbols."""
        return replace(self, symbols=tuple(symbols))

    def with_status(self, status: str) -> "StrategyDefinition":
        """Return a copy with a new status."""
        return replace(self, status=status)

    def with_runtime_state(self, runtime_state: str) -> "StrategyDefinition":
        """Return a copy with a new runtime state."""
        return replace(self, runtime_state=runtime_state)

    def with_last_error(
        self, last_error: str, runtime_state: str = "ERROR"
    ) -> "StrategyDefinition":
        """Return a copy carrying a load/instantiation failure.

        Isolation surface: a strategy that failed to register or instantiate
        keeps its identity and its diagnostic instead of disappearing, and
        the ERROR runtime state keeps it out of every run.
        """
        return replace(self, last_error=str(last_error), runtime_state=runtime_state)

    def with_contract_declaration(self, **changes: object) -> "StrategyDefinition":
        """Return a copy with declared contract fields replaced.

        Explicit keys only — every field the caller does not name is carried
        over verbatim, so a declaration can never silently drop an earlier
        one.
        """
        allowed = {
            "supported_timeframes",
            "required_warmup_bars",
            "required_capabilities",
            "supported_modes",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"not contract declaration fields: {sorted(unknown)}")
        return replace(self, **changes)

    @property
    def metadata_dict(self) -> dict[str, str]:
        """Convert tuple metadata to dict."""
        return dict(self.metadata)

    @property
    def label(self) -> str:
        """Display label: ``NAME vX.Y``."""
        return f"{self.name} v{self.version}"
