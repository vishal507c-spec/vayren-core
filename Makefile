.PHONY: setup dev fast check-fast test test-coverage bench lint format typecheck check validate-structure validate-imports validate-language validate-architecture validate-routes validate-authority validate-repo-graph validate-scope context-check rust release exe clean

# ═══════════════════════════════════════════════════════════════
# VAYREN — MAKEFILE
# ═══════════════════════════════════════════════════════════════
# Single entry point for all common commands.
# Windows: GNU Make can be installed via `winget install ezwinports.make`
# or `choco install make`. Alternatively run target commands directly
# (e.g. `python tools/benchmark.py impact --run` for `make fast`).

# ── Setup ──────────────────────────────────────────────────────

setup:
	pip install -e ".[dev]"
	pre-commit install
	python tools/build_rust.py

# ── Development & Fast Loop (10x Speedup) ──────────────────────

dev:
	python tools/launch_native.py

fast:
	python tools/benchmark.py impact --run

check-fast:
	ruff check .
	cargo check --workspace
	python tools/validate_imports.py
	python tools/validate_structure.py
	python tools/validate_language_ownership.py

# ── Quality ────────────────────────────────────────────────────

test:
	pytest

# Performance benchmarks (frame budgets, million-row projections, scroll and
# projection cost). They are `#[ignore]`d so the default gate asks only "is it
# correct?" — measured 76.2s -> 1.6s for the Rust lib suite. Run them here when
# the algorithms changed and the numbers need re-proving.
bench:
	cargo test --workspace -- --ignored

test-coverage:
	pytest --cov=app --cov=core --cov=market --cov=data --cov=strategy --cov=backtest --cov=risk --cov=execution --cov-report=term --cov-report=html

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

typecheck:
	pyright

# ── Validation ─────────────────────────────────────────────────

validate-structure:
	python tools/validate_structure.py

validate-imports:
	python tools/validate_imports.py

validate-language:
	python tools/validate_language_ownership.py

validate-architecture:
	python tools/validate_architecture_gate.py

validate-routes:
	python tools/validate_routes.py

context-check:
	python tools/context_engine.py --check

validate-authority:
	python tools/validate_authority.py

validate-repo-graph:
	python tools/validate_repo_graph.py

validate-scope:
	python tools/validate_scope.py --stats

# ── Rust (AI_ENTRY.md §1: Rust owns core/perf kernels) ─────────

rust:
	python tools/build_rust.py --lean-test
	cargo fmt --manifest-path Cargo.toml --all -- --check

# ── Full Check ─────────────────────────────────────────────────
# Every gate command lives here exactly once: the CI `validators`
# job mirrors this list, and `make check` is the local superset.

check: rust lint typecheck test validate-structure validate-imports validate-language validate-architecture validate-authority validate-routes validate-repo-graph validate-scope context-check

# ── Release (mechanics only; run `make check` first, CI validates on push) ─

# Recursively expanded: the error fires only when the release recipe actually
# reads VERSION, so every other target is unaffected.
VERSION ?= $(error VERSION is required — run: make release VERSION=1.28.0)

release:
	python tools/release.py --version $(VERSION)

# ── Clean ──────────────────────────────────────────────────────
# Cross-platform on purpose: one Python one-liner instead of
# powershell + rmdir (Windows-only) or rm (POSIX-only).

clean:
	@python -c "import pathlib, shutil; skip = {'.git', '.venv', 'venv', 'node_modules', 'target'}; root = pathlib.Path('.'); [shutil.rmtree(p, ignore_errors=True) for p in root.rglob('__pycache__') if not skip & set(p.parts)]; [p.unlink() for p in list(root.rglob('*.pyc')) + list(root.rglob('*.pyo'))]; [shutil.rmtree(root / d, ignore_errors=True) for d in ('.pytest_cache', '.ruff_cache', 'htmlcov', 'dist', 'build')]"
	@echo "Clean complete."

# ═══════════════════════════════════════════════════════════════
# TYPICAL WORKFLOW
# ═══════════════════════════════════════════════════════════════
#
#   make setup       → First time: install everything
#   make dev         → Launch the charting application
#   make fast        → Fast inner loop: test & lint only changed files (<2s)
#   make check-fast  → Fast lint, cargo check & governance without full gate
#   make check       → Before commit: verify everything
#   make bench       → Perf benchmarks only (frame budgets, large datasets)
#   make format      → Auto-fix formatting issues
#   make test        → Run the test suite
#
# ═══════════════════════════════════════════════════════════════
