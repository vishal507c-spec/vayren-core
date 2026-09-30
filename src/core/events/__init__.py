"""Core events public API — marker base only (bus/registry are Rust-owned)."""

from core.events.event import Event

__all__ = ["Event"]
