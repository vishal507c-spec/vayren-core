# vayren-shell — native UI target (Rust + Slint)

The Rust+Slint direction for native UI (constitution §3). Currently owns
the application shell: navigation rail (7 workspaces, Alt+1..7 shortcuts),
the honest migration state for not-yet-migrated screens, and one genuine
migrated surface — the broker/UBL status panel, modeled as a pure,
headless-tested view-model (`view_model.rs`) projected onto Slint
properties by `shell.rs` and rendered by `ui/app.slint`.

The model is backend-driven and never invents readiness: connection is
derived from reported health only, and LIVE readiness from an explicit
gate verdict. Run with `cargo run -p vayren-shell`.

The existing Qt application shell is proven-retained during migration (a
full rewrite in one pass would destroy working behavior); new native UI
surfaces belong here, enforced by `scripts/validate_language_ownership.py`.
