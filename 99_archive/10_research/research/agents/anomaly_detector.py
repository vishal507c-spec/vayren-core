from logging import getLogger

logger = getLogger(__name__)


class AnomalyDetector:
    """AI agent that detects anomalies in market data or system behavior."""

    def __init__(self, threshold: float = 3.0) -> None:
        self._threshold = threshold

    def check(self, values: list[float]) -> list[int]:
        if len(values) < 2:
            return []
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance ** 0.5
        anomalies = [i for i, v in enumerate(values) if abs(v - mean) > self._threshold * std]
        if anomalies:
            logger.warning("Anomalies detected at indices: %s", anomalies)
        return anomalies
