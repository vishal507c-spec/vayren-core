"""One-command GitHub release (~30 seconds of mechanics, not validation).

Does the mechanical release only: version bump -> stage -> secret scan ->
commit -> tag -> push (+ `gh release` attempt). It deliberately does NOT
re-run the full test gate here — run `make check` BEFORE releasing; CI
mirrors the full gate on push and is the validation authority.

Usage:
    python scripts/release.py --version 1.11.0
    python scripts/release.py --version 1.11.0 --dry-run   # print plan only
    python scripts/release.py --version 1.11.0 --no-push   # commit+tag locally

Exit 0 = code released (pushed). The `gh release` object may still need
`gh auth login` (printed as an exact recovery command when it fails).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

# Scratch evidence that must never ride along in a release commit.
NEVER_STAGE = {"M7-shim-baseline.txt"}

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{8,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|"
    r"gho_[A-Za-z0-9]{20,}|xox[bpas]-[A-Za-z0-9-]{8,}|"
    r"Authorization:\s*Bearer\s+\S+|"
    r"password\s*=\s*[\"'][^\"' ]{4,}[\"'])"
)


def _run(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _git(*args: str, timeout: int = 120) -> str:
    proc = _run(["git", *args], timeout=timeout)
    if proc.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{(proc.stderr or '')[-1500:]}")
    return proc.stdout


def _current_version() -> str:
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise SystemExit("cannot find version in pyproject.toml")
    return match.group(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="One-command GitHub release")
    parser.add_argument("--version", required=True, help="release version, e.g. 1.11.0")
    parser.add_argument("--message", default="", help="extra commit message line")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    parser.add_argument("--no-push", action="store_true", help="commit+tag locally, skip push")
    args = parser.parse_args()
    if not VERSION_RE.match(args.version):
        raise SystemExit(f"bad version (want X.Y.Z): {args.version!r}")

    started = time.perf_counter()
    marks: dict[str, float] = {}

    branch = _git("branch", "--show-current").strip()
    head = _git("rev-parse", "--short", "HEAD").strip()
    current = _current_version()
    print(f"branch={branch} head={head} current={current} target={args.version}")

    existing_tags = _git("tag", "-l", f"v{args.version}").strip()
    if existing_tags:
        raise SystemExit(f"tag v{args.version} already exists — pick a new version")

    status = _git("status", "--short").strip().splitlines()
    dirty = [line for line in status if line.strip()]
    print(f"working tree entries: {len(dirty)}")
    if not dirty and current == args.version:
        raise SystemExit("nothing to release (clean tree, version unchanged)")

    if args.dry_run:
        print(
            f"DRY RUN: would bump {current} -> {args.version}, stage {len(dirty)} "
            f"entries (minus {sorted(NEVER_STAGE)}), commit, tag v{args.version}, "
            + ("skip push" if args.no_push else "push branch+tag")
        )
        return 0

    t0 = time.perf_counter()
    pyproject = ROOT / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    new_text = re.sub(
        r'^version\s*=\s*"[^"]+"', f'version = "{args.version}"', text, count=1, flags=re.MULTILINE
    )
    if new_text == text:
        raise SystemExit("version bump failed (pattern not found)")
    pyproject.write_text(new_text, encoding="utf-8")
    marks["version"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    _git("add", "-A")
    for scratch in NEVER_STAGE:
        _run(["git", "restore", "--staged", scratch])
    staged = _git("diff", "--cached", "--name-only").strip().splitlines()
    staged = [f for f in staged if f.strip()]
    print(f"staged files: {len(staged)}")
    marks["stage"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    diff = _git("diff", "--cached", "--", *staged) if staged else ""
    hits = [
        line
        for line in diff.splitlines()
        if line.startswith("+") and not line.startswith("+++") and SECRET_RE.search(line)
    ]
    if hits:
        raise SystemExit(f"secret scan FAILED ({len(hits)} hits, first: {hits[0][:100]})")
    print("secret scan: clean")
    marks["scan"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    message = f"release: v{args.version}"
    cmd = ["commit", "-m", message]
    if args.message:
        cmd += ["-m", args.message]
    _git(*cmd)
    commit = _git("rev-parse", "--short", "HEAD").strip()
    print(f"committed {commit}")
    marks["commit"] = time.perf_counter() - t0

    t0 = time.perf_counter()
    _git("tag", "-a", f"v{args.version}", "-m", f"v{args.version}")
    marks["tag"] = time.perf_counter() - t0

    if not args.no_push:
        t0 = time.perf_counter()
        _git("push", "origin", branch, timeout=180)
        _git("push", "origin", f"v{args.version}", timeout=180)
        marks["push"] = time.perf_counter() - t0
        print(f"pushed {branch} + v{args.version}")

    total = time.perf_counter() - started
    print(
        "phase seconds: "
        + " ".join(f"{k}={v:.1f}" for k, v in marks.items())
        + f" TOTAL={total:.1f}s"
    )

    if shutil.which("gh") and not args.no_push:
        proc = _run(
            [
                "gh",
                "release",
                "create",
                f"v{args.version}",
                "--title",
                f"v{args.version}",
                "--generate-notes",
            ],
            timeout=120,
        )
        if proc.returncode != 0:
            print("ACTION NEEDED (gh auth): run `gh auth login`, then:")
            print(f"  gh release create v{args.version} --title v{args.version} --generate-notes")
            print("  (or 1-click 'Create release from tag' on the tag page)")
        else:
            print(f"GitHub release v{args.version} created")
    elif not args.no_push:
        print("ACTION NEEDED: `gh` not found — create the release from the tag page")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
