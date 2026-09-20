//! VAYREN native UI binary — Rust + Slint application shell with Python backend.
//!
//! Production entry point: spawns headless Python backend, communicates via
//! JSON/IPC, renders native Slint UI. No legacy toolkit dependency.

use slint::ComponentHandle;
use std::cell::RefCell;
use std::rc::Rc;
use vayren_shell::broker_connection::BrokerWorkspace;
use vayren_shell::lab;
use vayren_shell::market;
use vayren_shell::portfolio;
use vayren_shell::python_bridge::{BackendCommand, BackendResponse, PythonBackend};
use vayren_shell::research_state;
use vayren_shell::shell;

/// Read a `--name value` / `--name=value` CLI argument.
fn arg_value(argv: &[String], name: &str) -> Option<String> {
    let mut index = 0;
    while index < argv.len() {
        let arg = argv[index].as_str();
        if let Some(value) = arg.strip_prefix(&format!("{name}=")) {
            return Some(value.to_string());
        }
        if arg == name && index + 1 < argv.len() {
            return Some(argv[index + 1].clone());
        }
        index += 1;
    }
    None
}

fn default_data_dir() -> String {
    std::env::var("VAYREN_DATA_DIR").unwrap_or_else(|_| {
        let home = std::env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string());
        format!("{}/.vayren/data", home)
    })
}

fn default_strategy_dir() -> String {
    std::env::var("VAYREN_STRATEGIES").unwrap_or_else(|_| {
        let home = std::env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string());
        format!("{}/.vayren/strategies", home)
    })
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Explicit flags win; otherwise env vars; otherwise per-user defaults
    // (same precedence family as the Python CLI).
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let data_dir = arg_value(&argv, "--data-dir").unwrap_or_else(default_data_dir);
    let strategy_dir = arg_value(&argv, "--strategy-dir").unwrap_or_else(default_strategy_dir);

    println!("VAYREN starting...");
    println!("Data dir: {}", data_dir);
    println!("Strategy dir: {}", strategy_dir);

    // Spawn Python backend
    println!("Spawning headless Python backend...");
    let (backend, ready_data) = PythonBackend::spawn(&data_dir, &strategy_dir)?;
    let backend = Rc::new(backend);
    println!(
        "Backend ready: {} v{}",
        ready_data.backend, ready_data.version
    );

    // Verify the backend round-trips before opening the UI.
    println!("Requesting symbol list...");
    match backend.send_command(BackendCommand::ListSymbols)? {
        BackendResponse::SymbolsListed { data } => {
            println!("Backend returned {} symbols", data.symbols.len());
            if !data.symbols.is_empty() {
                println!(
                    "First 5 symbols: {:?}",
                    &data.symbols[..data.symbols.len().min(5)]
                );
            }
        }
        BackendResponse::Error { data } => {
            eprintln!("Backend error: {}", data.message);
        }
        _ => {
            eprintln!("Unexpected response");
        }
    }

    // Market production wiring (SLICE 3): the real backend snapshot feeds
    // the native MarketState. The chart/UI mount arrives in SLICE 5 — here
    // the state must be real, complete and render-ready.
    let snapshot_symbol = arg_value(&argv, "--symbol");
    let snapshot_timeframe = arg_value(&argv, "--timeframe");
    let snapshot_limit: Option<i64> = arg_value(&argv, "--limit").and_then(|s| s.parse().ok());
    println!("Requesting market snapshot...");
    let market_state = match backend.send_command(BackendCommand::GetMarketSnapshot {
        symbol: snapshot_symbol,
        timeframe: snapshot_timeframe,
        limit: snapshot_limit,
    })? {
        BackendResponse::MarketSnapshot { data } => {
            let mut state = market::MarketState::default();
            market::apply_snapshot_json(&mut state, &data);
            let view = market::project(&state);
            println!(
                "Market ready: {} {} ({} bars, {} watchlist rows) — {}",
                state.selected_symbol,
                state.timeframe,
                state.bars.len(),
                state.symbols.len(),
                view.status_message
            );
            state
        }
        BackendResponse::Error { data } => {
            eprintln!("Market snapshot failed: {}", data.message);
            market::MarketState::default()
        }
        _ => {
            eprintln!("Unexpected response");
            market::MarketState::default()
        }
    };
    // Initialize Slint UI
    println!("Initializing Slint UI...");
    let ui = vayren_shell::AppWindow::new()?;
    let zoom = Rc::new(RefCell::new(
        vayren_shell::viewport::ChartViewportZoom::default(),
    ));
    // VISUAL-CHECK fixtures are reachable only via the explicit CLI flag
    // and are watermarked in the UI; the normal path never shows results
    // without the engine bridge. A fixture bypasses the backend snapshot.
    let visual_kind = argv
        .iter()
        .find_map(|a| a.strip_prefix("--visual-check=").map(str::to_owned));
    let mut lab_state = match visual_kind.as_deref() {
        Some(kind) => {
            let fixture = shell::visual_fixture(kind);
            ui.set_visual_check_label(
                format!("VISUAL CHECK — DEMO DATA ({kind}) — NOT REAL RESULTS").into(),
            );
            fixture
        }
        None => shell::demo_lab_state(),
    };
    // Lab production wiring (SLICE 4e): the strategy library snapshot feeds
    // the native lab state (real rows, no runs yet).
    if visual_kind.is_none() {
        println!("Requesting lab snapshot...");
        match backend.send_command(BackendCommand::GetLabSnapshot)? {
            BackendResponse::LabSnapshot { data } => {
                lab::apply_snapshot_json(&mut lab_state, &data);
                let selected = lab_state
                    .selected
                    .and_then(|i| lab_state.strategies.get(i))
                    .map(|s| s.name.clone())
                    .unwrap_or_default();
                println!(
                    "Lab ready: {} strategies, selected '{}'",
                    lab_state.strategies.len(),
                    selected
                );
            }
            BackendResponse::Error { data } => {
                eprintln!("Lab snapshot failed: {}", data.message);
            }
            _ => {
                eprintln!("Unexpected response");
            }
        }
    }
    let lab_state = Rc::new(RefCell::new(lab_state));
    // Portfolio production wiring (SLICE 4b): the idle trading service
    // snapshot feeds the native portfolio state (honest not-running book).
    println!("Requesting portfolio snapshot...");
    let portfolio_state = Rc::new(RefCell::new(
        match backend.send_command(BackendCommand::GetPortfolioSnapshot)? {
            BackendResponse::PortfolioSnapshot { data } => {
                let snapshot = portfolio::PortfolioSnapshot::from_json(&data);
                println!(
                    "Portfolio ready: mode {} lifecycle {} ({} positions, {} orders) — configured: {}",
                    snapshot.mode,
                    snapshot.lifecycle,
                    snapshot.positions.len(),
                    snapshot.orders.len(),
                    portfolio::is_configured(&snapshot)
                );
                portfolio::PortfolioState {
                    snapshot,
                    ..Default::default()
                }
            }
            BackendResponse::Error { data } => {
                eprintln!("Portfolio snapshot failed: {}", data.message);
                portfolio::PortfolioState::default()
            }
            _ => {
                eprintln!("Unexpected response");
                portfolio::PortfolioState::default()
            }
        },
    ));
    // Research starts from the honest empty state; when VAYREN_RESEARCH_SNAPSHOT
    // points at engine output, the executed bundle presents instead — still
    // read-only engine facts, never UI inference.
    // Research production wiring (SLICE 4d): the research service snapshot
    // feeds the native research state (strategies + experiments).
    println!("Requesting research snapshot...");
    let mut research_state = research_state::demo_research_state();
    match backend.send_command(BackendCommand::GetResearchSnapshot)? {
        BackendResponse::ResearchSnapshot { data } => {
            research_state.apply_host_snapshot(&data);
            println!(
                "Research ready: {} strategies, {} experiments",
                research_state.strategies.len(),
                research_state.experiments.len()
            );
        }
        BackendResponse::Error { data } => {
            eprintln!("Research snapshot failed: {}", data.message);
        }
        _ => {
            eprintln!("Unexpected response");
        }
    }
    if let Some(bundle) = research_state::load_snapshot_from_env() {
        research_state::apply_snapshot(&mut research_state, bundle);
    }
    let research_state = Rc::new(RefCell::new(research_state));
    // LIVE starts from the honest static readiness facts of this workstation:
    // no venue adapter, PAPER default, empty execution tables.
    // Live production wiring (SLICE 4c): the idle trading service snapshot
    // feeds the native live state (same dict the legacy workspace consumed).
    println!("Requesting live snapshot...");
    let mut live_state = shell::demo_live_state();
    match backend.send_command(BackendCommand::GetLiveSnapshot)? {
        BackendResponse::LiveSnapshot { data } => {
            live_state.apply_snapshot(&data);
            println!(
                "Live ready: {} {} ({} bars, last {})",
                live_state.market_symbol,
                live_state.market_timeframe,
                live_state.market_bar_count,
                live_state
                    .market_last_price
                    .map(|p| p.to_string())
                    .unwrap_or_else(|| "N/A".to_string())
            );
        }
        BackendResponse::Error { data } => {
            eprintln!("Live snapshot failed: {}", data.message);
        }
        _ => {
            eprintln!("Unexpected response");
        }
    }
    let live_state = Rc::new(RefCell::new(live_state));
    shell::wire(&ui);
    shell::wire_zoom(&ui, zoom.clone());
    shell::wire_lab(&ui, lab_state.clone());
    shell::wire_portfolio(&ui, portfolio_state.clone());
    shell::wire_research(&ui, research_state.clone());
    shell::wire_live(&ui, live_state.clone());
    shell::apply(&ui, &shell::demo_snapshot());
    // System production wiring (SLICE 4a): the real broker snapshot feeds
    // the native connection workspace (selection, states, field shapes).
    println!("Requesting system snapshot...");
    let connection_workspace = match backend.send_command(BackendCommand::GetSystemSnapshot)? {
        BackendResponse::SystemSnapshot { data } => {
            let workspace = BrokerWorkspace::from_json(&data);
            let (pill, _) = workspace.pill();
            println!(
                "System ready: {} broker(s), selected '{}' — {}",
                workspace.brokers.len(),
                workspace.selected_id,
                pill
            );
            workspace
        }
        BackendResponse::Error { data } => {
            eprintln!("System snapshot failed: {}", data.message);
            BrokerWorkspace::empty()
        }
        _ => {
            eprintln!("Unexpected response");
            BrokerWorkspace::empty()
        }
    };
    // Symbol/timeframe refetch for the chart interactions (same snapshot
    // shape the startup path loads; the startup limit is preserved).
    let fetch_market: Rc<dyn Fn(Option<String>, Option<String>) -> Option<serde_json::Value>> =
        Rc::new({
            let backend = backend.clone();
            move |symbol: Option<String>, timeframe: Option<String>| -> Option<serde_json::Value> {
                match backend.send_command(BackendCommand::GetMarketSnapshot {
                    symbol,
                    timeframe,
                    limit: snapshot_limit,
                }) {
                    Ok(BackendResponse::MarketSnapshot { data }) => Some(data),
                    Ok(other) => {
                        eprintln!("Market refetch: unexpected response ({other:?})");
                        None
                    }
                    Err(err) => {
                        eprintln!("Market refetch failed: {err}");
                        None
                    }
                }
            }
        });
    let market_state = Rc::new(RefCell::new(market_state));
    shell::apply_market(&ui, &market_state.borrow());
    shell::wire_market(&ui, market_state, fetch_market);
    shell::apply_connection(&ui, &connection_workspace);
    shell::apply_zoom(&ui, &zoom.borrow());
    shell::apply_lab(&ui, &lab_state.borrow());
    shell::apply_portfolio(&ui, &portfolio_state.borrow());
    shell::apply_research(&ui, &research_state.borrow());
    shell::apply_live(&ui, &live_state.borrow());
    // Cross-runtime entry point: `--screen <name>` preselects that screen;
    // default stays Lab.
    let initial = shell::initial_screen(&argv);
    shell::select(&ui, initial);
    println!("VAYREN UI ready - entering event loop");
    ui.run()?;

    // Shutdown backend
    println!("Shutting down Python backend...");
    backend.shutdown()?;
    println!("VAYREN shutdown complete");

    Ok(())
}
