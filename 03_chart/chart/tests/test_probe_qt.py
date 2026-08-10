"""Throwaway probe to isolate the Qt abort under pytest."""

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage, QPainter

from chart.models.crosshair_value import CrosshairValue
from chart.renderer.label_renderer import LabelRenderer
from chart.renderer.overlay_renderer import OverlayRenderer


def _app() -> None:
    QGuiApplication.instance()


def _value(ts: str = "2026-08-04 10:15:00") -> CrosshairValue:
    return CrosshairValue(
        bar_index=0,
        price=102.0,
        timestamp=ts,
        open=100.0,
        high=105.0,
        low=95.0,
        close=102.0,
    )


def _paint(crosshair_x: int) -> QRect:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    rect = OverlayRenderer.paint_time(painter, _value(), "1h", crosshair_x, QRect(0, 0, 400, 24))
    painter.end()
    return rect


def test_x5() -> None:
    assert _paint(5).left() >= 0


def test_x20() -> None:
    assert _paint(20).width() > 0


def test_x200() -> None:
    assert _paint(200).width() > 0


def test_x395() -> None:
    assert _paint(395).right() <= 400


def test_monkeypatch_center() -> None:
    _app()
    image = QImage(400, 24, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    captured: list[float] = []
    original = LabelRenderer.paint_centered

    def recording(_p, _text, _font, center_x: float, *_args, **_kwargs) -> QRect:
        captured.append(center_x)
        return original(_p, _text, _font, center_x, *_args, **_kwargs)

    LabelRenderer.paint_centered = staticmethod(recording)  # type: ignore[assignment]
    try:
        OverlayRenderer.paint_time(painter, _value(), "1h", 20, QRect(0, 0, 400, 24))
    finally:
        LabelRenderer.paint_centered = staticmethod(original)  # type: ignore[assignment]
    painter.end()
    assert captured == [20.0]
