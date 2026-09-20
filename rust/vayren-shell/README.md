# vayren-shell — native UI (Rust + Slint)

The production application shell: navigation rail (7 workspaces) and the
native surfaces — Market chart (`market.rs` + `ui/market.slint`), broker
connection workspace (`view_model.rs` + `broker_connection.rs`),
Strategy Lab (`lab.rs` + `ui/lab.slint`), Research (`research_state.rs` +
`ui/research.slint`), Portfolio (`portfolio.rs` + `ui/portfolio.slint`),
and the LIVE execution workstation (`live.rs` + `ui/live.slint` —
first-principles responsive layout: compact command/safety bar with
always-reachable HALT, workspace + docked-or-drawer inspector,
recomposition thresholds derived from content minima, DPI-independent
logical units). Each is a pure, headless-tested view-model projected onto
Slint properties by `shell.rs`; `ui/live_harness.slint` +
`tests/live_render_snapshot.rs` verify the LIVE layout at every viewport
tier (harness geometry + real software-rasterizer pixel probes).

The model is backend-driven and never invents readiness: connection is
derived from reported health only, and LIVE readiness from an explicit
gate verdict. All state arrives from the headless Python backend over the
JSON bridge (`src/python_bridge.rs`). Run with `make dev`.
