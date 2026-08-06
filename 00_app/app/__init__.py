"""App — desktop charting application entry point."""

import argparse
import os
import sys
from pathlib import Path

from core.logger import configure_logging, get_logger
from PySide6.QtWidgets import QApplication

from app.bootstrap.bootstrap import Bootstrap

logger = get_logger(__name__)

DEFAULT_DATABASE = "data/vayren.db"
DEFAULT_SYMBOL = "SPY"
DEFAULT_LIMIT = 5000


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(prog="vayren", description="Vayren desktop charting platform")
    parser.add_argument(
        "--symbol", default=os.environ.get("VAYREN_SYMBOL", DEFAULT_SYMBOL), help="Symbol to chart"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get("VAYREN_DB", DEFAULT_DATABASE),
        help="Path to the candle SQLite database",
    )
    parser.add_argument(
        "--limit", type=int, default=DEFAULT_LIMIT, help="Maximum number of candles to load"
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


class App:
    """Application entry point: bootstrap, start, run the Qt event loop."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int:
        """Run the application and return the process exit code."""
        args = parse_args(argv)
        configure_logging(args.log_level)

        database_path = Path(args.db)
        if not database_path.is_file():
            print(f"ERROR: candle database not found: {database_path}", file=sys.stderr)
            return 2

        qt_app = QApplication(argv if argv is not None else sys.argv)
        bootstrap = Bootstrap(database_path=database_path, symbol=args.symbol, limit=args.limit)
        try:
            bootstrap.start()
        except Exception:
            logger.exception("Application bootstrap failed")
            return 1
        return qt_app.exec()


def main(argv: list[str] | None = None) -> int:
    """Console-script friendly entry point."""
    return App.main(argv)
