"""Wrong-language change gate (hard gate, no advisory PASS).

Fails when a Rust-owned behavior is implemented in Python or new Qt UI is
written outside the Slint path. Allowed Python (strategy, research, AI,
bridges, oracles, tests, explicitly retained glue) passes.

Usage: python scripts/validate_architecture_gate.py [--ref HEAD] [--json]
Exit: 0 PASS (warnings allowed), 1 FAIL, 2 ERROR.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from migration.change_gate import check_changes


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate change language ownership")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = check_changes(ref=args.ref)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif report.verdict == "PASS":
        print(f"Architecture gate PASSED ({report.files_checked} files checked)")
        for warning in report.warnings[:20]:
            print(f"  warn: {warning}")
        if report.debt_created:
            print(f"  migration debt created: +{report.debt_created} definitions")
    elif report.verdict == "ERROR":
        print("Architecture gate ERROR:")
        for detail in report.details:
            print(f"  - {detail}")
    else:
        print("Architecture gate FAILED:")
        for violation in report.violations:
            print(f"  - {violation.file}: [{violation.rule}] {violation.message}")
    return {"PASS": 0, "FAIL": 1}.get(report.verdict, 2)


if __name__ == "__main__":
    sys.exit(main())
