"""Serve the VAYREN remote transport (EC2 backend side).

Reads the operator's data/strategy stores through the EXISTING headless
backend — the same snapshots, the same gated action path — and exposes
them on one authenticated WebSocket endpoint for the Windows EXE and
Android APK clients.

Safety posture:

- Binds loopback by default; a non-loopback bind without TLS refuses to
  start (fail closed). Certificates live outside source code (env paths).
- Authentication is mandatory: without ``VAYREN_REMOTE_TOKEN`` (or a
  token file) the process exits instead of listening.
- Serving transport never starts trading: no ``start`` is ever issued
  here — only connected clients can send commands, and those travel the
  normal gates (execution mode, capital/risk, broker health,
  reconciliation, duplicate protection).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path


def _bootstrap_src_path() -> None:
    """Make sibling ``src/`` packages importable regardless of cwd."""
    try:
        anchor = Path(__file__).resolve()
        repo_root = anchor.parents[2]  # src/remote/runner.py → repo root
        src = repo_root / "src"
        if src.is_dir() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("src path bootstrap failed: %s", exc)


_bootstrap_src_path()

logger = logging.getLogger("remote.runner")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """CLI flags for the remote transport (all tunable without code edits)."""
    parser = argparse.ArgumentParser(
        prog="vayren-remote", description="VAYREN remote transport (EC2 backend side)"
    )
    parser.add_argument("--data-dir", required=True, help="Folder with SQLite stores")
    parser.add_argument("--strategy-dir", required=True, help="Folder with strategy .py files")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Configure logging, bind the gateway, and serve until interrupted."""
    from remote.auth import AuthConfigError, load_token_map
    from remote.gateway import HeadlessGateway
    from remote.server import ConfigError, RemoteServer, config_from_env

    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        tokens = load_token_map()
    except AuthConfigError as exc:
        logger.error("remote auth misconfigured: %s", exc)
        return 2
    try:
        config = config_from_env()
    except ConfigError as exc:
        logger.error("remote config refused: %s", exc)
        return 2
    gateway = HeadlessGateway(args.data_dir, args.strategy_dir)
    server = RemoteServer(gateway, tokens, config)
    try:
        server.start()
    except (ConfigError, OSError) as exc:
        logger.error("remote transport failed to start: %s", exc)
        gateway.close()
        return 1
    logger.info("remote transport serving (backend idle; trading starts only via client command)")
    try:
        while True:
            try:
                stopped = server._stop.wait(timeout=1.0)
            except KeyboardInterrupt:
                break
            if stopped:
                break
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        gateway.close()
    logger.info("remote transport stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
