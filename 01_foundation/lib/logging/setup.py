import logging
import sys
from typing import Optional


def configure_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure root logger with standard format."""
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]

    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=fmt,
        handlers=handlers,
    )


def get_logger(name: str) -> logging.Logger:
    """Get a named logger.

    Usage:
        logger = get_logger(__name__)
        logger.info("Bar received: %s", bar)
    """
    return logging.getLogger(name)
