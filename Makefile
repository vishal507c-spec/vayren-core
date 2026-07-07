.PHONY: setup test lint format typecheck clean check dev shell backtest adr journal experiment validate-structure validate-imports docker-build docker-up docker-down

# ═══════════════════════════════════════════════════════════════
# VAYREN CORE — MAKEFILE
# ═══════════════════════════════════════════════════════════════
# The Makefile is the single entry point for all common commands.
# Instead of remembering tool-specific flags, you type `make <command>`.
# Everything is consistent regardless of operating system.

# ── Setup ──────────────────────────────────────────────────────

setup:
	pip install -e ".[dev]"
	pre-commit install

# ── Quality ────────────────────────────────────────────────────

test:
	pytest

test-coverage:
	pytest --cov=vayren --cov-report=term --cov-report=html

lint:
	ruff check .
	ruff format --check .

format:
	ruff check --fix .
	ruff format .

typecheck:
	pyright

# ── Clean ──────────────────────────────────────────────────────

clean:
	@echo "Removing __pycache__ directories..."
	@powershell -Command "Get-ChildItem -Path . -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue"
	@echo "Removing .pyc files..."
	@powershell -Command "Get-ChildItem -Path . -Recurse -File -Filter *.pyc | Remove-Item -Force -ErrorAction SilentlyContinue"
	@echo "Removing cache and build directories..."
	-rmdir /s /q .pytest_cache 2>nul
	-rmdir /s /q .ruff_cache 2>nul
	-rmdir /s /q htmlcov 2>nul
	-rmdir /s /q dist 2>nul
	-rmdir /s /q build 2>nul
	@echo "Clean complete."

# ── Development ────────────────────────────────────────────────

dev:
	python -m interfaces.api.server

shell:
	python -m interfaces.cli.shell

backtest:
	python -m interfaces.cli.backtest --config config/backtest-sample.yaml

# ── Validation ─────────────────────────────────────────────────

validate-structure:
	python scripts/validate_structure.py

validate-imports:
	python scripts/validate_imports.py

# ── Full Check ─────────────────────────────────────────────────

check: lint typecheck test validate-structure validate-imports

# ── Knowledge Management ──────────────────────────────────────

adr:
	@echo "Usage: make adr NAME=my-decision-title"
	@powershell -Command "$$name = '$(NAME)'.ToLower().Replace(' ', '-'); $$path = '13_knowledge/knowledge/decisions/' + [int][double]::Parse((Get-Date -UFormat %s)) + '-' + $$name + '.md'; Copy-Item '13_knowledge/knowledge/decisions/000-template.md' $$path; Write-Output 'Created: ' + $$path"

journal:
	@echo "Usage: make journal"
	@powershell -Command "$$date = Get-Date -Format 'yyyy-MM-dd'; $$path = '13_knowledge/knowledge/journals/' + $$date + '.md'; if (!(Test-Path $$path)) { Copy-Item '13_knowledge/knowledge/journals/journal-template.md' $$path; Write-Output 'Created: ' + $$path } else { Write-Output 'Journal for ' + $$date + ' already exists.' }"

experiment:
	@echo "Usage: make experiment NAME=my-experiment"
	@powershell -Command "$$name = '$(NAME)'.ToLower().Replace(' ', '-'); $$date = Get-Date -Format 'yyyy-MM'; $$dir = '13_knowledge/knowledge/experiments/' + $$date + '-' + $$name; New-Item -ItemType Directory -Path $$dir -Force | Out-Null; Copy-Item '13_knowledge/knowledge/experiments/experiment-template.md' (Join-Path $$dir 'summary.md'); Write-Output 'Created: ' + $$dir"

# ── Docker ─────────────────────────────────────────────────────

docker-build:
	docker compose -f 12_infrastructure/infrastructure/docker/docker-compose.yml build

docker-up:
	docker compose -f 12_infrastructure/infrastructure/docker/docker-compose.yml up -d

docker-down:
	docker compose -f 12_infrastructure/infrastructure/docker/docker-compose.yml down

# ═══════════════════════════════════════════════════════════════
# TYPICAL WORKFLOW
# ═══════════════════════════════════════════════════════════════
#
#   make setup       → First time: install everything
#   make dev         → Start working on code
#   make check       → Before commit: verify everything
#   make format      → Auto-fix formatting issues
#   make test        → Run specific tests
#
# ═══════════════════════════════════════════════════════════════
