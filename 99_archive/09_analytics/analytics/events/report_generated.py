from dataclasses import dataclass
from analytics.models.report import Report


@dataclass(frozen=True)
class ReportGenerated:
    report: Report
