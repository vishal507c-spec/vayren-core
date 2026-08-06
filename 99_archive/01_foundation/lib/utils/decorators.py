import asyncio
import functools
import logging
import time
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])

logger = logging.getLogger(__name__)


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, exceptions: tuple = (Exception,)) -> Callable[[F], F]:
    """Retry a function on failure with exponential backoff."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait = delay * (backoff ** attempt)
                        logger.warning("Retry %d/%d for %s after %fs: %s", attempt + 1, max_attempts, func.__name__, wait, e)
                        time.sleep(wait)
            raise last_exception  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


def async_retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, exceptions: tuple = (Exception,)) -> Callable[[F], F]:
    """Retry an async function on failure with exponential backoff."""
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt < max_attempts - 1:
                        wait = delay * (backoff ** attempt)
                        logger.warning("Async retry %d/%d for %s after %fs: %s", attempt + 1, max_attempts, func.__name__, wait, e)
                        await asyncio.sleep(wait)
            raise last_exception  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


def timed(func: F) -> F:
    """Log execution time of a function."""
    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        logger.debug("%s took %.3fms", func.__name__, elapsed * 1000)
        return result
    return wrapper  # type: ignore[return-value]
