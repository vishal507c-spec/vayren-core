import pytest

from analytics.models.report import Report
from analytics.models.metrics import PerformanceMetrics


@pytest.fixture
def sample_report() -> Report:
    return Report(title="Test Report", report_type="daily")


@pytest.fixture
def sample_metrics() -> PerformanceMetrics:
    return PerformanceMetrics(total_return_pct=10.0, sharpe_ratio=1.5, total_trades=50)
