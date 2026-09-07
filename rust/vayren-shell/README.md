# vayren-shell — native UI target (Rust + egui)

The established Rust+egui direction for native UI (constitution §3).
Currently owns one genuine surface: the broker/UBL status panel, modeled
as a pure, headless-tested view-model (`view_model.rs`) bound to egui
widgets in the binary (`main.rs`).

The model is backend-driven and never invents readiness: connection is
derived from reported health only, and LIVE readiness from an explicit
gate verdict. Run with `cargo run -p vayren-shell`.

The existing Qt application shell is proven-retained (a full rewrite in
one pass would destroy working behavior); new native UI surfaces belong
here, enforced by `scripts/validate_language_ownership.py`.
