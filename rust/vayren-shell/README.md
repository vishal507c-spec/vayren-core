# vayren-shell — native UI target (Rust + Slint)

The Rust+Slint direction for native UI (constitution §3). Currently owns
the application shell: navigation rail (7 workspaces, Alt+1..7 shortcuts)
and the migrated native surfaces — broker/UBL status panel
(`view_model.rs`), Strategy Lab (`lab.rs` + `ui/lab.slint`), Research
(`research_state.rs` + `ui/research.slint`), Portfolio (`portfolio.rs` +
`ui/portfolio.slint`, hosted into the Qt window by the `vayren-portfolio-view`
embed), and the LIVE execution workstation (`live.rs` +
`ui/live.slint` — first-principles responsive layout: compact command/safety
bar with always-reachable HALT, workspace + docked-or-drawer inspector,
recomposition thresholds derived from content minima, DPI-independent
logical units). Each is a pure, headless-tested view-model projected onto
Slint properties by `shell.rs`; `ui/live_harness.slint` +
`tests/live_render_snapshot.rs` verify the LIVE layout at every viewport
tier (harness geometry + real software-rasterizer pixel probes).

The model is backend-driven and never invents readiness: connection is
derived from reported health only, and LIVE readiness from an explicit
gate verdict. Run with `cargo run -p vayren-shell`.

The existing Qt application shell is proven-retained during migration (a
full rewrite in one pass would destroy working behavior); new native UI
surfaces belong here, enforced by `scripts/validate_language_ownership.py`.
