from datetime import datetime

from analytics.models.report import Report


class ReportGenerator:
    def generate(self, title: str, metrics: dict[str, float], report_type: str = "daily") -> Report:
        lines = [f"# {title}", f"Generated: {datetime.utcnow().isoformat()}", f"Type: {report_type}", ""]
        for key, value in sorted(metrics.items()):
            lines.append(f"- {key}: {value:.4f}")
        return Report(title=title, content="\n".join(lines), report_type=report_type, metrics=metrics)
