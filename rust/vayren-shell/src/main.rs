//! VAYREN native UI binary — Rust + Slint application shell with Python backend.
//!
//! Production entry point: spawns headless Python backend, communicates via
//! JSON/IPC, renders native Slint UI. No legacy toolkit dependency.

// GUI subsystem on Windows: no console window ever — the app opens directly.
// Backend diagnostics stay available via the Slint status surfaces; startup
// failures surface as native UI messages, never as a terminal.
#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use slint::ComponentHandle;
use std::cell::RefCell;
use std::rc::Rc;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    mpsc, Arc, Mutex,
};
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
    // Same precedence as the Python launcher: explicit flags (handled by the
    // caller) → VAYREN_DATA_DIR → legacy workstation folder when present →
    // per-user default. Never invents data: a missing folder surfaces as a
    // backend honest-empty snapshot, never as fabricated bars.
    if let Ok(dir) = std::env::var("VAYREN_DATA_DIR") {
        if !dir.trim().is_empty() {
            return dir;
        }
    }
    let legacy = std::path::Path::new(r"D:\ZerodhaTradingData");
    if legacy.is_dir() {
        return legacy.to_string_lossy().into_owned();
    }
    let home = std::env::var("USERPROFILE").unwrap_or_else(|_| ".".to_string());
    format!("{}/.vayren/data", home)
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

    // Spawn Python backend. One mutex-guarded handle serves the UI thread
    // (startup/shutdown snapshots) and worker threads (interaction fetches).
    // The mutex serializes whole command round-trips: the bridge locks
    // stdin/stdout separately, so concurrent `&PythonBackend` users without
    // it could read each other's responses.
    let (backend, ready_data) = PythonBackend::spawn(&data_dir, &strategy_dir)?;
    let backend: Arc<Mutex<PythonBackend>> = Arc::new(Mutex::new(backend));
    println!(
        "Backend ready: {} v{}",
        ready_data.backend, ready_data.version
    );

    // Verify the backend round-trips before opening the UI.
    println!("Requesting symbol list...");
    match PythonBackend::lock_send(&backend, BackendCommand::ListSymbols)? {
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
    let market_state = match PythonBackend::lock_send(
        &backend,
        BackendCommand::GetMarketSnapshot {
            symbol: snapshot_symbol,
            timeframe: snapshot_timeframe,
            limit: snapshot_limit,
        },
    )? {
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
        match PythonBackend::lock_send(&backend, BackendCommand::GetLabSnapshot)? {
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
    // Async fetch bus: interaction fetches run on worker threads (the UI
    // thread never blocks on the backend), results return through the
    // channel and apply on the UI thread via the poll timer below. Each
    // request carries a per-kind sequence; stale arrivals lose to newer
    // requests — latest state wins, obsolete renders never happen.
    #[derive(Debug)]
    enum FetchResult {
        Market(u64, Option<serde_json::Value>),
        LabSelect(u64, Option<serde_json::Value>),
        LabRun(u64, Option<serde_json::Value>),
    }
    let (fetch_tx, fetch_rx) = mpsc::channel::<FetchResult>();
    let market_seq = Arc::new(AtomicU64::new(0));
    let lab_select_seq = Arc::new(AtomicU64::new(0));
    let lab_run_seq = Arc::new(AtomicU64::new(0));
    fn spawn_fetch(
        backend: &Arc<Mutex<PythonBackend>>,
        tx: &mpsc::Sender<FetchResult>,
        command: BackendCommand,
        wrap: impl FnOnce(Option<serde_json::Value>) -> FetchResult + Send + 'static,
    ) {
        let backend = Arc::clone(backend);
        let tx = tx.clone();
        std::thread::spawn(move || {
            let data = match PythonBackend::lock_send(&backend, command) {
                Ok(response) => match response {
                    BackendResponse::MarketSnapshot { data }
                    | BackendResponse::LabSnapshot { data } => Some(data),
                    BackendResponse::Error { data } => {
                        eprintln!("Fetch backend error: {}", data.message);
                        None
                    }
                    other => {
                        eprintln!("Fetch unexpected response ({other:?})");
                        None
                    }
                },
                Err(err) => {
                    eprintln!("Fetch failed: {err}");
                    None
                }
            };
            let _ = tx.send(wrap(data));
        });
    }
    // Lab refetch closures (same snapshot shape as startup; selection pulls
    // the strategy workspace, RUN executes a real backend backtest).
    let fetch_lab_workspace: Rc<dyn Fn(String)> = Rc::new({
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        let seq = Arc::clone(&lab_select_seq);
        move |name: String| {
            let id = seq.fetch_add(1, Ordering::SeqCst) + 1;
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::SelectLabStrategy { strategy: name },
                move |data| FetchResult::LabSelect(id, data),
            );
        }
    });
    let fetch_lab_run: Rc<dyn Fn(shell::LabRunRequest)> = Rc::new({
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        let seq = Arc::clone(&lab_run_seq);
        move |request: shell::LabRunRequest| {
            let id = seq.fetch_add(1, Ordering::SeqCst) + 1;
            let command = BackendCommand::RunBacktest {
                strategy: request.strategy,
                symbols: request.symbols,
                timeframe: Some(request.timeframe).filter(|s| !s.is_empty()),
                start: Some(request.start).filter(|s| !s.is_empty()),
                end: Some(request.end).filter(|s| !s.is_empty()),
                capital: request.capital,
                mode: request.mode,
            };
            spawn_fetch(&backend, &tx, command, move |data| {
                FetchResult::LabRun(id, data)
            });
        }
    });
    // Portfolio production wiring (SLICE 4b): the idle trading service
    // snapshot feeds the native portfolio state (honest not-running book).
    println!("Requesting portfolio snapshot...");
    let portfolio_state = Rc::new(RefCell::new(
        match PythonBackend::lock_send(&backend, BackendCommand::GetPortfolioSnapshot)? {
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
    match PythonBackend::lock_send(&backend, BackendCommand::GetResearchSnapshot)? {
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
    match PythonBackend::lock_send(&backend, BackendCommand::GetLiveSnapshot)? {
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
    shell::wire_lab(&ui, lab_state.clone(), fetch_lab_workspace, fetch_lab_run);
    shell::wire_portfolio(&ui, portfolio_state.clone());
    shell::wire_research(&ui, research_state.clone());
    shell::wire_live(&ui, live_state.clone());
    shell::apply(&ui, &shell::demo_snapshot());
    // System production wiring (SLICE 4a): the real broker snapshot feeds
    // the native connection workspace (selection, states, field shapes).
    println!("Requesting system snapshot...");
    let connection_workspace =
        match PythonBackend::lock_send(&backend, BackendCommand::GetSystemSnapshot)? {
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
    // shape the startup path loads; the startup limit is preserved). The
    // old bars stay visible until the worker's snapshot swaps in atomically.
    let fetch_market: Rc<dyn Fn(Option<String>, Option<String>)> = Rc::new({
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        let seq = Arc::clone(&market_seq);
        move |symbol: Option<String>, timeframe: Option<String>| {
            let id = seq.fetch_add(1, Ordering::SeqCst) + 1;
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::GetMarketSnapshot {
                    symbol,
                    timeframe,
                    limit: snapshot_limit,
                },
                move |data| FetchResult::Market(id, data),
            );
        }
    });
    let market_state = Rc::new(RefCell::new(market_state));
    shell::apply_market(&ui, &market_state.borrow());
    shell::wire_market(&ui, market_state.clone(), fetch_market);
    // Fetch-result pump: drains worker snapshots on the UI thread (50ms —
    // far below a frame budget, far above fetch latency). Latest-wins per
    // kind: a slow earlier request never overwrites a newer arrival.
    let fetch_timer = slint::Timer::default();
    {
        let weak = ui.as_weak();
        let market_state = market_state.clone();
        let lab_state = lab_state.clone();
        let mut last_market: u64 = 0;
        let mut last_select: u64 = 0;
        let mut last_run: u64 = 0;
        fetch_timer.start(
            slint::TimerMode::Repeated,
            std::time::Duration::from_millis(50),
            move || {
                let Some(ui) = weak.upgrade() else { return };
                let mut latest_market: Option<(u64, Option<serde_json::Value>)> = None;
                let mut latest_lab_select: Option<(u64, Option<serde_json::Value>)> = None;
                let mut latest_lab_run: Option<(u64, Option<serde_json::Value>)> = None;

                for result in fetch_rx.try_iter() {
                    match result {
                        FetchResult::Market(id, data) => {
                            if id > last_market && latest_market.as_ref().map_or(true, |(prev_id, _)| id > *prev_id) {
                                latest_market = Some((id, data));
                            }
                        }
                        FetchResult::LabSelect(id, data) => {
                            if id > last_select && latest_lab_select.as_ref().map_or(true, |(prev_id, _)| id > *prev_id) {
                                latest_lab_select = Some((id, data));
                            }
                        }
                        FetchResult::LabRun(id, data) => {
                            if id > last_run && latest_lab_run.as_ref().map_or(true, |(prev_id, _)| id > *prev_id) {
                                latest_lab_run = Some((id, data));
                            }
                        }
                    }
                }

                if let Some((id, data)) = latest_market {
                    last_market = id;
                    if let Some(data) = data {
                        market::apply_snapshot_json(
                            &mut market_state.borrow_mut(),
                            &data,
                        );
                        shell::apply_market(&ui, &market_state.borrow());
                    }
                }
                if let Some((id, data)) = latest_lab_select {
                    last_select = id;
                    if let Some(data) = data {
                        lab::apply_snapshot_json(&mut lab_state.borrow_mut(), &data);
                        shell::apply_lab(&ui, &lab_state.borrow());
                    }
                }
                if let Some((id, data)) = latest_lab_run {
                    last_run = id;
                    match data {
                        Some(data) => {
                            lab::apply_snapshot_json(&mut lab_state.borrow_mut(), &data);
                        }
                        None => lab_state.borrow_mut().fail_run(),
                    }
                    shell::apply_lab(&ui, &lab_state.borrow());
                }
            },
        );
    }
    shell::apply_connection(&ui, &connection_workspace);
    let current_workspace = Rc::new(RefCell::new(connection_workspace));

    // Wire broker connection interactions
    {
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_select_requested(move |id: slint::SharedString| {
            let id_str = id.as_str().to_string();
            println!("Broker select requested: {}", id_str);
            match PythonBackend::lock_send(&backend,BackendCommand::GetSystemSnapshot {
                selected_id: Some(id_str),
            }) {
                Ok(BackendResponse::SystemSnapshot { data }) => {
                    let workspace = BrokerWorkspace::from_json(&data);
                    *cur_ws.borrow_mut() = workspace.clone();
                    if let Some(ui) = handle.upgrade() {
                        shell::apply_connection(&ui, &workspace);
                    }
                }
                Ok(other) => {
                    eprintln!("Broker select: unexpected response ({other:?})");
                }
                Err(err) => {
                    eprintln!("Broker select failed: {err}");
                }
            }
        });
    }
    {
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_refresh_requested(move || {
            println!("Broker refresh requested");
            match PythonBackend::lock_send(&backend,BackendCommand::GetSystemSnapshot { selected_id: None }) {
                Ok(BackendResponse::SystemSnapshot { data }) => {
                    let workspace = BrokerWorkspace::from_json(&data);
                    *cur_ws.borrow_mut() = workspace.clone();
                    if let Some(ui) = handle.upgrade() {
                        shell::apply_connection(&ui, &workspace);
                    }
                }
                Ok(other) => {
                    eprintln!("Broker refresh: unexpected response ({other:?})");
                }
                Err(err) => {
                    eprintln!("Broker refresh failed: {err}");
                }
            }
        });
    }
    {
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_connect_requested(move |v0, v1, v2, v3, v4, v5| {
            let broker_id = cur_ws.borrow().selected_id.clone();
            let fields = cur_ws.borrow().fields.clone();
            let raw_values = [v0, v1, v2, v3, v4, v5];
            // Sentinel written by apply_connection when a field is saved in the
            // OS vault.  If the user has not changed the field, the sentinel is
            // still there — we must not forward it to the backend, which would
            // overwrite the stored value with literal bullet characters.
            const SAVED_SENTINEL: &str = "\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}";
            let mut credentials = std::collections::HashMap::new();
            for (i, f) in fields.iter().enumerate() {
                if let Some(v) = raw_values.get(i) {
                    let s = v.as_str().trim();
                    if !s.is_empty() && s != SAVED_SENTINEL {
                        credentials.insert(f.key.clone(), s.to_string());
                    }
                }
            }
            println!(
                "Broker connect requested for '{}' ({} fields entered)",
                broker_id,
                credentials.len()
            );

            // Update UI immediately to Authenticating state so user sees progress instantly
            if let Some(ui) = handle.upgrade() {
                let mut auth_ws = cur_ws.borrow().clone();
                auth_ws.state = vayren_shell::broker_connection::ConnectionState::Authenticating;
                for b in auth_ws.brokers.iter_mut() {
                    if b.id == broker_id {
                        b.connected = false;
                    }
                }
                shell::apply_connection(&ui, &auth_ws);
            }

            // Spawn background thread to perform the connection IPC so UI event loop stays fluid
            let handle_bg = handle.clone();
            let backend_bg = Arc::clone(&backend);
            std::thread::spawn(move || {
                match PythonBackend::lock_send(&backend_bg,BackendCommand::ConnectBroker {
                    broker_id,
                    credentials,
                }) {
                    Ok(BackendResponse::SystemSnapshot { data }) => {
                        let workspace = BrokerWorkspace::from_json(&data);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                shell::apply_connection(&ui, &workspace);
                            }
                        });
                    }
                    Ok(BackendResponse::Error { data }) => {
                        eprintln!("Broker connect failed: {}", data.message);
                        let msg = data.message.clone();
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                ui.set_conn_is_failed(true);
                                ui.set_conn_error_message(msg.into());
                                ui.set_conn_pill_label("Connection Failed".into());
                                ui.set_conn_pill_tone(3);
                                ui.set_conn_cta_enabled(true);
                                ui.set_conn_form_enabled(true);
                            }
                        });
                    }
                    Ok(other) => {
                        eprintln!("Broker connect: unexpected response ({other:?})");
                    }
                    Err(err) => {
                        eprintln!("Broker connect IPC error: {err}");
                        let err_str = err.to_string();
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                ui.set_conn_is_failed(true);
                                ui.set_conn_error_message(format!("Connection IPC error: {err_str}").into());
                                ui.set_conn_pill_label("Connection Failed".into());
                                ui.set_conn_pill_tone(3);
                                ui.set_conn_cta_enabled(true);
                                ui.set_conn_form_enabled(true);
                            }
                        });
                    }
                }
            });
        });
    }
    {
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_login_requested(move || {
            let broker_id = cur_ws.borrow().selected_id.clone();
            println!("Broker retry requested for '{}'", broker_id);
            if let Some(ui) = handle.upgrade() {
                let mut auth_ws = cur_ws.borrow().clone();
                auth_ws.state = vayren_shell::broker_connection::ConnectionState::Authenticating;
                shell::apply_connection(&ui, &auth_ws);
            }

            let handle_bg = handle.clone();
            let backend_bg = Arc::clone(&backend);
            std::thread::spawn(move || {
                match PythonBackend::lock_send(&backend_bg,BackendCommand::ConnectBroker {
                    broker_id,
                    credentials: std::collections::HashMap::new(),
                }) {
                    Ok(BackendResponse::SystemSnapshot { data }) => {
                        let workspace = BrokerWorkspace::from_json(&data);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                shell::apply_connection(&ui, &workspace);
                            }
                        });
                    }
                    Ok(BackendResponse::Error { data }) => {
                        eprintln!("Broker retry failed: {}", data.message);
                        let msg = data.message.clone();
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                ui.set_conn_is_failed(true);
                                ui.set_conn_error_message(msg.into());
                                ui.set_conn_pill_label("Connection Failed".into());
                                ui.set_conn_pill_tone(3);
                                ui.set_conn_cta_enabled(true);
                                ui.set_conn_form_enabled(true);
                            }
                        });
                    }
                    Ok(other) => {
                        eprintln!("Broker retry: unexpected response ({other:?})");
                    }
                    Err(err) => {
                        eprintln!("Broker retry IPC error: {err}");
                        let err_str = err.to_string();
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                ui.set_conn_is_failed(true);
                                ui.set_conn_error_message(format!("Connection IPC error: {err_str}").into());
                                ui.set_conn_pill_label("Connection Failed".into());
                                ui.set_conn_pill_tone(3);
                                ui.set_conn_cta_enabled(true);
                                ui.set_conn_form_enabled(true);
                            }
                        });
                    }
                }
            });
        });
    }
    {
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_disconnect_requested(move || {
            let broker_id = cur_ws.borrow().selected_id.clone();
            println!("Broker disconnect requested for '{}'", broker_id);
            let handle_bg = handle.clone();
            let backend_bg = Arc::clone(&backend);
            std::thread::spawn(move || {
                match PythonBackend::lock_send(&backend_bg,BackendCommand::DisconnectBroker { broker_id }) {
                    Ok(BackendResponse::SystemSnapshot { data }) => {
                        let workspace = BrokerWorkspace::from_json(&data);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                shell::apply_connection(&ui, &workspace);
                            }
                        });
                    }
                    Ok(other) => {
                        eprintln!("Broker disconnect: unexpected response ({other:?})");
                    }
                    Err(err) => {
                        eprintln!("Broker disconnect failed: {err}");
                    }
                }
            });
        });
    }
    ui.on_broker_help_requested(move || {
        println!("Broker help requested");
    });
    ui.on_broker_add_requested(move || {
        println!("Add broker requested");
    });
    {
        let handle = ui.as_weak();
        ui.on_broker_paste_requested(move |idx: i32| {
            // clipboard-win is a Windows-only crate (empty elsewhere), so the
            // read itself is platform-gated; other platforms report unavailable.
            #[cfg(windows)]
            let pasted = clipboard_win::get_clipboard_string().ok();
            #[cfg(not(windows))]
            let pasted: Option<String> = None;
            if let Some(text) = pasted {
                let trimmed = text.trim();
                println!("Broker paste requested for field {idx}: length {}", trimmed.len());
                if let Some(ui) = handle.upgrade() {
                    let s = slint::SharedString::from(trimmed);
                    match idx {
                        0 => ui.set_broker_field_v0(s),
                        1 => ui.set_broker_field_v1(s),
                        2 => ui.set_broker_field_v2(s),
                        3 => ui.set_broker_field_v3(s),
                        4 => ui.set_broker_field_v4(s),
                        _ => ui.set_broker_field_v5(s),
                    }
                }
            } else {
                eprintln!("Broker paste failed: clipboard empty or unavailable");
            }
        });
    }
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
    backend
        .lock()
        .map_err(|e| format!("backend lock: {e}"))?
        .shutdown()?;
    println!("VAYREN shutdown complete");

    Ok(())
}
