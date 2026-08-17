"""Engine logging — daily file logger (preserved from the original engine).

The original engine configured a file handler once at import time. Here the
setup is explicit: Bootstrap calls :func:`setup_engine_logging` with the
settings so logs land next to the data. Unit tests leave ``logs_dir`` unset
and rely on the standard logging configuration instead.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime

_ENGINE_LOGGER_NAME = "HistDownloadEngine"

_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
_LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def engine_logger() -> logging.Logger:
    """The shared engine logger (same name as the original engine)."""
    return logging.getLogger(_ENGINE_LOGGER_NAME)


def setup_engine_logging(logs_dir) -> None:
    """Attach a daily file handler (DEBUG) plus a stdout handler (WARNING).

    Idempotent: repeated calls replace the handlers rather than stacking.
    """
    import os

    os.makedirs(logs_dir, exist_ok=True)
    log_file = os.path.join(logs_dir, datetime.now().strftime("%Y-%m-%d") + "_hist_download.log")
    logger = logging.getLogger(_ENGINE_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        logger.handlers.clear()
    formatter = logging.Formatter(_LOG_FORMAT, _LOG_DATE_FORMAT)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.WARNING)
    ch.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(ch)
