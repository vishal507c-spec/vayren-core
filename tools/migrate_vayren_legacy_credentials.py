"""Migrate legacy VAYREN OS hardcoded credentials into the secure store.

Reads the attached legacy strategy file (``vayren (2).py``) with ``ast`` —
never imports/executes it — extracts the ``AuthCredentials`` constants, and
writes them into the existing broker credential store. Secret VALUES are
never printed, logged, or committed; only field names are reported.

Dry-run by default. Pass ``--apply`` to actually write to the secure store.
The legacy file itself is never modified by this script.

Usage:
    python tools/migrate_vayren_legacy_credentials.py "C:\\path\\vayren (2).py"
    python tools/migrate_vayren_legacy_credentials.py "C:\\path\\vayren (2).py" --apply
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_FIELDS = (
    "API_KEY",
    "API_SECRET",
    "ZERODHA_USER_ID",
    "ZERODHA_PASSWORD",
    "ZERODHA_TOTP_SECRET",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


def _extract(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AuthCredentials":
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                    for target in item.targets:
                        if (
                            isinstance(target, ast.Name)
                            and target.id in _FIELDS
                            and isinstance(item.value.value, str)
                        ):
                            out[target.id] = item.value.value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy_file", type=Path)
    parser.add_argument("--apply", action="store_true", help="write to secure store")
    parser.add_argument("--data-dir", default=None)
    args = parser.parse_args(argv)

    values = _extract(args.legacy_file)
    present = [k for k in _FIELDS if values.get(k)]
    print("Found credential fields:", ", ".join(present) or "(none)")
    if not present:
        print("Nothing to migrate.")
        return 0
    if not args.apply:
        print("DRY RUN — no values written. Re-run with --apply to store securely.")
        return 0

    from broker.common.credentials_store import default_store, provider_service

    data_dir = args.data_dir or str(Path.home() / ".vayren" / "data")
    store = default_store(data_dir)
    zerodha = {
        "app_id": values.get("API_KEY", ""),
        "secret": values.get("API_SECRET", ""),
        "client_id": values.get("ZERODHA_USER_ID", ""),
        "password": values.get("ZERODHA_PASSWORD", ""),
        "totp_secret": values.get("ZERODHA_TOTP_SECRET", ""),
    }
    store.save(provider_service("zerodha"), {k: v for k, v in zerodha.items() if v})
    telegram = {
        "bot_token": values.get("TELEGRAM_BOT_TOKEN", ""),
        "chat_id": values.get("TELEGRAM_CHAT_ID", ""),
    }
    store.save(provider_service("telegram"), {k: v for k, v in telegram.items() if v})
    print("Stored securely. Values were not printed. Rotate the legacy secrets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
