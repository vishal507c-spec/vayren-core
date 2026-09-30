"""FFI layout/length/zero-size pins for the aggregation bridge."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from market import native_aggregate as na  # noqa: E402

# Validator boundary: tests never import core.native directly; the bridge
# re-exports the exact same error class object.
NativeBridgeError = na.NativeBridgeError


def _rows(n: int = 2) -> dict:
    return {
        "days": [100] * n,
        "secs": [33300 + 60 * i for i in range(n)],
        "opens": [10.0] * n,
        "highs": [10.5] * n,
        "lows": [9.5] * n,
        "closes": [10.2] * n,
        "volumes": [100.0] * n,
        "timeframe_seconds": 900,
        "session_start": 33300,
    }


def test_mismatched_lengths_fail_closed() -> None:
    rows = _rows()
    rows["closes"] = [10.2]
    with pytest.raises(NativeBridgeError, match="mismatched lengths"):
        na.aggregate(**rows)


def test_non_positive_timeframe_fails_closed() -> None:
    rows = _rows()
    rows["timeframe_seconds"] = 0
    with pytest.raises(NativeBridgeError, match="timeframe_seconds"):
        na.aggregate(**rows)


def test_layout_is_checked_before_the_kernel_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the pre-call order: a drifted layout must raise before any FFI call."""

    def _explode(*_args: object, **_kwargs: object) -> int:
        raise AssertionError("kernel must not be called on layout drift")

    class _ExplodingLib:
        def __getattr__(self, name: str):
            return _explode

    monkeypatch.setattr(na, "_lib", _ExplodingLib())
    monkeypatch.setattr(na, "_BUCKET_SIZE", 1)
    with pytest.raises(RuntimeError, match="layout drift"):
        na.aggregate(**_rows())


def test_aggregate_happy_path_groups_one_bucket() -> None:
    buckets = na.aggregate(**_rows(3))
    assert len(buckets) == 1
    day, index, o, h, low, c, v = buckets[0]
    assert (day, index, o, h, low, c, v) == (100, 0, 10.0, 10.5, 9.5, 10.2, 300.0)


def test_bucket_start_zero_size_fails_closed() -> None:
    with pytest.raises(NativeBridgeError, match="size_s"):
        na.bucket_start("2026-01-05 09:15:00", 0, 33300)


def test_bucket_start_non_string_stamp_fails_closed() -> None:
    with pytest.raises(NativeBridgeError, match="stamp type"):
        na.bucket_start(20260105, 900, 33300)  # type: ignore[arg-type]


def test_session_anchor_rejects_non_string() -> None:
    with pytest.raises(TypeError):
        na.session_anchor_seconds(915)  # type: ignore[arg-type]
    assert na.session_anchor_seconds("09:15") == 33300


def test_closed_count_clamps_negative_and_rejects_garbage() -> None:
    assert na.closed_count(-5, True) == 0
    assert na.closed_count(3, True) == 2
    assert na.closed_count(3, False) == 3
    with pytest.raises(TypeError):
        na.closed_count("3", True)  # type: ignore[arg-type]


def test_fold_tick_guards_and_happy_path() -> None:
    with pytest.raises(NativeBridgeError, match="NaN price"):
        na.fold_tick(None, float("nan"), 500, None)
    # Numeric strings coerce via float(); truly non-numeric input is misuse.
    assert na.fold_tick(None, "101", 500, None) == (  # type: ignore[arg-type]
        101.0,
        101.0,
        101.0,
        101.0,
        0.0,
    )
    with pytest.raises(TypeError, match="price must be numeric"):
        na.fold_tick(None, "one-oh-one", 500, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="price must be numeric"):
        na.fold_tick(None, object(), 500, None)  # type: ignore[arg-type]
    with pytest.raises(NativeBridgeError, match="5 OHLCV"):
        na.fold_tick((1.0, 2.0), 101.0, 500, None)  # type: ignore[arg-type]
    opened = na.fold_tick(None, 101.0, 500, None)
    assert opened == (101.0, 101.0, 101.0, 101.0, 0.0)


def test_mode_matches_counter_semantics() -> None:
    assert na.mode([1, 2, 2, 3]) == 2
    assert na.mode([]) is None
