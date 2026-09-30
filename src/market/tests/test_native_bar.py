"""native_bar bridge pins (lazy load, renamed param, finite guards)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from market import native_bar as nb  # noqa: E402

# Validator boundary: tests never import core.native directly; the bridge
# carries the exact same error class object.
NativeBridgeError = nb.NativeBridgeError


def test_return_pct_math() -> None:
    assert nb.return_pct(open_px=100.0, close=110.0) == pytest.approx(10.0)
    assert nb.return_pct(open_px=0.0, close=50.0) == 0.0


def test_open_param_was_renamed() -> None:
    with pytest.raises(TypeError):
        nb.return_pct(open=100.0, close=110.0)  # type: ignore[call-arg]


@pytest.mark.parametrize("bad", ["one-hundred", None, object()])
def test_garbage_inputs_raise_value_error(bad: object) -> None:
    with pytest.raises(ValueError, match="non-numeric"):
        nb.return_pct(open_px=bad, close=110.0)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="non-numeric"):
        nb.return_pct(open_px=100.0, close=bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_legs_raise_value_error(bad: float) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        nb.return_pct(open_px=bad, close=110.0)
    with pytest.raises(ValueError, match="non-finite"):
        nb.return_pct(open_px=100.0, close=bad)


def test_kernel_is_loaded_lazily(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Import/reload must not probe the library; the first call fails closed."""
    import importlib

    monkeypatch.setenv("VAYREN_NATIVE_LIB", str(tmp_path / "missing.dll"))
    reloaded = importlib.reload(nb)
    assert reloaded._lib is None
    with pytest.raises(NativeBridgeError):
        reloaded.return_pct(100.0, 110.0)
    importlib.reload(nb)  # restore the working handle for the rest of the session
