"""Make the forensics package importable under pytest (scripts/ is not a package).

Both scripts/ (package root) and scripts/forensics/ (flat sibling imports,
matching `python scripts/forensics/__main__.py` execution) are added.
"""

import sys
from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(PKG_DIR.parent))
sys.path.insert(0, str(PKG_DIR))
