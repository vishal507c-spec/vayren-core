"""Parse a cargo `--timings` HTML report into per-unit sections + critical path.

Usage: python tools/parse_timings.py target/cargo-timings/cargo-timing-*.html [--top N]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def extract(path: Path) -> tuple[int, list[dict]]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = re.search(r"DURATION\s*=\s*(\d+);", raw)
    if not m:
        raise SystemExit(f"no DURATION found in {path}")
    duration = int(m.group(1))
    start = raw.find("const UNIT_DATA = ")
    if start < 0:
        raise SystemExit("no UNIT_DATA found")
    i = raw.find("[", start)
    depth = 0
    j = i
    while j < len(raw):
        c = raw[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return duration, json.loads(raw[i : j + 1])


def main() -> int:
    path = Path(sys.argv[1])
    duration, units = extract(path)
    print(f"file      : {path.name}")
    print(f"duration  : {duration} s ({duration / 60:.2f} min)")
    print(f"units     : {len(units)}")
    print(f"max conc  : {max((u['start'] + u['duration'] for u in units), default=0):.1f} s")

    # Total work (sum of durations) vs wall clock -> parallelism factor.
    work = sum(u["duration"] for u in units)
    print(f"cpu work  : {work:.1f} s   parallelism={work / duration:.2f}x")

    def row(u: dict) -> str:
        secs = u.get("sections") or []
        parts = []
        for name, span in secs:
            parts.append(f"{name}={span['end'] - span['start']:.2f}")
        return " ".join(parts)

    print("\n=== TOP 25 UNITS BY DURATION ===")
    print(f"{'name':<38}{'unit':<18}{'start':>8}{'dur':>8}  sections")
    for u in sorted(units, key=lambda x: -x["duration"])[:25]:
        label = u["name"] + (" " + u["target"].strip() if u.get("target") else "")
        print(
            f"{label:<38}{u.get('mode', ''):<18}{u['start']:>8.2f}{u['duration']:>8.2f}  {row(u)}"
        )

    print("\n=== CRITICAL PATH (latest-finishing 15) ===")
    for u in sorted(units, key=lambda x: -(x["start"] + x["duration"]))[:15]:
        label = u["name"] + (" " + u["target"].strip() if u.get("target") else "")
        finish = u["start"] + u["duration"]
        print(
            f"  finish={finish:>8.2f}s  start={u['start']:>8.2f} dur={u['duration']:>7.2f}  {label}"
        )

    print("\n=== SECTION TOTALS (sum across all units) ===")
    totals: dict[str, float] = {}
    for u in units:
        for name, span in u.get("sections") or []:
            totals[name] = totals.get(name, 0.0) + (span["end"] - span["start"])
    for k, v in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<12}{v:>10.2f} s")

    print("\n=== PHASE TOTALS (sum by unit mode) ===")
    modes: dict[str, list[float]] = {}
    for u in units:
        modes.setdefault(u.get("mode", "?"), []).append(u["duration"])
    for k, v in sorted(modes.items(), key=lambda kv: -sum(kv[1])):
        print(f"  {k:<12}{sum(v):>10.2f} s  ({len(v)} units)")

    # Workspace-only view (local crates).
    print("\n=== WORKSPACE (vayren-*) UNITS ===")
    for u in sorted(units, key=lambda x: x["start"]):
        if u["name"].startswith("vayren"):
            label = u["name"] + (" " + u["target"].strip() if u.get("target") else "")
            finish = u["start"] + u["duration"]
            print(
                f"  start={u['start']:>8.2f} dur={u['duration']:>7.2f} "
                f"finish={finish:>8.2f}  {label}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
