# Cargo Build Experiments — VAYREN

> Permanent record of Cargo / Rust release-build performance investigations.
> Read this BEFORE starting any build-speed work. Do not repeat a finished
> experiment unless new evidence justifies it.

**Owns:** build-performance experiment history, measured numbers, open cleanup items, future-session checklist.
**Not owns:** current product state (`docs/ai_memory.md`), build rules (`AGENTS.md`), ownership (`AI_ENTRY.md`).

Source revision at time of investigation: `b916597` (git HEAD).
Toolchain: `rustc 1.98.1 (48a229cea 2026-09-01)`, host/target `x86_64-pc-windows-msvc`.
Machine: 12 logical CPUs (`i5-1235U`, 10 cores / 12 threads), `jobs=12`.

---

## 1. Baseline release build (production profile)

Command: `cargo build --release -p vayren-shell --features packaged-remote --timings`

| Metric | Value |
|---|---|
| Total build duration | 695.7 s (11 m 35.7 s) |
| Cargo units | 613 total, 611 fresh, 2 dirty (mostly incremental) |
| Max concurrency | 1 (jobs=12, ncpu=12) |
| `vayren-shell` lib total | 679.1 s |
| — Frontend | 206.1 s (30%) |
| — Codegen | 473.0 s (70%) |
| `vayren-shell` bin | 9.7 s |
| Production binary size | 35,910,656 bytes |

Interpretation: the build was mostly cached. The dominant cost was the
`vayren-shell` lib unit's rustc codegen, which runs as a single unit.

---

## 2. Experimental profile: `release-cgu256` (REJECTED)

Profile added to `Cargo.toml`:

```toml
[profile.release-cgu256]
inherits = "release"
codegen-units = 256
```

Command (separate target dir, production output untouched):
`CARGO_TARGET_DIR=<repo>\target-exp cargo build --profile release-cgu256 -p vayren-shell --features packaged-remote --timings`

| Metric | Value |
|---|---|
| Total build duration | 1,092.1 s (18 m 12.1 s) |
| Cargo units | 613 total, 0 fresh, 613 dirty (full rebuild) |
| Max concurrency | 13 |
| `vayren-shell` lib total | 704.4 s |
| — Frontend | 218.9 s (31%) |
| — Codegen | 485.5 s (69%) |
| Experimental binary size | 36,124,672 bytes (+214,016 bytes, +0.6%) |

### Outcome

**Do not recommend `codegen-units = 256`.** The `vayren-shell` codegen did
not get faster (473.0 s → 485.5 s), total wall time rose, and the binary grew.

### Caveats (important)

- The two builds were NOT comparable as a whole: baseline was incremental
  (611 fresh units), experimental was a full rebuild (613 dirty). Only the
  `vayren-shell` unit numbers are a fair signal, and they point the same way.
- Max concurrency 13 in the experiment shows that more parallel dependency
  work was possible, but it did not reduce the `vayren-shell` critical path.

---

## 3. Verified facts

- `[profile.release]` in `Cargo.toml`: `opt-level = 3`, `lto = false`,
  `codegen-units = 16`, `incremental = true`. **Keep as-is.**
- `crates/vayren-shell/build.rs` already content-hashes Slint inputs
  (FNV-1a 64) and skips `slint_build::compile` when the generated file and
  inputs are unchanged. It also emits `SLINT_INCLUDE_GENERATED` on the skip
  path (required, see `docs/ai_memory.md`).
- Slint input: 14 `.slint` files under `crates/vayren-shell/ui/`, about
  684 KB total (largest: `lab.slint` 208 KB, `market.slint` 125 KB).
- **CORRECTION (see §9).** The earlier "16.8 KB" figure for the generated
  `app.rs` is wrong by three orders of magnitude. Measured 2026-10-14 on
  `rustc 1.98.1`: `OUT_DIR/app.rs` is **19,145,181 bytes / 191,847 lines**,
  plus `research_harness.rs` **8,548,704 bytes / 90,020 lines** (which used
  to be compiled into the production lib as well) and
  `live_harness.rs` **3,945,521 bytes** (`#[cfg(test)]`, never in release).
  The generated Slint Rust — not the hand-written 12,123 lines — is what
  makes the `vayren-shell` unit expensive.
- `sccache`, `lld-link`, `rust-lld` were NOT found on PATH at the time of
  measurement. `.cargo/config.toml` references `rust-lld.exe` as the MSVC
  linker, but PATH lookup did not find it.
- An earlier incremental release build (`--timings`) exceeded a 15-minute
  tool timeout and produced no timing report. The cancelled run is not a
  valid measurement. Only the completed runs above are.

---

## 4. Environment finding: `CARGO_TARGET_DIR` (OPEN, root cause verified)

Verified values (read-only, no change made):
- PROCESS: `C:\Users\visha\Desktop\vayren-core-main\target`
- USER: `C:\Users\visha\Desktop\vayren-core-main\target` (persistent)
- MACHINE: unset
- `.cargo/config.toml` (repo): sets only the MSVC linker (`rust-lld.exe`), no
  target dir. No `.cargo/config` at Desktop, user, or CARGO_HOME levels.
- `CARGO_BUILD_TARGET_DIR`, `CARGO_HOME`: unset.

Verified cause of the `vayren-core-main` rebuild:
- Observed behavior: builds run without an explicit override wrote to
  `vayren-core-main\target`. The User-scope variable is the only target-dir
  setting found, so it is the most likely source (consistent with all
  checks above; not isolated by a controlled test).
- Evidence: `vayren-core-main\target\cargo-timings\cargo-timing-20261011T055635076Z-*.html`
  is the baseline build (the one run with the session override not set), and
  its fingerprints / dep-info reference `vayren-core\crates` sources. The
  SOURCE was always `vayren-core`; only the OUTPUT folder came from the env var.
- Earlier mistake (recorded for honesty): setting `CARGO_TARGET_DIR` in one
  PowerShell session did not stop the user-scope value from being used by
  other commands. The env override must be applied on every build command.

Hypothesis (NOT verified by a run): whether removing the User variable changes
any other tool. Risk is low but unmeasured.

Status: FIXED (User-scope variable removed). Verified after the change:
- USER scope: absent (read via the registry `HKCU\Environment`).
- MACHINE scope: unset (unchanged).
- Shells and Antigravity started before the change may still show the old
  value in their environment until restarted.
- Default Cargo target directory is `C:\Users\visha\Desktop\vayren-core\target`.
- Production `target\release\vayren-shell.exe` present, 35,910,656 bytes.
- `vayren-core-main\target` retained (NOT deleted).

Previous status (before removal): the User variable was still set. Changing it is a
system-wide change and needs explicit user approval. Fix options (user action):
`setx CARGO_TARGET_DIR ""` or delete the User variable in Windows Settings,
then open a new terminal and verify with `echo %CARGO_TARGET_DIR%`.

---

## 5. Cleanup status

Completed and verified:
- `[profile.release-cgu256]` removed from `Cargo.toml`. `git diff -- Cargo.toml`
  is empty (file matches the committed version). Verified by git.
- `target-exp/` DELETED (3.88 GB, 5,422 files reclaimed). Before deletion it
  held only experiment output (`release-cgu256/`, `cargo-timings/`,
  `CACHEDIR.TAG`, `.rustc_info.json`). Path was verified as a direct child of
  the repo root and not an ancestor or child of `target/`.
- Production `target\release\vayren-shell.exe` verified after deletion:
  35,910,656 bytes, unchanged.

Not done (needs approval):
- `vayren-core-main\target` (the folder `CARGO_TARGET_DIR` points to) still
  exists, containing the baseline build output (~ several GB). Not deleted.
- Environment variable `CARGO_TARGET_DIR` at USER scope is still set to
  `C:\Users\visha\Desktop\vayren-core-main\target`. Not modified.

---

## 6. Future-session checklist

Before starting any Cargo build-performance work:

1. Read this file first.
2. Do NOT repeat the `codegen-units = 256` experiment unless new evidence
   justifies it.
3. Do NOT start a long release build (10+ min) without a specific
   measurement goal written down first.
4. Check `CARGO_TARGET_DIR` (process AND user scope) without changing
   system settings before any build run.
5. Keep the production executable and `[profile.release]` safe. Experiments
   use a separate profile name and a separate target dir.
6. For every new experiment, record: command, toolchain (`rustc -V`), source
   revision (`git rev-parse --short HEAD`), profile, cache state (fresh vs
   dirty unit counts), total time, `vayren-shell` frontend/codegen split,
   max concurrency, binary size, and outcome.
7. Do not delete `target-exp` or run `cargo clean` without explicit approval.
8. Do not claim an improvement without measured before/after numbers taken
   under comparable cache conditions.

---

## 7. Ideas NOT yet tested (candidates only, no evidence yet)

- `sccache` for repeat clean builds (only helps repeated identical compiles;
  not installed).
- `rust-lld` / alternative linker verification (PATH lookup failed; check
  the actual linker path before testing).
- Splitting Slint UI or `vayren-shell` into smaller crates. Only justified
  if a timing report shows the `vayren-shell` codegen unit is still the
  critical path after cheaper fixes. Architectural change: needs approval.

---

## 8. Re-validation of §7 candidates — 2026-10-14 (`7ebe4d5`, rustc 1.98.1)

This session re-ran the open questions from §7 on the current checkout with a
cold, empty `target/` (the previous cache no longer existed on disk).

### 8.1 The 13-minute delay is NOT a linker problem

- `.cargo/config.toml` sets `linker = "rust-lld.exe"` for
  `x86_64-pc-windows-msvc`. `rust-lld.exe` is **not** on `PATH`, and the
  §3 note "PATH lookup did not find it" is misleading: rustc resolves it.
  Direct experiment (`rustc hello.rs -C linker=rust-lld.exe -o t.exe` in a
  scratch dir) produced a working 133,632-byte executable. The fast LLD
  linker is therefore **already active** for every MSVC build here.
- A cold `cargo build --release -p vayren-shell --features packaged-remote
  --timings` took **1,079 s (17 m 59 s)** over **613 cargo units**, and cargo's
  own `--timings` attribution resolves the delay completely:

```
duration  : 1079 s     units: 613     cpu work: 4191.9 s   parallelism = 3.89x
SECTION TOTALS: codegen 2730.70s   frontend 1158.58s
WORKSPACE UNITS
  257.0 +  43.6 =  300.6   vayren-core
  269.6 +  15.2 =  284.8   vayren-remote-client
  275.0 +   7.3 =  282.4   vayren-shell build-script
  282.4 +  23.7 =  306.1   vayren-shell build-script (run)
  300.6 +  22.9 =  323.5   vayren-domain
  306.9 + 751.3 = 1058.2   vayren-shell   <-- 69.7% of the whole build
 1058.2 +  20.1 = 1078.3   vayren-shell (bin)
```

**Root cause: one crate.** Every other unit finishes by t=323 s; the
`vayren-shell` lib then runs 751 s single-threaded
(frontend 218.6 s + codegen 532.8 s). Link time is 20 s total. The delay is
rustc work inside one compilation unit, and §3's corrected generated-code
sizes explain why: that unit pulls in ~27.7 MB of macro-generated Rust
(`app.rs` 19.1 MB + `research_harness.rs` 8.5 MB) on top of 12,123 lines of
hand-written code.

### 8.2 Why incremental release builds were NOT already "fast"

`[profile.release] incremental = true` was already set, and it does work —
but §3's earlier profile was recorded on a much smaller unit. Measured
localized-rebuild cost (same protocol as §9's table):

| localized change | before | `vayren-shell` lib |
|---|---|---|
| `vayren-domain/src/live.rs` | 43.2 s | 36.38 s (frontend 27.3 / codegen 9.1) |
| `vayren-core/src/order_state.rs` | 96.9 s | 85.97 s (frontend 27.4 / codegen 58.5) |
| `ui/market.slint` (real property) | n/a | 36–42 s |

The `vayren-core` case is the interesting one: a one-line change in the
lowest leaf crate costs 97 s because the whole downstream chain
(`vayren-core → vayren-domain → vayren-shell lib + bin`) invalidates, and the
shell lib's codegen has to re-emit more CGUs than in the other cases.

**Caution on those two numbers.** They were single samples. §9.6's 3×
repeated-edit protocol on an isolated pristine worktree gives 40.4 s
(`order_state.rs`) and 39.7 s (`live.rs`), not 96.9 / 43.2 s: the first
incremental rebuild after a cold build pays more than the steady state.
Always use the 3× protocol before quoting a number.

### 8.3 Conclusion for §7

- `sccache`: still not installed, and it would not help these numbers (it
  only removes compiler work that is *identical* between cold builds; the
  observed bottleneck is a single crate's own rustc invocation, which is
  already reusing its incremental cache on rebuilds).
- `rust-lld` verification: CLOSED — rust-lld is in use and resolving. No
  change made.
- Splitting `vayren-shell` into smaller crates: remains the only lever on the
  remaining 750 s cold-build critical path. It was **not** attempted: all
  screens are a single `AppWindow` in `ui/app.slint`, so the generated code
  cannot be partitioned without a UI refactor. Recorded as the open item.
- `codegen-units = 256`: still rejected, and the new data strengthens it —
  the shell lib's frontend (218.6 s cold) is 30–70% of its own time, and more
  CGUs do nothing for frontend work.

---

## 9. IMPLEMENTED — test-only Slint harness removed from the production lib

**Change (2 files, no profile or manifest change, no behavior change):**

1. `crates/vayren-shell/src/lib.rs` — `pub mod research_harness_ui` is now
   `#[cfg(test)]`, matching `live_harness_ui` below it.
2. `crates/vayren-shell/tests/render_matrix.rs` — declares the same generated
   module locally
   (`mod research_harness_ui { include!(concat!(env!("OUT_DIR"),
   "/research_harness.rs")) }`), because integration tests cannot see
   `#[cfg(test)]` items of the lib they link.

**Evidence for the change.** `research_harness_ui` had zero production
callers (grep over `src/` and `tests/`: only `shell.rs`'s own `mod tests` and
`render_matrix.rs`). It `include!`s the generated `research_harness.rs`
(8,548,704 bytes / 90,020 lines) into **every** non-test build of the lib,
i.e. ~31% of the crate's ~27.7 MB of generated Rust, for a Window nothing
production ever instantiates. `live_harness_ui` was already `#[cfg(test)]`,
which is why the release build ended up compiling the LIVE harness twice but
the research harness once; both are now test-only.

`OUT_DIR` is set by cargo for every target of a package that has a build
script, so the integration test compiles the harness into its own binary
instead of the shipped rlib — no `cargo:rustc-env` was needed and the build
script is untouched.

### 9.1 Measured result (identical protocol, same warm incremental cache)

**These are single-sample / first-rebuild figures — use §9.6's 3× matrix as the
definitive before/after.** They are kept because they are what first revealed
the effect and they carry the `--timings` per-unit attribution.

| scenario (change → `cargo build --release -p vayren-shell --features packaged-remote`) | BEFORE | AFTER | shell lib (after) | speedup |
|---|---|---|---|---|
| repeat, no change | 4.1 s | (unchanged path) | — | — |
| `vayren-core/src/order_state.rs` | 96.9 s | **32.8 s** | 24.56 s (frontend 18.6 / codegen 5.9) | **2.95x** |
| `vayren-domain/src/live.rs` | 43.2 s | **31.9 s** | 24.91 s (frontend 18.8 / codegen 6.1) | **1.35x** |
| `ui/market.slint` (real `private property`) | n/a | **38.5 s** | 24.57 s (frontend 18.6 / codegen 6.0) | — |

`vayren-shell` lib per-run attribution (parsed with `tools/parse_timings.py`):

```
cargo-timing-...222054893Z.html   frontend=218.55  codegen=532.76  (cold)
cargo-timing-...224320049Z.html   frontend= 27.44  codegen= 58.53  (core change, before)
cargo-timing-...224549977Z.html   frontend= 27.32  codegen=  9.06  (domain change, before)
cargo-timing-...230138169Z.html   frontend= 18.64  codegen=  5.92  (core change, after)
cargo-timing-...230051763Z.html   frontend= 18.77  codegen=  6.14  (domain change, after)
cargo-timing-...230505313Z.html   frontend= 18.57  codegen=  6.00  (slint change, after)
```

The frontend of the `vayren-shell` lib dropped from ~27.3 s to ~18.6 s
(−32%) and its codegen in the `vayren-core` case from 58.5 s to 5.9 s.

### 9.2 Honest second-order effect (must not be hidden)

The FIRST build after the `lib.rs` structural change cost **485.4 s**
(`vayren-shell` lib 471.20 s: frontend 56.7 / codegen 414.5). Removing
8.5 MB from a crate invalidates the shape of its incremental cache, so the
first rebuild is a near-full recompile; the steady state above is what
repeats. Anything that changes the *shape* of the crate pays this once.
This is why §9.6's 3× protocol matters — the same first-vs-steady-state
distortion is what made the earlier `order_state.rs` "before" number range
from 40.4 s to 96.9 s.

### 9.3 Validation

- `cargo fmt --manifest-path Cargo.toml --all -- --check` → 0 errors.
- `cargo check -p vayren-shell --all-targets` → exit 0 (354 s, dev profile,
  cold for that profile).
- `cargo test -p vayren-shell` → **95 passed, 0 failed** (incl.
  `render_matrix::render_research_matrix`, the test that now owns the local
  harness module).
- `cargo test --workspace --exclude vayren-shell` → 21 `test result:` lines,
  all `ok`, 0 failed (761 tests across the 9 other workspace members).
- `cargo test --release -p vayren-core -p vayren-domain` → 694 passed,
  0 failed under the production `opt-level = 3` profile.
- Release binary smoke test (read-only console probe
  `vayren-shell.exe --remote wss://127.0.0.1:9/vayren/v1`): exits in 3.1 s with
  `remote connect failed ... Unable to connect` — the launch contract
  resolved, no fallback, no crash, no backend spawned, no command sent.
- `python -m pytest -q` → **1528 passed, 2 skipped, 0 failed**.
- `docs/repo_graph.json` regenerated (`python tools/repo_graph.py --build`,
  5862 entities / 12983 relationships / 0 unresolved) because the test module
  moved; `validate_repo_graph` now passes.
- All 7 other Python validators pass. `validate_scope.py --stats` exits 2 **by
  design** — with no task it prints help; that is pre-existing and unrelated.

### 9.4 Production binary is unchanged

| build | bytes |
|---|---|
| before (cold `target/release`, `run1`) | 37,924,352 |
| after (cold, isolated `target-after-clean`) | 37,912,064 |

The removed module was entirely dead code that LLVM had already eliminated at
`opt-level = 3`, so the shipped EXE is 12,288 bytes (−0.03%) *smaller*.
What changed is only the time it takes to reach that identical result.

### 9.5 Isolated clean-cache build (fresh `CARGO_TARGET_DIR`)

Command (own target dir, production `target/` and its cache untouched):
`$env:CARGO_TARGET_DIR=<repo>\target-after-clean; cargo build --release -p vayren-shell --features packaged-remote --timings`

```
  BEFORE (run 1, cold)                     AFTER (clean cache)
duration  : 1079 s (17.99 min)             duration  :  891 s (14.85 min)
units     : 613                            units     : 613
parallelism: 3.89x                         parallelism: 4.72x
vayren-shell lib   751.31 s                vayren-shell lib   549.26 s
  frontend         218.55 s                  frontend         126.50 s  (-42.1%)
  codegen          532.76 s                  codegen          422.76 s  (-20.6%)
bin                20.07 s                  bin                13.73 s
TOTAL           = 1078.3 s                 TOTAL           =  890.8 s
```

**Full cold build: 1,079 s → 891 s (−188 s, −17.4%).** The dependency half of
the graph is unchanged (~328 s to first rustc of the workspace); the whole
improvement comes out of the one crate that was the critical path.
`target-after-clean/` (3.17 GB, 4,670 files) was deleted after the timing
report was extracted — same cleanup protocol §5 used for `target-exp/`.

### 9.6 Complete before/after matrix — isolated pristine worktree, 3× protocol

One caveat on every number above: they are first-rebuild or single-sample
figures. This is the definitive matrix, measured with an **identical protocol
on both sides**: a pristine `git worktree` at `HEAD 7ebe4d5` (no opt-1) with
its own `CARGO_TARGET_DIR`, versus the working tree with opt-1. Every
scenario ran **three successive small edits** (real edits, each a distinct
minimal content change), and the median is reported. Three samples, not one.

Protocol (identical on both trees):
`cargo build --release -p vayren-shell --features packaged-remote`

| localized change (3 successive edits, median) | BEFORE (pristine HEAD) | AFTER (opt-1) | speedup |
|---|---|---|---|
| `vayren-core/src/order_state.rs` (kernel impl) | 40.4 s | 26.2 s | **1.54x** |
| `vayren-domain/src/live.rs` (shared domain) | 39.7 s | 25.9 s | **1.53x** |
| `vayren-domain/src/broker_connection.rs` | 40.1 s | 25.7 s | **1.56x** |
| `vayren-remote-client/src/client.rs` (gateway/network) | 41.1 s | 26.0 s | **1.58x** |
| `vayren-shell/src/shell.rs` (shell impl) | 39.1 s | 25.2 s | **1.55x** |
| `ui/market.slint` (real `private property`) | 46.5 s | 31.3 s | **1.49x** |

First build after a **cold** cache, each in its own target dir:

| | BEFORE (pristine worktree, cold) | AFTER (opt-1, cold) |
|---|---|---|
| total | 975 s (16.25 min) | 891 s (14.85 min) |
| `vayren-shell` lib | 641.91 s | 549.26 s |
| — frontend | 181.08 s | 126.50 s |
| — codegen | 460.83 s | 422.76 s |

Cold builds carry more machine-load variance than the 3× protocol, so
prefer the table above it when quoting a number.

**Which crates each change invalidated** (from the `Compiling` lines):

```
BEFORE / AFTER identical — opt-1 changes cost, not blast radius:
  vayren-core/src/*            -> vayren-core, vayren-domain, vayren-shell(lib+bin)
  vayren-domain/src/*          -> vayren-domain, vayren-shell(lib+bin)
  vayren-remote-client/src/*   -> vayren-remote-client, vayren-shell(lib+bin)
  vayren-shell/src/shell.rs    -> vayren-shell(lib+bin)              [no downstream]
  ui/market.slint              -> vayren-shell build-script, vayren-shell(lib+bin)
  the six vayren-*-view crates are NOT rebuilt by any of these (they do
  not depend on vayren-shell; they compile their own copy of the screens)
```

The six `vayren-*-view` crates are the reason a change in `vayren-core` or
`vayren-domain` costs ~40 s while a change in `vayren-shell/src/` costs
~39 s: the chain is `crate → vayren-domain → vayren-shell lib → bin`, and
the bin link (~13 s cold / ~2 s warm) is paid every time. Those numbers are
the floor set by one crate (`vayren-shell`) plus its bin, and every other
rustc on the path is ≤ 1 s incremental.

### 9.7 Dependency audit (evidence, no removals justified)

Ground truth comes from cargo's own `--timings` unit list, not `cargo tree`
paths: 613 units, 350 distinct crates. **171 crates are compiled more than
once, contributing 263 extra units (43% of the whole build).**

| audit item | finding |
|---|---|
| Duplicate units owned by VAYREN | **0.** The only VAYREN crate with >1 unit is `vayren-shell` x4 = lib + build-script + `build-script (run)` + bin — the normal, non-duplicating set. |
| Worst third-party duplicates | `serde_core` x6, `num-traits` x6, `crossbeam-utils` x6, `slotmap` x6 (2 feature sets each); `libm`, `crc32fast`, `pulldown-cmark`, `thiserror`, the six `icu_*_data` x5 (build-script/proc-macro host variants). |
| Non-unifiable version pairs | `syn` 2.0.119 + 3.0.5, `hashbrown` 0.14/0.16/0.17, `read-fonts` 0.39/0.41, `skrifa` 0.42/0.44, `windows-sys` 0.52/0.61, `getrandom` 0.2/0.4, `webpki-roots` 0.26/1.0, `miniz_oxide` 0.8/0.9, `smol_str` 0.2/0.3, `font-types` 0.11/0.12. |

**Every one of these is downstream of `slint` 1.17 / `i-slint-*` / ICU /
`image` (the avif stack: `rav1e`, `ravif`, `exr`, `tiff` — pulled by
`i-slint-compiler`'s asset handling).** VAYREN controls none of the version
selections: `Cargo.lock` is fully resolved and every path dependency is a
first-party crate with no optional features (`vayren-core` declares no
features at all; `vayren-domain` and `vayren-remote-client` none either;
the only features in the workspace are `vayren-shell/packaged-remote`, the
6 views' `slint` features, and the `slint`/`slint-build` defaults).

**Decision: no dependency removed.** The task's §5 rule applies — the net
benefit is zero. Unifying the ICU/`image`/`syn` splits requires an upstream
cargo `[patch]` on a published crate tree we do not own, which would risk
silently changing Slint's text layout and AVIF handling in a trading UI.
Cryptography (`rustls`/`ring`), transport (`tungstenite`), credential store
(`keyring`), serialization (`serde`/`serde_json`) and clipboard
(`clipboard-win`) are all in active use and retained.

---

## 10. Reproducible measurement harness (added this session)

- `tools/parse_timings.py <cargo-timing-*.html>` — parses a cargo `--timings`
  report into duration/unit counts/cpu work/parallelism, the top units by
  duration, the latest-finishing critical path, section totals
  (frontend/codegen) and the `vayren-*` workspace timeline. This is how every
  number in §8–§9 was obtained. Also used for §9.7's unit-level duplicate
  count (`python tools/parse_timings.py <report>` → §9.7 numbers come from
  counting duplicate `name` fields in the report's `UNIT_DATA`).
- Raw per-run artifacts: `docs/experiments/*.out` (89 KB total) plus the
  untouched `target/cargo-timings/*.html` or worktree timing reports they
  were generated from.

**No `.ps1` helper was kept.** A first attempt shipped
`tools/measure_build.ps1`; `tools/validate_architecture_gate.py` rejected it
(`unauthorized-language`: this repo allows only `.py`/`.rs`/`.slint`) and it
was deleted. If a PowerShell helper is genuinely needed, allowlisting a new
language needs an explicit architecture decision first — don't add the file
and hope.

---

## 11. Open items for the next session (evidence-based, not yet done)

1. **Splitting `vayren-shell`** is still the only lever on the remaining
   ~520 s cold-build critical path. All screens live in one `AppWindow`
   (`crates/vayren-shell/ui/app.slint`), so the generated code cannot be
   partitioned without a UI refactor. Architectural change: needs approval.
2. **Harness generation in `build.rs`.** The build script still generates
   `research_harness.rs` and `live_harness.rs` (12.5 MB of Rust) on every
   cold build even though only test targets consume them. Build scripts
   cannot see `--cfg test`, so this cannot be gated without moving the
   harness into its own package; the gain is bounded by the 23.7 s
   `run-custom-build` and 0 s on incremental builds (content-hash skip
   already works). Low value, do not spend time here.
3. `sccache` remains uninstalled; §8.3 explains why it would not move these
   numbers even if it were.
4. **Dependency audit: DONE (§9.7), nothing removed.** 171 crates compile
   more than once (263 extra units, 43% of the build) but VAYREN owns none of
   them — all are `syn`/ICU/`image`/`slint` host-variant splits that need an
   upstream `[patch]` to unify. Not worth the risk. Do not re-litigate this
   without new evidence (e.g. a Slint release that unifies its font stack).

---

## 12. Everyday commands (measured, current setup)

Everything below assumes `rustc 1.98.1`, `x86_64-pc-windows-msvc`, and that
`CARGO_TARGET_DIR` is unset at both process and user scope (see §4).

```powershell
# — everyday editing loop (the numbers that motivated §9) —
cargo build --release -p vayren-shell --features packaged-remote   # ~32 s after a
                                                                  # localized change
cargo check -p vayren-shell --lib                                  # type-check only
cargo test  -p vayren-shell                                        # 95 tests, ~5 min
                                                                  # cold / ~20 s warm

# — fast inner loop for a single workspace member —
cargo check  -p <crate>          # vayren-core / vayren-domain /
                                # vayren-remote-client / a *-view crate
cargo test   -p <crate> --lib

# — full gate (run once, at task end) —
make check                       # = cargo fmt + cargo test + ruff + pyright +
                                #   all 8 python validators + pytest
# Windows without GNU Make:
cargo fmt  --manifest-path Cargo.toml --all -- --check
cargo test --workspace --manifest-path Cargo.toml
python tools/build_rust.py --lean-test
ruff check . ; ruff format --check . ; pyright
python tools/validate_architecture_gate.py   # + the other 7 validators
python -m pytest -q

# — final production release (only with explicit user permission to ship) —
python tools/build_rust.py --package      # core cdylib + 6 view DLLs +
                                         # packaged-remote release shell
python tools/build_rust.py --check-only   # native ABI handshake, no build
```

The Python suite needs the `vayren-core` cdylib on disk first
(`python -m pytest` fails at COLLECTION with `NativeBridgeError: Rust native
library not found`, 57 collection errors, if `target/release/vayren_core.dll`
is missing) — that is why `build_rust.py --lean-test` builds the kernel cdylib
before the suites. `python tools/build_rust.py --lean-test` (the CI path)
intentionally skips the six `vayren-*-view` release DLLs — nothing in the repo
consumes them, and they add a second cold Slint compilation to every test run.

---

## 13. Slint decomposition diagnosis + build-script optimization — 2026-10-14

Source revision: `622093c` (branch `safety/reporting-fixes`, tree clean).
Toolchain: `rustc 1.98.1`, `x86_64-pc-windows-msvc`. Profile: `[profile.release]`
as §3 (opt-3, no LTO, CGU-16, incremental) + the §13.3 addition. All runs
`cargo build --release -p vayren-shell --features packaged-remote --timings`
unless noted. Reports parsed with `tools/parse_timings.py`; raw HTML kept in
`target/cargo-timings/` (ephemeral, not committed).

### 13.1 Verified baseline matrix (current tree, no changes yet)

| # | scenario | wall | shell lib (frontend / codegen) | build-script (Slint) | bin (link) |
|---|---|---|---|---|---|
| M1 | first build, no source change | 64 s | 34.12 (26.51 / 7.61) | 1.37 | 6.07 |
| M2 | identical warm build | 2 s | — | — | — |
| M3 | `ui/market.slint` comment | 38 s | 24.48 (18.41 / 6.07) | 8.35 | 2.88 |
| M4 | `ui/portfolio.slint` comment (unrelated screen) | 37 s | 23.99 (18.18 / 5.81) | 8.18 | ~2.9 |
| M5 | `src/shell.rs` comment (**no Slint regen**, build-script 0.00 s) | 29 s | 24.29 (18.48 / 5.81) | 0.00 | 2.87 |
| VIEW | same `market.slint` edit → `-p vayren-market-view` | 22 s | view lib 12.81 | 4.75 | — |
| CHECK | `cargo check --release -p vayren-shell` (cold check-cache) | 158 s | — | — | — |
| STEADY | no-change after revert | 2 s | — | — | — |

M1 also rebuilt `vayren-core` (2.98 s) + `vayren-domain` (2.02 s) with zero
source changes — first-build-after-whatever effect (§9.2); M2 onward is steady.
M3 matches §9.1's real-property change to the second (38.5 s → 38 s), so even a
comment pays the full path and screen identity is irrelevant (M3 ≈ M4).

### 13.2 Diagnosis: why one screen costs 38 s, and what cannot fix it

Three segments, measured: Slint recompile **8.3 s (22%)**, lib frontend
**18.4 s (48%)**, lib codegen **6.0 s (16%)**, link **2.9 s (8%)**.

- **Same-crate file splits cannot help.** M5 is the decisive control: with
  `app.rs` byte-identical (regen skipped, build-script 0.00 s) the lib
  frontend still costs **18.48 s** — the floor is per-crate fixed processing
  of 19 MB (`include!` re-expansion + query re-validation), not what changed.
- **Separate `compile()` calls cannot feed one Window.** Slint resolves
  `import` inside a single compiler invocation; generated modules from
  different `compile()` calls are unrelated Rust types and a `Window` cannot
  instantiate a foreign-crate component. Evidence in-tree: `vayren-market-view`
  **recompiles** `market.slint` from source instead of reusing the shell's
  compilation (see its `build.rs` comment). So no source-file reshuffle can
  split `AppWindow`'s rustc unit — the Slint compilation boundary coincides
  with the Window tree.
- Consequence: the only structural lever is one-Window-per-crate
  (multi-window + Rust-side navigation), which changes UX architecture —
  recorded as the open recommendation in §13.5, NOT implemented.

### 13.3 IMPLEMENTED — release build-override for build scripts (kept)

**Change (`Cargo.toml`, 5 lines incl. comments, no source change):**

```toml
[profile.release.build-override]
opt-level = 2
```

Mechanism: build scripts (and proc macros + their deps) compile at opt-0 by
default, so every `.slint` regen paid ~8 s of debug-speed Slint compilation.
The override optimizes ONLY build-time code; `[profile.release]` for the
shipped lib/bin is untouched. Verified proof it was needed: `slint-viewer`
(release-optimized binary, §13.4) compiles the same tree in ~2 s.

| # | scenario | wall | Slint seg | lib (fe / cg) | bin |
|---|---|---|---|---|---|
| E0 | first build after profile change (one-time) | 1082 s | (i-slint-compiler 140.6, syn ×2 ~128, full rebuild) | 527.62 | — |
| E1 | slint edit | 35 s | **2.93** | 23.68 (17.82 / 5.86) | 5.34 |
| E2 | slint edit | 32 s | **2.92** | 23.51 (17.79 / 5.72) | 3.23 |
| E3 | slint edit | 31 s | **2.94** | 23.58 (17.76 / 5.82) | 2.84 |
| E4 | Rust-only edit (no regen, script 0.06 s) | 31 s | 0.06 | 23.63 (17.83 / 5.80) | 2.95 |

Slint segment (3× median): **8.27 s → 2.93 s (−65%)**. UI-change total:
**~37.5 s → ~32 s (−15%, 1.17x)**. Rust-only path unchanged by design
(E4 lib 17.83 vs M5 18.48 frontend — noise). Dev/test profiles untouched
(release-only override; warm dev cache from §9.3 still valid).

Release-behavior verification: `crates/` + `Cargo.toml` + `Cargo.lock`
byte-identical to `76dcdbd` (only Python/docs changed since §9.4), so the
+70,656 B exe delta (37,912,064 → 37,982,720, +0.19%) is incremental-codegen
noise, not the change; smoke probe identical to §9.5 (honest
`remote connect failed ... Unable to connect`, 1 s, no crash, no backend).

### 13.4 Live-preview workflow (measured, not installed by us)

`slint-viewer` 1.18.1 was already present (`~/.cargo/bin`, zero install cost).
Verified flags (from `--help`, not assumed): `--auto-reload`,
`--screenshot`, `--check`, `--component`. Measured headlessly:

- `slint-viewer --check ui/app.slint` → whole 726 KB tree, **~2 s**, exit 0
  (one pre-existing deprecation warning: `viewport-width` at
  `broker_connection.slint:488` — reported, NOT fixed: out of scope).
- `slint-viewer --screenshot preview.png --component AppWindow` → **~2 s**,
  real 94 KB render (verified non-blank by size + successful exit).
- Caveat: viewer is 1.18.1 vs workspace Slint 1.17 — preview fidelity only.

Recommendation: layout iteration via `slint-viewer --auto-reload` (~2–3 s per
edit, measured compiler bound) INSTEAD of release rebuilds. This is the only
measured path to seconds-scale UI iteration; no compiler change can get a
rebuild there (smallest real unit: view lib 12.81 s + Slint 4.75 s).

### 13.5 Open recommendation: multi-window decomposition (NOT implemented)

Measured prize, using the already-decomposed view crates: a `market.slint`
edit costs **22 s** in `vayren-market-view` vs **38 s** in the shell (1.7x),
and would cost the shell **0 s** if the screen moved out (no shared inputs).
Requires: one Window per screen crate + Rust-side navigation, i.e. the UX
architecture change §11.1 flagged as needs-approval. Do NOT implement without
explicit approval — single-window Alt+1..6 switching, one taskbar icon, and
shared-header behavior would all need a UX decision first.

### 13.6 Validation for this session

- `cargo test -p vayren-shell` → **95 passed, 0 failed** (17 ignored), 163 s
  cold-dev wall; re-run warm confirms same totals.
- Full Python gate → **1577 passed, 1 skipped, 0 failed** (unchanged files).
- `cargo fmt --check` → clean. No `.rs`/`.py` changed (only `Cargo.toml`).
- Tree at end: only `M Cargo.toml` (+ pre-existing untracked experiment `.out`).
- No live orders, no broker/account changes, no strategy start. Nothing
  committed or pushed (no authorization).
- Noted, not touched: `crates/target/` (68 MB, stale experiment residue —
  needs approval to delete); `target/cargo-timings/*.html` for every run above
  (ephemeral).

