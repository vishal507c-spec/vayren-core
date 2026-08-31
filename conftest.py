"""Root test configuration.

PySide6 + cyclic GC interact badly on Windows/offscreen: a generational
collection inside an unrelated test can destroy Qt objects whose C++
parents are already gone (access violation). Two mitigations:

1. Cyclic GC stays disabled for the whole session — Qt objects die by
   reference count instead (deterministic). Cycles leak; acceptable.
2. When the full run has passed, ``pytest_cmdline_main`` exits the process
   via ``os._exit(0)`` immediately after the test loop, skipping teardown
   hooks and interpreter shutdown that would otherwise walk the leaked
   cycles and abort AFTER every test has already passed.
"""

import gc
import os

import pytest

gc.disable()
# Some third-party imports re-enable cyclic GC, and pytest's own unraisable
# plugin calls gc.collect() around every test. Either can walk leaked Qt
# cycles mid-suite and abort the process (access violation under pymalloc).
# Keep both off for the whole session — cycles leak instead.
gc.enable = lambda: None  # type: ignore[assignment]
gc.collect = lambda *_a: 0  # type: ignore[method-assign, assignment]


@pytest.hookimpl(wrapper=True)
def pytest_runtestloop(session: object) -> object:  # noqa: ARG001
    """Exit immediately after the last test when everything passed.

    Skipping sessionfinish/unconfigure avoids interpreter teardown walking
    intentionally-leaked Qt cycles, which aborts AFTER all tests passed.
    """
    result = yield
    if result is None or int(result) == 0:
        os._exit(0)
    return result
