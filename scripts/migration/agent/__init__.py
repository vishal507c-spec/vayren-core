"""Autonomous migration agent: evidence-driven, never declaration-driven.

The agent processes BLOCKED behavioral units in dependency order. For each
unit it analyzes the contract in an isolated sandbox, generates a Rust
candidate from an explicit intermediate spec, proves differential parity
before touching production, repairs only mechanical failures, and promotes
to canonical only when every safety gate passes. Anything unproven stays
BLOCKED with a recorded reason — never a fake PASS.
"""

from __future__ import annotations

from . import analyzer, order, pipeline

__all__ = ["analyzer", "order", "pipeline"]
