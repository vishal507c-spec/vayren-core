"""Core domain — Python retains AI, the native bridge and the event marker only.

EventBus, registries, contracts and system intelligence are Rust-owned;
their Python twins were removed. `Event` stays as the zero-logic marker
base for Python-owned Strategy/AI/Research event vocabulary.
"""

from core.events.event import Event

__all__ = ["Event"]
