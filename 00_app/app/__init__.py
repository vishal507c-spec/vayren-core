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
    return parser.parse_args(argv)


class App:
    """Application entry point: bootstrap, start, run the Qt event loop."""

    @staticmethod
    def main(argv: list[str] | None = None) -> int:
        """Run the application and return the process exit code."""
        args = parse_args(argv)
        configure_logging(args.log_level)

        if args.describe:
            from app.describe import describe_architecture

            print(describe_architecture(args.data_dir, args.limit))
            return 0

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
