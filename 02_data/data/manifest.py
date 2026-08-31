"""Historical data component manifest — the real ingest component.

Describes the preserved historical download engine factually: download,
coverage and status capabilities. It never changes runtime behaviour; the
ComponentRegistry and SystemModel read it at startup for discovery and
AI-readable architecture context.

No credentials, API secrets or private configuration values are declared —
only architecture metadata.
"""

from core.contracts.capability import (
    BehavioralRules,
    CapabilityContract,
    CapabilityDecl,
    CapabilityId,
)
from core.contracts.component import ComponentId, ComponentVersion
from core.contracts.contract import ComponentContract
from core.contracts.manifest import ComponentManifest


def data_manifest() -> ComponentManifest:
    """Return the manifest of the real historical-data component."""
    return ComponentManifest(
        identity=ComponentId("historical_data"),
        version=ComponentVersion.parse("1.0.0"),
        type="ingest",
        capabilities=(
            CapabilityDecl(
                id=CapabilityId("historical_data.download"),
                description="download historical OHLCV candles for a symbol, "
                "interval and date range",
                inputs=("symbol: str", "interval: str", "from_date: str", "to_date: str"),
                outputs=("new_rows: int", "db_total: int"),
                contract=CapabilityContract(
                    capability=CapabilityId("historical_data.download"),
                    inputs=("symbol: str", "interval: str", "from_date: str", "to_date: str"),
                    outputs=("new_rows: int", "db_total: int"),
                    rules=BehavioralRules(
                        guarantees=(
                            "idempotent writes",
                            "no fabricated candles",
                            "resumes from database state after interruption",
                        ),
                        failure_modes=(
                            "auth failure reported",
                            "unknown symbol reported",
                            "rate limit stops the run",
                        ),
                    ),
                ),
            ),
            CapabilityDecl(
                id=CapabilityId("historical_data.coverage"),
                description="scan a symbol's candle database and report "
                "historical coverage (state, head/tail gaps, rows)",
                inputs=("symbol: str", "interval: str"),
                outputs=("coverage: SymbolInfo",),
            ),
            CapabilityDecl(
                id=CapabilityId("historical_data.status"),
                description="report provider readiness and engine status",
                inputs=(),
                outputs=("status: dict",),
            ),
        ),
        inputs=("symbol: str", "interval: str", "from_date: str", "to_date: str"),
        outputs=("new_rows: int", "db_total: int", "coverage: SymbolInfo"),
        dependencies=(ComponentId("core"),),
        events_consumed=("DownloadRequest", "CoverageRequest", "CancelDownload"),
        events_produced=(
            "DownloadStarted",
            "DownloadProgress",
            "DownloadCompleted",
            "DownloadFailed",
            "DownloadCoverage",
        ),
        resource_requirements=("sqlite", "file-system", "network"),
        side_effects=(
            "writes-sqlite-candle-files",
            "calls-broker-api",
            "writes-lock-file",
        ),
        contract=ComponentContract(
            component=ComponentId("historical_data"),
            version=ComponentVersion.parse("1.0.0"),
            invariants=(
                "candle databases are the only source of truth",
                "no fabricated candles",
                "schema compatible with market storage (ohlcv table)",
            ),
        ),
    )
