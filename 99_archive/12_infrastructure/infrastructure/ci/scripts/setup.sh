#!/bin/bash
set -e

echo "Setting up Vayren Core..."
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
echo "Setup complete. Run 'make test' to verify."
