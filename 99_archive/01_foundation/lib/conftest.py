import pytest


@pytest.fixture(autouse=True)
def setup_lib_logging():
    """Configure logging for lib tests."""
    import logging
    logging.basicConfig(level=logging.DEBUG, force=True)
    yield
