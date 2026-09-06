"""App — desktop charting application entry point."""

import argparse
import os
import sys
from pathlib import Path

from core.logger import configure_logging, get_logger
from PySide6.QtWidgets import QApplication

from app.bootstrap.bootstrap import Bootstrap

logger = get_logger(__name__)

DEFAULT_DATA_DIR = r"D:\ZerodhaTradingData"
DEFAULT_LIMIT: int | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(prog="vayren", description="Vayren desktop charting platform")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("VAYREN_DATA_DIR", DEFAULT_DATA_DIR),
        help="Folder containing one SQLite database per stock",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of candles to load (default: entire history)",
    )
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument(
        "--describe",
        action="store_true",
        help="Print the architecture snapshot and exit (no window, no event loop)",
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        help="Run a headless paper-trading session (no window, no real orders)",
    )
    parser.add_argument(
        "--paper-symbol", default=None, help="Paper session symbol (default: first in store)"
    )
    parser.add_argument(
        "--paper-strategy", default=None, help="Paper session strategy (default: first in library)"
    )
    parser.add_argument(
        "--paper-timeframe", default=None, help="Paper session timeframe (default: base bars)"
    )
    parser.add_argument(
        "--strategy-dir",
        default=os.environ.get("VAYREN_STRATEGIES", r"D:\VAYREN_STRATEGIES"),
        help="Folder containing strategy .py files",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Live trading (NOT CONFIGURED: always fails closed)",
    )
    parser.add_argument(
        "--check-live",
        action="store_true",
        help="Evaluate live readiness gates without placing any order",
    )
    return parser.parse_args(argv)


class App:
    """Application entry point: bootstrap, start, run the Qt event loop."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int:
        """Run the application and return the process exit code."""
        args = parse_args(argv)
        configure_logging(args.log_level)

        if args.live:
            print("LIVE TRADING NOT CONFIGURED: no live broker adapter is registered.")
            print("Refusing to trade.")
            return 2

        if args.check_live:
            from app.services.paper_service import run_check_live

            return run_check_live(args)

        if args.describe:
            from app.describe import describe_architecture

            print(describe_architecture(args.data_dir, args.limit))
            return 0

        if args.paper:
            from app.services.paper_service import run_paper

            try:
                return run_paper(args)
            except KeyboardInterrupt:
                print("PAPER INTERRUPTED: shutdown clean")
                return 130

        qt_app = QApplication(argv if argv is not None else sys.argv)
        bootstrap = Bootstrap(data_dir=Path(args.data_dir), limit=args.limit)
        try:
            bootstrap.start()
        except Exception:
            logger.exception("Application bootstrap failed")
            return 1
        return qt_app.exec()


def main(argv: list[str] | None = None) -> int:
    """Console-script friendly entry point."""
    return App.main(argv)
