.PHONY: setup dev test test-coverage lint format typecheck check validate-structure validate-imports validate-language validate-architecture rust release exe clean

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

# ── Rust (constitution §8: Rust owns core/perf kernels) ─────────

rust:
	python scripts/build_rust.py --test
	cd rust && cargo fmt --all -- --check

# ── Full Check ─────────────────────────────────────────────────

check: rust lint typecheck test validate-structure validate-imports validate-language validate-architecture

# ── Release (mechanics only; run `make check` first, CI validates on push) ─

release:
	python scripts/release.py --version $(VERSION)

# ── Clean ──────────────────────────────────────────────────────

clean:
	@echo "Removing __pycache__ directories..."
	@powershell -Command "Get-ChildItem -Path . -Recurse -Directory -Filter __pycache__ | Where-Object { $$_.FullName -notlike '*\.venv\*' } | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"
	@echo "Removing .pyc files..."
	@powershell -Command "Get-ChildItem -Path . -Recurse -File -Filter *.pyc | Remove-Item -Force -ErrorAction SilentlyContinue"
	@echo "Removing cache and build directories..."
	-rmdir /s /q .pytest_cache 2>nul
	-rmdir /s /q .ruff_cache 2>nul
	-rmdir /s /q htmlcov 2>nul
	-rmdir /s /q dist 2>nul
	-rmdir /s /q build 2>nul
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
