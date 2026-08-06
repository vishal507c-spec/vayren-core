from portfolio.models.portfolio import Portfolio
from portfolio.models.pnl import PnL


class PortfolioTracker:
    def __init__(self) -> None:
        self._pnl_history: dict[str, list[PnL]] = {}

    def update_pnl(self, portfolio_name: str, pnl: PnL) -> None:
        if portfolio_name not in self._pnl_history:
            self._pnl_history[portfolio_name] = []
        self._pnl_history[portfolio_name].append(pnl)

    def get_pnl(self, portfolio_name: str) -> list[PnL]:
        return self._pnl_history.get(portfolio_name, [])
