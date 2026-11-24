"""Authoritative OBR C1C4 strategy source — VAYREN OS v5.3.0 preservation.

The trading rules live in the sibling plain-Python module
``obr_c1c4_engine.py`` so they can be reviewed, diffed and regression-tested
directly. This module exposes the exact source text as ``OBR_C1C4_SOURCE``
for the strategy registry/compiler, keeping a single source of truth.

Original reference: ``vayren (2).py`` (VAYREN OS v5.3.0). SELL-only OBR,
30m third-candle reference (10:15–10:45), C1>=0.35% / C4>=0.45% of previous
close, break-candle-low SLM entry, ref_high stop / invalidation, 15:15 EOD.
No credentials or broker code are part of the strategy source.
"""

from __future__ import annotations

from pathlib import Path

OBR_C1C4_SOURCE: str = Path(__file__).with_name("obr_c1c4_engine.py").read_text(encoding="utf-8")

__all__ = ["OBR_C1C4_SOURCE"]
