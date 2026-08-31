.PHONY: setup dev test test-coverage lint format typecheck check validate-structure validate-imports exe clean

# ═══════════════════════════════════════════════════════════════
# VAYREN — MAKEFILE
# ═══════════════════════════════════════════════════════════════
# Single entry point for all common commands.

# ── Setup ──────────────────────────────────────────────────────

setup:
	pip install -e ".[dev]"
	pre-commit install

# ── Development ────────────────────────────────────────────────

dev:
	python -m app

# ── Packaging ──────────────────────────────────────────────────

exe:
	.venv\Scripts\pyinstaller scripts\assets\vayren.spec --noconfirm --distpath build\dist --workpath build\work

# ── Quality ────────────────────────────────────────────────────

test:
	pytest

test-coverage:
	pytest --cov=app --cov=core --cov=market --cov=chart --cov=data --cov-report=term --cov-report=html

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

# ── Full Check ─────────────────────────────────────────────────

check: lint typecheck test validate-structure validate-imports

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
