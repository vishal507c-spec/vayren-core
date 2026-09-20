"""App — native composition package.

Production entry is the Rust + Slint shell (``make dev``), served by the
headless Python backend (``app.headless``). The legacy toolkit entry point
was retired when the native shell became the production entry (SLICE 6):
this package carries no UI, no event loop and no toolkit dependency.
"""
