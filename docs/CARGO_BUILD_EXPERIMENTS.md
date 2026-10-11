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
- Generated `app.rs` observed: about 16.8 KB (in `OUT_DIR`).
- **Earlier claim of a 31.6 MB generated `app.rs` was NOT reproduced.** Do
  not reuse that number.
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
