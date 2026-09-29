.PHONY: setup dev test test-coverage lint format typecheck check validate-structure validate-imports validate-language validate-architecture validate-routes validate-authority validate-repo-graph context-check rust release exe clean

# ═══════════════════════════════════════════════════════════════
# VAYREN — MAKEFILE
# ═══════════════════════════════════════════════════════════════
# Single entry point for all common commands.

# ── Setup ──────────────────────────────────────────────────────

setup:
	pip install -e ".[dev]"
	pre-commit install
	python scripts/build_rust.py

# ── Development ────────────────────────────────────────────────

dev:
	python scripts/launch_native.py

# ── Quality ────────────────────────────────────────────────────

test:
	pytest

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
	python scripts/validate_structure.py

validate-imports:
	python scripts/validate_imports.py

validate-language:
	python scripts/validate_language_ownership.py

validate-architecture:
	python scripts/validate_architecture_gate.py

validate-routes:
	python scripts/validate_routes.py

context-check:
	python scripts/context_engine.py --check

validate-authority:
	python scripts/validate_authority.py

validate-repo-graph:
	python scripts/validate_repo_graph.py

# ── Rust (AI_ENTRY.md §1: Rust owns core/perf kernels) ─────────

rust:
	python scripts/build_rust.py --test
	cd rust && cargo fmt --all -- --check

# ── Full Check ─────────────────────────────────────────────────
# Every gate command lives here exactly once: the CI `validators`
# job mirrors this list, and `make check` is the local superset.

check: rust lint typecheck test validate-structure validate-imports validate-language validate-architecture validate-authority validate-routes validate-repo-graph context-check

# ── Release (mechanics only; run `make check` first, CI validates on push) ─

# Recursively expanded: the error fires only when the release recipe actually
# reads VERSION, so every other target is unaffected.
VERSION ?= $(error VERSION is required — run: make release VERSION=1.28.0)

release:
	python scripts/release.py --version $(VERSION)

# ── Clean ──────────────────────────────────────────────────────
# Cross-platform on purpose: one Python one-liner instead of
# powershell + rmdir (Windows-only) or rm (POSIX-only).

clean:
	@python -c "import pathlib, shutil; skip = {'.git', '.venv', 'venv', 'node_modules', 'target', 'rust/target'}; root = pathlib.Path('.'); [shutil.rmtree(p, ignore_errors=True) for p in root.rglob('__pycache__') if not skip & set(p.parts)]; [p.unlink() for p in list(root.rglob('*.pyc')) + list(root.rglob('*.pyo'))]; [shutil.rmtree(root / d, ignore_errors=True) for d in ('.pytest_cache', '.ruff_cache', 'htmlcov', 'dist', 'build')]"
	@echo "Clean complete."

# ═══════════════════════════════════════════════════════════════
# TYPICAL WORKFLOW
# ═══════════════════════════════════════════════════════════════
#
#   make setup       → First time: install everything
#   make dev         → Launch the charting application
#   make check       → Before commit: verify everything
#   make format      → Auto-fix formatting issues
#   make test        → Run the test suite
#
# ═══════════════════════════════════════════════════════════════
