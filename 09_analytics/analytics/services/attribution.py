from typing import Sequence

from analytics.models.attribution import Attribution


class AttributionAnalyzer:
    def calculate_strategy_attribution(self, strategy_returns: dict[str, Sequence[float]]) -> Attribution:
        total_return = 0.0
        contributions: dict[str, float] = {}
        for strategy, returns in strategy_returns.items():
            strat_return = sum(returns) / len(returns) if returns else 0
            contributions[strategy] = strat_return
            total_return += strat_return
        return Attribution(strategy_contributions=contributions, total_return=total_return)
