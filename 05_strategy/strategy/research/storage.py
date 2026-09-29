"""Research storage — generic file-based, one JSON per object."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .experiment import Experiment


def _research_dir(data_dir: Path | str | None, sub: str) -> Path:
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "research" / sub
    else:
        d = Path.cwd() / ".vayren" / "research" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _backup_corrupt(path: Path) -> Path:
    """Rename a corrupt file aside (never delete evidence) and return the backup path."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    backup = path.with_name(f"{path.name}.corrupt-{stamp}")
    counter = 0
    while backup.exists():
        counter += 1
        backup = path.with_name(f"{path.name}.corrupt-{stamp}-{counter}")
    path.rename(backup)
    return backup


def _decode_experiment_file(path: Path) -> Experiment:
    """Read one experiment file; corrupt content is backed up, then raises."""
    try:
        return Experiment.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError:
        raise
    except Exception as exc:
        backup = _backup_corrupt(path)
        raise ValueError(f"corrupt experiment file {path} (backed up to {backup})") from exc


def _record_lineage_for_experiment(exp: Experiment, data_dir: Path | str | None) -> None:
    try:
        from .lineage import load_lineage, save_lineage

        g = load_lineage(data_dir)
        g.add_node("STRATEGY", exp.strategy_id)
        g.add_node("VERSION", exp.version_id)
        g.add_node("EXPERIMENT", exp.experiment_id)
        g.add_edge(
            "STRATEGY",
            exp.strategy_id,
            "EXPERIMENT",
            exp.experiment_id,
            relationship="experiment_of",
        )
        g.add_edge(
            "VERSION", exp.version_id, "EXPERIMENT", exp.experiment_id, relationship="experiment_of"
        )
        for eid in exp.execution_ids:
            g.add_node("EXECUTION", eid)
            g.add_edge("EXECUTION", eid, "EXPERIMENT", exp.experiment_id, relationship="input_to")
        save_lineage(g, data_dir)
    except Exception:
        pass


def save_experiment(exp: Experiment, data_dir: Path | str | None = None) -> Path:
    p = _research_dir(data_dir, "experiments") / f"{exp.experiment_id}.json"
    # Immutable check — if exists and identical, return; otherwise collision error.
    # A corrupt existing file is backed up, never silently overwritten.
    if p.exists():
        existing = _decode_experiment_file(p)
        if existing.to_dict() == exp.to_dict():
            return p
        raise FileExistsError(f"experiment id collision: {exp.experiment_id}")
    p.write_text(exp.to_json(), encoding="utf-8")
    _record_lineage_for_experiment(exp, data_dir)
    return p


def save_experiment_update(exp: Experiment, data_dir: Path | str | None = None) -> Path:
    """Persist a lifecycle transition of an existing experiment.

    Non-terminal experiments (no result fingerprint yet) may advance
    DRAFT → RUNNING → ANALYZING → ... → COMPLETED. Once an experiment is
    terminal with a result fingerprint, its evidence is immutable: any
    differing rewrite raises instead of silently overwriting history.
    """
    from .experiment import TERMINAL_STATUSES

    p = _research_dir(data_dir, "experiments") / f"{exp.experiment_id}.json"
    if p.exists():
        existing = _decode_experiment_file(p)
        terminal = existing.status in TERMINAL_STATUSES and bool(existing.result_fingerprint)
        if existing.to_dict() == exp.to_dict():
            return p
        if terminal:
            raise FileExistsError(
                f"immutable experiment already completed: {exp.experiment_id} "
                "— create a new experiment to re-run"
            )
    p.write_text(exp.to_json(), encoding="utf-8")
    _record_lineage_for_experiment(exp, data_dir)
    return p


def load_experiment(experiment_id: str, data_dir: Path | str | None = None) -> Experiment | None:
    p = _research_dir(data_dir, "experiments") / f"{experiment_id}.json"
    if not p.exists():
        return None
    return _decode_experiment_file(p)


def list_experiments(data_dir: Path | str | None = None) -> list[Experiment]:
    d = _research_dir(data_dir, "experiments")
    exps: list[Experiment] = []
    for p in d.glob("*.json"):
        if ".corrupt-" in p.name:
            continue
        try:
            exps.append(_decode_experiment_file(p))
        except Exception:
            # _decode_experiment_file already backed the corrupt file aside;
            # listing stays robust while direct loads raise.
            continue
    exps.sort(key=lambda x: x.created_at)
    return exps


def _run_dir(data_dir: Path | str | None, experiment_id: str) -> Path:
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "research" / "runs" / str(experiment_id)
    else:
        d = Path.cwd() / ".vayren" / "research" / "runs" / str(experiment_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_run_artifacts(
    experiment_id: str,
    signals: list[dict],
    trades: list[dict],
    data_dir: Path | str | None = None,
) -> Path:
    """Persist executed signals + trades for one experiment (JSON, immutable).

    Never overwrites differing content with the same experiment id: identical
    rewrites return, differing rewrites raise (mirrors save_history).
    """
    d = _run_dir(data_dir, experiment_id)
    payload = {"experiment_id": str(experiment_id), "signals": signals, "trades": trades}
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    path = d / "run.json"
    if path.exists():
        try:
            existing_text = path.read_text(encoding="utf-8")
            json.loads(existing_text)
        except Exception as exc:
            backup = _backup_corrupt(path)
            raise ValueError(
                f"corrupt run file {path} (backed up to {backup}) — refusing overwrite"
            ) from exc
        if existing_text == text:
            return path
        raise FileExistsError(f"immutable run already exists: {experiment_id}")
    path.write_text(text, encoding="utf-8")
    return path


def load_run_artifacts(
    experiment_id: str, data_dir: Path | str | None = None
) -> tuple[list[dict], list[dict]]:
    """Return (signals, trades) for an executed experiment (empty when none).

    Raises :class:`ValueError` on corrupt content (after backing it up) —
    a corrupt run is never mistaken for "no run".
    """
    path = _run_dir(data_dir, experiment_id) / "run.json"
    if not path.exists():
        return [], []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        backup = _backup_corrupt(path)
        raise ValueError(f"corrupt run file {path} (backed up to {backup})") from exc
    try:
        signals = list(data.get("signals", []))
        trades = list(data.get("trades", []))
    except Exception as exc:
        backup = _backup_corrupt(path)
        raise ValueError(f"malformed run payload in {path} (backed up to {backup})") from exc
    return signals, trades
