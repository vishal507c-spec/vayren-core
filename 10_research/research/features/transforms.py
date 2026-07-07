from typing import Any


class FeatureTransform:
    @staticmethod
    def normalize(values: list[float]) -> list[float]:
        if not values:
            return []
        mn, mx = min(values), max(values)
        if mx == mn:
            return [0.5] * len(values)
        return [(v - mn) / (mx - mn) for v in values]

    @staticmethod
    def returns(prices: list[float]) -> list[float]:
        if len(prices) < 2:
            return []
        return [(prices[i] - prices[i - 1]) / prices[i - 1] for i in range(1, len(prices))]

    @staticmethod
    def log_returns(prices: list[float]) -> list[float]:
        import math
        if len(prices) < 2:
            return []
        return [math.log(prices[i] / prices[i - 1]) for i in range(1, len(prices))]
