"""Migration package entry point: ``python scripts/migration/__main__.py <cmd>``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts.migration.cli import main

if __name__ == "__main__":
    sys.exit(main())
