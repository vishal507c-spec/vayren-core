"""Analytics & Reporting Domain.

Computes performance metrics, attribution, drawdown analysis, and generates reports.

Public API:
    models: PerformanceMetrics, Attribution, Report
    services: PerformanceAnalyzer, AttributionAnalyzer, ReportGenerator
    events: ReportGenerated
"""

from analytics.models.metrics import PerformanceMetrics
from analytics.models.attribution import Attribution
from analytics.models.report import Report
from analytics.services.performance import PerformanceAnalyzer
from analytics.services.attribution import AttributionAnalyzer
from analytics.services.reporting import ReportGenerator
from analytics.events.report_generated import ReportGenerated

__all__ = [
    "PerformanceMetrics", "Attribution", "Report",
    "PerformanceAnalyzer", "AttributionAnalyzer", "ReportGenerator",
    "ReportGenerated",
]
