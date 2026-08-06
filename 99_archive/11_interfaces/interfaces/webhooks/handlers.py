from logging import getLogger

logger = getLogger(__name__)


class WebhookHandler:
    def handle_trade_update(self, payload: dict) -> None:
        logger.info("Trade update received: %s", payload)

    def handle_portfolio_update(self, payload: dict) -> None:
        logger.info("Portfolio update received: %s", payload)
