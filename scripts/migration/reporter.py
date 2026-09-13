"""Migration reporting: repository summary, top blockers, safety, integrity.

The report answers immediately: what remains, why it is not migrated, what
blocks it, which dependency must migrate first, what tests are missing, what
parity mismatches exist, which Python code is still authoritative and what
exact action unblocks it.
"""

from __future__ import annotations

import json

from .config import REPORT_JSON_PATH, REPORT_MD_PATH
from .manifest_store import load_all
from .planner import blocked_units, build_plan
from .redact import redact_text
from .scanner import inventory_dict, scan
from .validator import validate_all


def build_report() -> dict:
    manifests = load_all()
    plan = build_plan()
    blocked = blocked_units(plan)
    exit_code, gate = validate_all()
    inv = scan()
    by_state: dict[str, int] = {}
    for manifest in manifests.values():
        by_state[manifest.state] = by_state.get(manifest.state, 0) + 1
    parity_pass = sum(1 for m in manifests.values() if m.parity.status == "PASS")
    shadow_pass = sum(1 for m in manifests.values() if m.shadow.status == "PASS")
    rust_auth = sum(1 for m in manifests.values() if m.authority == "rust")
    python_auth = sum(1 for m in manifests.values() if m.authority == "python")
    quarantined = sum(
        1 for m in manifests.values() if "QUARANTIN" in m.state or "REMOVED" in m.state
    )
    report = {
        "summary": {
            "total_units": len(manifests),
            "by_state": by_state,
            "parity_verified": parity_pass,
            "shadow_validated": shadow_pass,
            "rust_authoritative": rust_auth,
            "python_authoritative": python_auth,
            "quarantined": quarantined,
            "migrated": by_state.get("MIGRATED", 0),
            "blocked": len(blocked),
            "stale_or_failed": gate["failures"],
        },
        "top_blockers": [
            {
                "unit": entry.unit_id,
                "state": entry.state,
                "authority": entry.owner,
                "blocked_by": entry.blockers,
                "parity": entry.parity,
                "shadow": entry.shadow,
                "required_action": entry.required_action,
            }
            for entry in blocked[:15]
        ],
        "safety": {
            "parity_coverage": f"{parity_pass}/{len(manifests)}",
            "shadow_coverage": f"{shadow_pass}/{len(manifests)}",
            "gate_failures": gate["failures"],
        },
        "integrity": {
            "orphaned_manifests": gate["orphaned_manifests"],
            "retention_missing": inventory_dict(inv)["retention_missing"][:10],
            "rust_unused": inventory_dict(inv)["rust_unused"],
            "python_authoritative": inventory_dict(inv)["python_authoritative"],
        },
        "gate_exit": exit_code,
    }
    return report


def render_markdown(report: dict) -> str:
    lines = ["# VAYREN Migration Report", ""]
    summary = report["summary"]
    lines.append("## Repository Migration Summary")
    lines.append("")
    for key, value in summary.items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Top Blockers")
    lines.append("")
    if not report["top_blockers"]:
        lines.append("None — every unit is migrated or ready.")
    for blocker in report["top_blockers"]:
        lines.append(f"### {blocker['unit']}")
        lines.append("")
        lines.append(f"- Authority: {blocker['authority']}")
        lines.append(f"- State: {blocker['state']}")
        blocked_by = ", ".join(blocker["blocked_by"]) or "none (own evidence missing)"
        lines.append(f"- Blocked by: {blocked_by}")
        lines.append(f"- Parity: {blocker['parity']} / Shadow: {blocker['shadow']}")
        lines.append(f"- Required: {blocker['required_action']}")
        lines.append("")
    lines.append("## Safety")
    lines.append("")
    for key, value in report["safety"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    lines.append("## Integrity")
    lines.append("")
    for key, value in report["integrity"].items():
        lines.append(f"- {key}: {value}")
    lines.append("")
    return redact_text("\n".join(lines))


def write_report() -> tuple[str, str]:
    report = build_report()
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    text = render_markdown(report)
    REPORT_MD_PATH.write_text(text + "\n", encoding="utf-8")
    return str(REPORT_JSON_PATH), str(REPORT_MD_PATH)


__all__ = ["build_report", "render_markdown", "write_report"]
