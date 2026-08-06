from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Report:
    title: str
    content: str = ""
    report_type: str = "daily"
    generated_at: str = ""
    metrics: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.generated_at:
            self.generated_at = datetime.utcnow().isoformat()
