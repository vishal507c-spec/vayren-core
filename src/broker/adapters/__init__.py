"""Broker adapter packages — one isolated package per venue (design §5).

Boundary rules (enforced by tests + validators):

- An adapter package may import ONLY ``broker.*`` contracts and the stdlib.
  It must NEVER import ``data.*``/``execution.*`` (that would be a
  data↔broker import cycle), NEVER import a broker SDK at module level,
  and NEVER duplicate UBL vocabulary (ErrorCode, CapabilitySet, faces,
  registry, selection, credentials model).
- Transport/credential implementations stay in their owning isolated
  packages; the adapter package owns broker identity, the advertised
  capability matrix, and the registry-record wiring.
"""
