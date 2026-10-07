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

/// The user's home directory.
///
/// `USERPROFILE` is the Windows variable; on every other platform it does not
/// exist, so the old `USERPROFILE` lookup fell back to `"."` and wrote
/// `./.vayren/data` — i.e. the data directory landed in whatever CWD the app
/// happened to be launched from. `HOME` is the portable name on unix.
fn home_dir() -> String {
    #[cfg(windows)]
    let raw = std::env::var("USERPROFILE").ok();
    #[cfg(not(windows))]
    let raw = std::env::var("HOME").ok();
    raw.filter(|home| !home.trim().is_empty())
        .unwrap_or_else(|| ".".to_string())
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
    format!("{}/.vayren/data", home_dir())
}

fn default_strategy_dir() -> String {
    // Same precedence as the Python launcher: explicit flags (handled by the
    // caller) → VAYREN_STRATEGIES → legacy workstation folder when present →
    // per-user default. Without the legacy step a direct exe launch resolved
    // to an empty home folder while the launcher resolved to the real
    // library, so the same OBR file was visible in one path and missing in
    // the other. No new location is introduced: this mirrors
    // tools/launch_native.py::default_strategy_dir exactly.
    if let Ok(dir) = std::env::var("VAYREN_STRATEGIES") {
        if !dir.trim().is_empty() {
            return dir;
        }
    }
    let legacy = std::path::Path::new(r"D:\VAYREN_STRATEGIES");
    if legacy.is_dir() {
        return legacy.to_string_lossy().into_owned();
    }
    format!("{}/.vayren/strategies", home_dir())
}

/// Parse the `--limit` CLI value.
///
/// A garbage value used to parse to `None`, which the bridge reads as "no
/// limit" — so `--limit abc` silently turned a bounded startup fetch into an
/// UNBOUNDED one (the backend would try to load the symbol's entire history).
/// An explicit flag that cannot be understood is an error, not a default.
fn parse_limit(raw: Option<String>) -> Result<Option<i64>, String> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err("--limit was given with no value".to_string());
    }
    let parsed: i64 = trimmed
        .parse()
        .map_err(|_| format!("--limit must be a whole number of bars, got '{raw}'"))?;
    if parsed < 0 {
        return Err(format!("--limit must be >= 0, got {parsed}"));
    }
    Ok(Some(parsed))
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Explicit flags win; otherwise env vars; otherwise per-user defaults
    // (same precedence family as the Python CLI).
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let data_dir = arg_value(&argv, "--data-dir").unwrap_or_else(default_data_dir);
    let strategy_dir = arg_value(&argv, "--strategy-dir").unwrap_or_else(default_strategy_dir);
    // An unparseable --limit is rejected BEFORE the backend is spawned: a
    // silent "no limit" would start an unbounded history fetch the user never
    // asked for.
    let snapshot_limit: Option<i64> = match parse_limit(arg_value(&argv, "--limit")) {
        Ok(limit) => limit,
        Err(message) => {
            eprintln!("vayren-shell: {message}");
            return Err(message.into());
        }
    };

    println!("VAYREN starting...");
    println!("Data dir: {}", data_dir);
    println!("Strategy dir: {}", strategy_dir);

    // Launch mode is owned by the remote-client launch contract (ONE decision
    // point): explicit `--remote-ui` / `--remote` win, `--local` forces the
    // developer loop, legacy `--data-dir` / `--strategy-dir` shortcuts keep
    // their local backend, and otherwise the PACKAGED build (feature
    // `packaged-remote`, set when the release EXE is built) goes remote on
    // its own while developer builds stay local. Shortcuts carry no token
    // and no endpoint by design — configuration lives in the OS credential
    // store plus the built-in default, never in argv.
    let remote_gateway_url = match vayren_remote_client::launch::resolve_launch_mode(
        &argv,
        cfg!(feature = "packaged-remote"),
    ) {
        vayren_remote_client::launch::LaunchMode::Remote { url } => Some(url),
        vayren_remote_client::launch::LaunchMode::Probe { url } => {
            // Read-only smoke probe: hello → welcome → snapshot → brief
            // subscribe, without spawning a backend, opening the UI, or
            // sending any command.
            return vayren_shell::remote_source::run_remote_probe(&url).map_err(|err| err.into());
        }
        vayren_remote_client::launch::LaunchMode::Local => None,
    };
    if let Some(ref url) = remote_gateway_url {
        println!("VAYREN starting (remote live client + local historical stores)...");
        println!("Gateway: {url}");
    }

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
        LabCoverage(u64, Option<serde_json::Value>),
        /// A measured progress event, applied on the UI thread as it arrives.
        /// The run sequence rides along so a progress event from a SUPERSEDED
        /// run can be dropped: without it, a run that was cancelled and
        /// restarted could have its old run's late progress applied on top of
        /// the new run, showing a completed count that belongs to a backtest
        /// the user no longer has on screen.
        LabProgress(u64, serde_json::Value),
        /// Broker select/refresh answers: moved OFF the UI thread (the old
        /// handlers blocked on `lock_send` inside the callback, freezing the
        /// window for the whole backend round-trip).
        System(Option<serde_json::Value>),
        /// The live service's own answer to a host-mode UI action, or to the
        /// periodic live poll below. It is the truth about what happened,
        /// so it replaces the optimistic local state instead of layering
        /// on top of it. The sequence keeps overlapping answers ordered:
        /// a stale arrival never overwrites a newer snapshot.
        Live(u64, Option<serde_json::Value>),
    }
    let (fetch_tx, fetch_rx) = mpsc::channel::<FetchResult>();
    let market_seq = Arc::new(AtomicU64::new(0));
    let live_seq = Arc::new(AtomicU64::new(0));
    let lab_coverage_seq = Arc::new(AtomicU64::new(0));
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
                    | BackendResponse::LabSnapshot { data }
                    | BackendResponse::LabCoverage { data }
                    | BackendResponse::SystemSnapshot { data } => Some(data),
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
    // the strategy workspace WITH the current config, RUN executes a real
    // backend backtest).
    let fetch_lab_workspace: Rc<dyn Fn(shell::LabSelectRequest)> = Rc::new({
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        let seq = Arc::clone(&lab_select_seq);
        move |request: shell::LabSelectRequest| {
            let id = seq.fetch_add(1, Ordering::SeqCst) + 1;
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::SelectLabStrategy {
                    strategy: request.strategy,
                    symbols: request.symbols,
                    timeframe: Some(request.timeframe).filter(|s| !s.is_empty()),
                    start: Some(request.start).filter(|s| !s.is_empty()),
                    end: Some(request.end).filter(|s| !s.is_empty()),
                    capital: request.capital,
                    mode: request.mode,
                },
                move |data| FetchResult::LabSelect(id, data),
            );
        }
    });
    // RUN is the one command that STREAMS: a 500+ symbol run occupies the
    // backend for minutes, so every measured `lab_progress` event is pushed
    // straight to the UI instead of the screen sitting on a dead "RUNNING".
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
            let backend = Arc::clone(&backend);
            let tx = tx.clone();
            std::thread::spawn(move || {
                let progress_tx = tx.clone();
                let error_tx = tx.clone();
                let data = PythonBackend::lock_send_streaming(
                    &backend,
                    command,
                    move |event| {
                        let _ = progress_tx.send(FetchResult::LabProgress(id, event));
                    },
                    move |message| {
                        eprintln!("Run progress without data ({message})");
                        let _ = error_tx.send(FetchResult::LabProgress(
                            id,
                            serde_json::json!({"stage": "failed", "error": message}),
                        ));
                    },
                );
                let payload = match data {
                    Ok(BackendResponse::LabSnapshot { data })
                    | Ok(BackendResponse::MarketSnapshot { data }) => Some(data),
                    Ok(BackendResponse::Error { data }) => {
                        eprintln!("Run failed: {}", data.message);
                        None
                    }
                    Ok(other) => {
                        eprintln!("Run unexpected response ({other:?})");
                        None
                    }
                    Err(err) => {
                        eprintln!("Run failed: {err}");
                        None
                    }
                };
                let _ = tx.send(FetchResult::LabRun(id, payload));
            });
        }
    });
    // Data-completeness probe for the §02 strip: the backend counts real rows
    // for the current selection, `lab.rs` turns the counts plus the window into
    // the percentage. Absent measurement leaves the strip hidden.
    let fetch_lab_coverage: Rc<dyn Fn(shell::LabCoverageRequest)> = Rc::new({
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        let seq = Arc::clone(&lab_coverage_seq);
        move |request: shell::LabCoverageRequest| {
            let id = seq.fetch_add(1, Ordering::SeqCst) + 1;
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::LabCoverage {
                    symbols: request.symbols,
                    timeframe: Some(request.timeframe).filter(|s| !s.is_empty()),
                    start: Some(request.start).filter(|s| !s.is_empty()),
                    end: Some(request.end).filter(|s| !s.is_empty()),
                    full: request.full,
                },
                move |data| FetchResult::LabCoverage(id, data),
            );
        }
    });
    // CANCEL: the backend polls this flag between symbols, so the stop is
    // graceful and a partial run is reported as cancelled, never as complete.
    let fetch_lab_cancel: Rc<dyn Fn()> = Rc::new({
        let backend = Arc::clone(&backend);
        move || {
            if let Err(err) = PythonBackend::lock_send(&backend, BackendCommand::CancelBacktest) {
                eprintln!("Cancel request failed: {err}");
            }
        }
    });
    let fetch_lab_save: Rc<dyn Fn(String, String)> = Rc::new({
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        let seq = Arc::clone(&lab_select_seq);
        move |strategy: String, code: String| {
            let id = seq.fetch_add(1, Ordering::SeqCst) + 1;
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::SaveLabStrategy { strategy, code },
                move |data| FetchResult::LabSelect(id, data),
            );
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
    let is_remote = remote_gateway_url.is_some();
    let mut live_state = if is_remote {
        vayren_shell::remote_live::init_remote_state()
    } else {
        shell::demo_live_state()
    };
    if !is_remote {
        println!("Requesting live snapshot...");
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
    }
    let live_state = Rc::new(RefCell::new(live_state));
    let remote_rx = if let Some(url) = remote_gateway_url {
        let (remote_tx, remote_rx) = mpsc::channel::<vayren_shell::remote_live::RemoteUiUpdate>();
        vayren_shell::remote_live::spawn_remote_bootstrap(url, remote_tx);
        Some(remote_rx)
    } else {
        None
    };
    shell::wire(&ui);
    shell::wire_zoom(&ui, zoom.clone());
    shell::wire_lab(
        &ui,
        lab_state.clone(),
        fetch_lab_workspace,
        fetch_lab_run,
        fetch_lab_coverage,
        fetch_lab_cancel,
        fetch_lab_save,
    );
    shell::wire_portfolio(&ui, portfolio_state.clone());
    shell::wire_research(&ui, research_state.clone());
    shell::wire_live(&ui, live_state.clone());
    shell::apply(&ui, &shell::demo_snapshot());
    // System production wiring (SLICE 4a): the real broker snapshot feeds
    // the native connection workspace (selection, states, field shapes).
    println!("Requesting system snapshot...");
    let connection_workspace = match PythonBackend::lock_send(
        &backend,
        BackendCommand::GetSystemSnapshot { selected_id: None },
    )? {
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
    let current_workspace = Rc::new(RefCell::new(connection_workspace));
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
        let live_state = live_state.clone();
        let current_workspace = current_workspace.clone();
        let backend = backend.clone();
        let tx = fetch_tx.clone();
        let live_seq = Arc::clone(&live_seq);
        let mut last_market: u64 = 0;
        let mut last_live: u64 = 0;
        // In-flight live answers (poll + actions) bound the backend load:
        // a new poll never starts while an answer is still travelling, so
        // a slow snapshot degrades to a slower cadence instead of a queue.
        let mut live_busy: u32 = 0;
        // The backend owns session truth (fills, P&L, events, broker edges,
        // kill-switch halts) and documents a 1s UI poll while idle — but no
        // poll existed, so a RUNNING session looked frozen until the next
        // click. The startup fetch just landed, so the first poll is one
        // interval out.
        let mut next_live_poll = std::time::Instant::now() + std::time::Duration::from_secs(1);
        let mut last_select: u64 = 0;
        let mut last_run: u64 = 0;
        let mut last_coverage: u64 = 0;
        // Dropped stale progress events (seq-guard): a superseded run's late
        // event must never overwrite the current run's facts.
        let mut progress_dropped: u64 = 0;
        let run_seq = Arc::clone(&lab_run_seq);
        fetch_timer.start(
            slint::TimerMode::Repeated,
            std::time::Duration::from_millis(50),
            move || {
                let Some(ui) = weak.upgrade() else { return };
                // LIVE is HOST-MODE: the buttons queue an action and the
                // backend owns the transition. Drain them here and forward
                // each on a worker thread — a blocking round-trip on the UI
                // thread would freeze the window, and dropping them would make
                // every LIVE control a silent no-op.
                if let Some(ref rx) = remote_rx {
                    use vayren_shell::remote_live::{
                        apply_remote_sections, mark_link, push_remote_event, RemoteUiUpdate,
                    };
                    while let Some(raw) = live_state.borrow_mut().take_action() {
                        let name: String = serde_json::from_str::<serde_json::Value>(&raw)
                            .ok()
                            .and_then(|v| {
                                v.get("action")
                                    .and_then(|a| a.as_str())
                                    .map(str::to_string)
                            })
                            .unwrap_or_else(|| "unknown".to_string());
                        eprintln!(
                            "remote UI: control '{name}' refused (read-only client, nothing sent)"
                        );
                    }
                    let mut dirty = false;
                    for update in rx.try_iter() {
                        match update {
                            RemoteUiUpdate::Snapshot { sections, resync } => {
                                if resync {
                                    println!("Remote resync: replacing state with fresh snapshot");
                                }
                                apply_remote_sections(&mut live_state.borrow_mut(), &sections, resync);
                                dirty = true;
                            }
                            RemoteUiUpdate::StateUpdate { sections } => {
                                apply_remote_sections(&mut live_state.borrow_mut(), &sections, false);
                                dirty = true;
                            }
                            RemoteUiUpdate::StreamEvent { name, payload } => {
                                push_remote_event(&mut live_state.borrow_mut(), &name, &payload);
                                dirty = true;
                            }
                            RemoteUiUpdate::Link {
                                label,
                                detail,
                                reconnects,
                            } => {
                                eprintln!("remote UI: link {label} ({detail})");
                                mark_link(&mut live_state.borrow_mut(), &label, &detail, reconnects);
                                dirty = true;
                            }
                        }
                    }
                    if dirty {
                        shell::apply_live(&ui, &live_state.borrow());
                    }
                } else {
                    while let Some(raw) = live_state.borrow_mut().take_action() {
                        let action: serde_json::Value = match serde_json::from_str(&raw) {
                            Ok(value) => value,
                            Err(err) => {
                                eprintln!("Live action is not valid JSON ({err}): {raw}");
                                continue;
                            }
                        };
                        let backend = Arc::clone(&backend);
                        let tx = tx.clone();
                        let id = live_seq.fetch_add(1, Ordering::SeqCst) + 1;
                        live_busy += 1;
                        std::thread::spawn(move || {
                            let data = match PythonBackend::lock_send(
                                &backend,
                                BackendCommand::LiveAction { action },
                            ) {
                                Ok(BackendResponse::LiveSnapshot { data }) => Some(data),
                                Ok(other) => {
                                    eprintln!("Live action: unexpected response ({other:?})");
                                    None
                                }
                                Err(err) => {
                                    eprintln!("Live action failed: {err}");
                                    None
                                }
                            };
                            let _ = tx.send(FetchResult::Live(id, data));
                        });
                    }
                    // Periodic LIVE snapshot poll (see `next_live_poll`): keeps
                    // fills, P&L, positions, events, quotes and broker edges
                    // live while a session runs — and re-reads the broker
                    // selection, so SYSTEM connects surface within one tick.
                    if live_busy == 0 && std::time::Instant::now() >= next_live_poll {
                        let id = live_seq.fetch_add(1, Ordering::SeqCst) + 1;
                        live_busy += 1;
                        let backend = Arc::clone(&backend);
                        let tx = tx.clone();
                        std::thread::spawn(move || {
                            let data = match PythonBackend::lock_send(
                                &backend,
                                BackendCommand::GetLiveSnapshot,
                            ) {
                                Ok(BackendResponse::LiveSnapshot { data }) => Some(data),
                                Ok(other) => {
                                    eprintln!("Live poll: unexpected response ({other:?})");
                                    None
                                }
                                Err(err) => {
                                    eprintln!("Live poll failed: {err}");
                                    None
                                }
                            };
                            let _ = tx.send(FetchResult::Live(id, data));
                        });
                        next_live_poll = std::time::Instant::now() + std::time::Duration::from_secs(1);
                    }
                }
                let mut latest_market: Option<(u64, Option<serde_json::Value>)> = None;
                let mut latest_lab_select: Option<(u64, Option<serde_json::Value>)> = None;
                let mut latest_lab_run: Option<(u64, Option<serde_json::Value>)> = None;
                let mut latest_lab_coverage: Option<(u64, Option<serde_json::Value>)> = None;
                let mut latest_system: Option<Option<serde_json::Value>> = None;
                // Latest-wins coalesce: one tick applies at most ONE progress
                // event (the newest in this batch) instead of repainting per
                // arrival, so a burst never floods the UI thread.
                let mut latest_progress: Option<(u64, serde_json::Value)> = None;

                for result in fetch_rx.try_iter() {
                    match result {
                        FetchResult::Market(id, data) => {
                            if id > last_market
                                && latest_market
                                    .as_ref()
                                    .map_or(true, |(prev_id, _)| id > *prev_id)
                            {
                                latest_market = Some((id, data));
                            }
                        }
                        FetchResult::LabSelect(id, data) => {
                            if id > last_select
                                && latest_lab_select
                                    .as_ref()
                                    .map_or(true, |(prev_id, _)| id > *prev_id)
                            {
                                latest_lab_select = Some((id, data));
                            }
                        }
                        FetchResult::LabRun(id, data) => {
                            if id > last_run
                                && latest_lab_run
                                    .as_ref()
                                    .map_or(true, |(prev_id, _)| id > *prev_id)
                            {
                                latest_lab_run = Some((id, data));
                            }
                        }
                        FetchResult::LabCoverage(id, data) => {
                            if id > last_coverage
                                && latest_lab_coverage
                                    .as_ref()
                                    .map_or(true, |(prev_id, _)| id > *prev_id)
                            {
                                latest_lab_coverage = Some((id, data));
                            }
                        }
                        // Progress is coalesced below: keep only the latest per
                        // tick batch. Seq-guard: an event whose run id is not
                        // the currently expected run is stale — drop and count
                        // it instead of overwriting current facts.
                        FetchResult::LabProgress(id, event) => {
                            let expected = run_seq.load(Ordering::SeqCst);
                            if id != expected {
                                progress_dropped += 1;
                                eprintln!(
                                    "Lab progress: dropped stale event for run {id} (expected {expected}, {progress_dropped} dropped total)"
                                );
                                continue;
                            }
                            latest_progress = Some((id, event));
                        }
                        // No sequence: every system answer supersedes the
                        // previous one (it IS the latest backend truth), so
                        // the last arrival in this drain wins.
                        FetchResult::System(data) => {
                            latest_system = Some(data);
                        }
                        // The service's own snapshot replaces local state: it
                        // reports what actually happened, so a refused START
                        // comes back as a refusal, not as an optimistic toggle.
                        // Sequenced latest-wins: an answer older than the
                        // newest applied one is stale (a slow poll must not
                        // paint over a fresher action result).
                        FetchResult::Live(id, data) => {
                            live_busy = live_busy.saturating_sub(1);
                            if !is_remote && id > last_live {
                                last_live = id;
                                if let Some(data) = data {
                                    live_state.borrow_mut().apply_snapshot(&data);
                                    shell::apply_live(&ui, &live_state.borrow());
                                }
                            }
                        }
                    }
                }

                if let Some(data) = latest_system {
                    if let Some(data) = data {
                        let workspace = BrokerWorkspace::from_json(&data);
                        *current_workspace.borrow_mut() = workspace.clone();
                        shell::apply_connection(&ui, &workspace);
                    }
                }

                if let Some((id, data)) = latest_market {
                    last_market = id;
                    if let Some(data) = data {
                        market::apply_snapshot_json(&mut market_state.borrow_mut(), &data);
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
                            // Success retires the panel; failure/cancel keeps
                            // the evidence on screen instead of wiping it.
                            lab_state.borrow_mut().clear_progress();
                        }
                        None => lab_state.borrow_mut().fail_run(),
                    }
                    shell::apply_lab(&ui, &lab_state.borrow());
                }
                // One coalesced progress apply per tick (latest wins).
                if let Some((_, event)) = latest_progress {
                    lab_state.borrow_mut().apply_progress(&event);
                    shell::apply_lab_progress(&ui, &lab_state.borrow());
                }
                if let Some((id, data)) = latest_lab_coverage {
                    last_coverage = id;
                    if let Some(data) = data {
                        // An empty payload means "not measurable" — the strip
                        // stays hidden rather than showing a 0%.
                        if !data.is_null() {
                            lab::apply_coverage_json(&mut lab_state.borrow_mut(), &data);
                            shell::apply_lab(&ui, &lab_state.borrow());
                        }
                    }
                }
            },
        );
    }
    shell::apply_connection(&ui, &current_workspace.borrow());

    // Wire broker connection interactions
    {
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        ui.on_broker_select_requested(move |id: slint::SharedString| {
            let id_str = id.as_str().to_string();
            println!("Broker select requested: {}", id_str);
            // Background worker + fetch pump (same as the market refetch):
            // the backend round-trip used to run on the UI thread here
            // (lock_send blocks until the response arrives), freezing the
            // window for the whole round-trip. The answer lands as
            // FetchResult::System and applies on the 50ms pump.
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::GetSystemSnapshot {
                    selected_id: Some(id_str),
                },
                FetchResult::System,
            );
        });
    }
    {
        let backend = Arc::clone(&backend);
        let tx = fetch_tx.clone();
        ui.on_broker_refresh_requested(move || {
            println!("Broker refresh requested");
            spawn_fetch(
                &backend,
                &tx,
                BackendCommand::GetSystemSnapshot { selected_id: None },
                FetchResult::System,
            );
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
            const SAVED_SENTINEL: &str =
                "\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}\u{2022}";
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
                match PythonBackend::lock_send(
                    &backend_bg,
                    BackendCommand::ConnectBroker {
                        broker_id,
                        credentials,
                    },
                ) {
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
                                ui.set_conn_error_message(
                                    format!("Connection IPC error: {err_str}").into(),
                                );
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
                match PythonBackend::lock_send(
                    &backend_bg,
                    BackendCommand::ConnectBroker {
                        broker_id,
                        credentials: std::collections::HashMap::new(),
                    },
                ) {
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
                                ui.set_conn_error_message(
                                    format!("Connection IPC error: {err_str}").into(),
                                );
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
                match PythonBackend::lock_send(
                    &backend_bg,
                    BackendCommand::DisconnectBroker { broker_id },
                ) {
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
        // REMOVE. The backend protocol has exactly ONE destructive broker
        // command — `disconnect_broker` — and no remove/delete verb. Rather
        // than inventing a wire message the Python side does not understand
        // (which would answer "Unknown command" and leave the user with a
        // button that looks broken), REMOVE performs the real destructive
        // action that DOES exist: it disconnects the session — then states
        // plainly that the stored broker configuration was NOT removed,
        // because no backend command does that. Silently pretending the
        // profile was deleted while the credentials are still on disk would
        // be the worse lie.
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_remove_requested(move || {
            let broker_id = cur_ws.borrow().selected_id.clone();
            println!("Broker remove requested for '{}'", broker_id);
            let handle_bg = handle.clone();
            let backend_bg = Arc::clone(&backend);
            std::thread::spawn(move || {
                let result = PythonBackend::lock_send(
                    &backend_bg,
                    BackendCommand::DisconnectBroker {
                        broker_id: broker_id.clone(),
                    },
                );
                match result {
                    Ok(BackendResponse::SystemSnapshot { data }) => {
                        let workspace = BrokerWorkspace::from_json(&data);
                        let _ = slint::invoke_from_event_loop(move || {
                            if let Some(ui) = handle_bg.upgrade() {
                                shell::apply_connection(&ui, &workspace);
                                ui.set_conn_error_message(
                                    format!(
                                        "Removed '{broker_id}' is not supported: the session was \
                                         disconnected, but the stored credentials are still on disk."
                                    )
                                    .into(),
                                );
                            }
                        });
                    }
                    Ok(other) => {
                        eprintln!("Broker remove: unexpected response ({other:?})");
                    }
                    Err(err) => {
                        eprintln!("Broker remove failed: {err}");
                    }
                }
            });
        });
    }
    {
        // CONFIG SAVE. The backend exposes no standalone "save one field"
        // command either: credentials are persisted as part of `connect_broker`
        // (which merges the typed values into the vault and then configures the
        // venue). Inventing a command here would be a no-op that reports
        // success. So the honest wiring is to route the typed key/value pair
        // through the EXISTING connect path, which is what actually stores it.
        let handle = ui.as_weak();
        let backend = Arc::clone(&backend);
        let cur_ws = current_workspace.clone();
        ui.on_broker_config_saved(
            move |key: slint::SharedString, value: slint::SharedString| {
                let key = key.as_str().trim().to_string();
                let value = value.as_str().trim().to_string();
                if key.is_empty() {
                    return;
                }
                let broker_id = cur_ws.borrow().selected_id.clone();
                println!("Broker config saved for '{}' (key '{}')", broker_id, key);
                let mut credentials = std::collections::HashMap::new();
                if !value.is_empty() {
                    credentials.insert(key, value);
                }
                let handle_bg = handle.clone();
                let backend_bg = Arc::clone(&backend);
                std::thread::spawn(move || {
                    match PythonBackend::lock_send(
                        &backend_bg,
                        BackendCommand::ConnectBroker {
                            broker_id,
                            credentials,
                        },
                    ) {
                        Ok(BackendResponse::SystemSnapshot { data }) => {
                            let workspace = BrokerWorkspace::from_json(&data);
                            let _ = slint::invoke_from_event_loop(move || {
                                if let Some(ui) = handle_bg.upgrade() {
                                    shell::apply_connection(&ui, &workspace);
                                }
                            });
                        }
                        Ok(BackendResponse::Error { data }) => {
                            eprintln!("Broker config save failed: {}", data.message);
                            let msg = data.message.clone();
                            let _ = slint::invoke_from_event_loop(move || {
                                if let Some(ui) = handle_bg.upgrade() {
                                    ui.set_conn_is_failed(true);
                                    ui.set_conn_error_message(msg.into());
                                }
                            });
                        }
                        Ok(other) => {
                            eprintln!("Broker config save: unexpected response ({other:?})");
                        }
                        Err(err) => {
                            eprintln!("Broker config save IPC error: {err}");
                        }
                    }
                });
            },
        );
    }
    {
        // COPY CALLBACK URL. A real clipboard write through the same
        // `clipboard-win` crate the PASTE path already uses (Windows-only, so
        // other platforms report unavailable rather than silently doing
        // nothing). The text is the selected broker's identity + environment —
        // the only facts the workspace actually holds; no URL is invented.
        let handle = ui.as_weak();
        let cur_ws = current_workspace.clone();
        ui.on_broker_copy_callback(move || {
            let ws = cur_ws.borrow();
            if ws.selected_id.is_empty() {
                eprintln!("Broker copy: no broker selected — nothing to copy");
                return;
            }
            let text = format!(
                "{} [{}] — {}",
                ws.display_name, ws.selected_id, ws.env_label
            );
            #[cfg(windows)]
            let copied = clipboard_win::set_clipboard_string(text.as_str()).is_ok();
            #[cfg(not(windows))]
            let copied: bool = {
                eprintln!("Broker copy: clipboard unavailable on this platform");
                false
            };
            if copied {
                println!("Broker callback details copied to clipboard");
            }
            if let Some(ui) = handle.upgrade() {
                ui.set_conn_status_message(format!("Copied to clipboard: {text}").into());
            }
        });
    }
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
                println!(
                    "Broker paste requested for field {idx}: length {}",
                    trimmed.len()
                );
                if let Some(ui) = handle.upgrade() {
                    let s = slint::SharedString::from(trimmed);
                    // Indexed, NOT defaulted: the old `_ => v5` arm meant any
                    // out-of-range index silently OVERWROTE the sixth
                    // credential field (usually the secret) with whatever was
                    // on the clipboard. An unknown index is a no-op.
                    match idx {
                        0 => ui.set_broker_field_v0(s),
                        1 => ui.set_broker_field_v1(s),
                        2 => ui.set_broker_field_v2(s),
                        3 => ui.set_broker_field_v3(s),
                        4 => ui.set_broker_field_v4(s),
                        5 => ui.set_broker_field_v5(s),
                        _ => eprintln!("Broker paste ignored: no field {idx}"),
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
    // default stays Lab in local mode, Live in remote mode.
    let initial = if is_remote && !argv.iter().any(|a| a.starts_with("--screen")) {
        shell::initial_screen(&["--screen".to_string(), "live".to_string()])
    } else {
        shell::initial_screen(&argv)
    };
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

/// Remote graphical client (read-only; also the packaged default).
///
/// Startup order: paint CONNECTING immediately → bootstrap thread proves the
/// token (`hello`), paints AUTHENTICATING while it does, applies the first
/// snapshot, subscribes, and hands the live client to the pump thread
/// (RECONNECTING with `subscribe{last_seq}` on drops; `resync:true` replaces
/// state). The 50ms UI timer applies arrivals — the UI thread never blocks
/// on the network, and a dead link never freezes the window.
///
/// A missing or rejected credential paints OFFLINE with the one-time setup
/// note instead of exiting: the bootstrap keeps retrying (1s→30s backoff,
/// token re-resolved per attempt), so provisioning the OS credential later
/// connects with zero restart. No secret is ever painted or logged.
///
/// Read-only by construction: no call site in this binary sends a command,
/// and `bridge_wired` stays false so the model keeps START/STOP/HALT inert
/// with honest feedback. Other workspaces (Market/Lab/Portfolio/Research,
/// SYSTEM broker panel) render honest-empty: the remote gateway does not
/// serve their local stores in this phase, and nothing is invented for them.
#[allow(dead_code)]
fn run_remote_ui(argv: Vec<String>, url: String) -> Result<(), Box<dyn std::error::Error>> {
    use vayren_shell::remote_live::{
        apply_remote_sections, init_remote_state, mark_link, push_remote_event,
        spawn_remote_bootstrap, RemoteUiUpdate,
    };

    println!("VAYREN starting (remote graphical client)...");
    println!("Gateway: {url}");

    let live_state = init_remote_state();

    println!("Initializing Slint UI...");
    let ui = vayren_shell::AppWindow::new()?;
    let zoom = Rc::new(RefCell::new(
        vayren_shell::viewport::ChartViewportZoom::default(),
    ));
    let live_state = Rc::new(RefCell::new(live_state));
    shell::wire(&ui);
    shell::wire_zoom(&ui, zoom.clone());
    shell::wire_live(&ui, live_state.clone());
    shell::wire_portfolio(
        &ui,
        Rc::new(RefCell::new(portfolio::PortfolioState::default())),
    );
    shell::wire_research(
        &ui,
        Rc::new(RefCell::new(research_state::demo_research_state())),
    );
    shell::apply(&ui, &shell::demo_snapshot());
    shell::apply_connection(&ui, &BrokerWorkspace::empty());
    shell::apply_market(&ui, &market::MarketState::default());
    shell::apply_lab(&ui, &shell::demo_lab_state());
    shell::apply_portfolio(&ui, &portfolio::PortfolioState::default());
    shell::apply_research(&ui, &research_state::demo_research_state());
    shell::apply_live(&ui, &live_state.borrow());
    shell::apply_zoom(&ui, &zoom.borrow());

    let (remote_tx, remote_rx) = mpsc::channel::<RemoteUiUpdate>();
    // Bootstrap owns the first connect (with enrollment retries); on success
    // it hands the live client to the pump and exits.
    spawn_remote_bootstrap(url, remote_tx);
    let fetch_timer = slint::Timer::default();
    {
        let weak = ui.as_weak();
        let live_state = live_state.clone();
        fetch_timer.start(
            slint::TimerMode::Repeated,
            std::time::Duration::from_millis(50),
            move || {
                let Some(ui) = weak.upgrade() else { return };
                // Inert controls stay inert: the model refuses to queue when
                // unwired, so anything here is drained and dropped with a
                // log line — never forwarded, never sent.
                while let Some(raw) = live_state.borrow_mut().take_action() {
                    let name: String = serde_json::from_str::<serde_json::Value>(&raw)
                        .ok()
                        .and_then(|v| v.get("action").and_then(|a| a.as_str()).map(str::to_string))
                        .unwrap_or_else(|| "unknown".to_string());
                    eprintln!(
                        "remote UI: control '{name}' refused (read-only client, nothing sent)"
                    );
                }
                let mut dirty = false;
                for update in remote_rx.try_iter() {
                    match update {
                        RemoteUiUpdate::Snapshot { sections, resync } => {
                            if resync {
                                println!("Remote resync: replacing state with fresh snapshot");
                            }
                            apply_remote_sections(&mut live_state.borrow_mut(), &sections, resync);
                            dirty = true;
                        }
                        RemoteUiUpdate::StateUpdate { sections } => {
                            apply_remote_sections(&mut live_state.borrow_mut(), &sections, false);
                            dirty = true;
                        }
                        RemoteUiUpdate::StreamEvent { name, payload } => {
                            push_remote_event(&mut live_state.borrow_mut(), &name, &payload);
                            dirty = true;
                        }
                        RemoteUiUpdate::Link {
                            label,
                            detail,
                            reconnects,
                        } => {
                            eprintln!("remote UI: link {label} ({detail})");
                            mark_link(&mut live_state.borrow_mut(), &label, &detail, reconnects);
                            dirty = true;
                        }
                    }
                }
                if dirty {
                    shell::apply_live(&ui, &live_state.borrow());
                }
            },
        );
    }
    let initial = shell::initial_screen(&argv);
    shell::select(&ui, initial);
    println!("VAYREN UI ready (remote) - entering event loop");
    ui.run()?;

    println!("VAYREN shutdown complete");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_garbage_limit_is_rejected_not_treated_as_no_limit() {
        // `--limit abc` used to parse to None, which the bridge reads as "no
        // limit" — silently upgrading a bounded startup fetch into an
        // UNBOUNDED one. An explicit flag that cannot be understood is an
        // error, not a default.
        assert_eq!(parse_limit(None).expect("absent flag"), None);
        assert_eq!(
            parse_limit(Some("150".to_string())).expect("int"),
            Some(150)
        );
        assert_eq!(
            parse_limit(Some(" 150 ".to_string())).expect("int"),
            Some(150)
        );
        assert_eq!(parse_limit(Some("0".to_string())).expect("zero"), Some(0));
        for bad in ["abc", "", "  ", "1.5", "-1", "150k", "١٥٠"] {
            assert!(
                parse_limit(Some(bad.to_string())).is_err(),
                "--limit {bad:?} must be rejected"
            );
        }
    }

    #[test]
    fn arg_value_reads_both_flag_spellings() {
        let argv: Vec<String> = ["--symbol", "RELIANCE", "--limit=150", "--flag"]
            .iter()
            .map(|s| s.to_string())
            .collect();
        assert_eq!(arg_value(&argv, "--symbol").as_deref(), Some("RELIANCE"));
        assert_eq!(arg_value(&argv, "--limit").as_deref(), Some("150"));
        assert_eq!(arg_value(&argv, "--missing"), None);
    }

    #[test]
    fn home_dir_is_never_empty_so_data_lands_in_a_stable_place() {
        // A blank home used to fall through to ".", putting the data folder in
        // whatever CWD the app was launched from.
        let home = home_dir();
        assert!(!home.trim().is_empty());
    }
}
