"""Evidence-driven migration gate (hard gate, no false PASS).

Fails on: missing manifests, false canonical claims, stale evidence,
parity/shadow failures, quarantined reintroduction, secret literals,
orphaned manifests. Units that simply have not migrated yet report
BLOCKED/INCONCLUSIVE without failing the gate.

Usage: python scripts/validate_migration.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.migration.validator import validate_all


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate evidence-driven migration state")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    code, report = validate_all()
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return code
    if code == 0:
        print(f"Migration validation PASSED ({report['units']} units tracked)")
        return 0
    print(f"Migration validation FAILED ({report['failures']} failures):")
    for unit_id, result in report["results"].items():
        if result["verdict"] in ("FAIL", "STALE"):
            print(f"  - {unit_id}: {result['verdict']} ({result['state']})")
            for reason in result["reasons"][:3]:
                print(f"      {reason}")
    if report["orphaned_manifests"]:
        print(f"  orphaned manifests: {report['orphaned_manifests']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
