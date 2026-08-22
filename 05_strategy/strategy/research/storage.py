"""Research storage — generic file-based, one JSON per object."""

from __future__ import annotations

import json
from pathlib import Path

from .discovery import Discovery
from .experiment import Experiment
from .intelligence import IntelligenceRun


def _research_dir(data_dir: Path | str | None, sub: str) -> Path:
    base = Path(data_dir) if data_dir and Path(data_dir).is_dir() else Path.cwd() / ".vayren"
    if data_dir and Path(data_dir).is_dir():
        d = Path(data_dir) / "research" / sub
    else:
        d = Path.cwd() / ".vayren" / "research" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_experiment(exp: Experiment, data_dir: Path | str | None = None) -> Path:
    p = _research_dir(data_dir, "experiments") / f"{exp.experiment_id}.json"
    p.write_text(exp.to_json(), encoding="utf-8")
    return p


def load_experiment(experiment_id: str, data_dir: Path | str | None = None) -> Experiment | None:
    p = _research_dir(data_dir, "experiments") / f"{experiment_id}.json"
    if not p.exists():
        return None
    try:
        return Experiment.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def list_experiments(data_dir: Path | str | None = None) -> list[Experiment]:
    d = _research_dir(data_dir, "experiments")
    exps: list[Experiment] = []
    for p in d.glob("*.json"):
        e = load_experiment(p.stem, data_dir)
        if e:
            exps.append(e)
    exps.sort(key=lambda x: x.created_at)
    return exps


def save_discovery(disc: Discovery, data_dir: Path | str | None = None) -> Path:
    p = _research_dir(data_dir, "discoveries") / f"{disc.discovery_id}.json"
    p.write_text(disc.to_json(), encoding="utf-8")
    return p


def load_discovery(discovery_id: str, data_dir: Path | str | None = None) -> Discovery | None:
    p = _research_dir(data_dir, "discoveries") / f"{discovery_id}.json"
    if not p.exists():
        return None
    try:
        return Discovery.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return None


def save_intelligence_run(run: IntelligenceRun, data_dir: Path | str | None = None) -> Path:
    p = _research_dir(data_dir, "intelligence_runs") / f"{run.run_id}.json"
    p.write_text(run.to_json(), encoding="utf-8")
    return p


def load_intelligence_run(run_id: str, data_dir: Path | str | None = None) -> IntelligenceRun | None:
    p = _research_dir(data_dir, "intelligence_runs") / f"{run_id}.json"
    if not p.exists():
        return None
    try:
        import json as _json

        data = _json.loads(p.read_text(encoding="utf-8"))
        from .intelligence import IntelligenceRun as _IR

        return _IR(
            run_id=str(data["run_id"]),
            strategy_id=str(data["strategy_id"]),
            version_id=str(data["version_id"]),
            execution_ids=tuple(data.get("execution_ids", [])),
            configuration=dict(data.get("configuration", {})),
            hypotheses_tested=int(data.get("hypotheses_tested", 0)),
            candidates_found=int(data.get("candidates_found", 0)),
            discoveries=tuple(data.get("discoveries", [])),
            created_at=str(data.get("created_at", "")),
            result_summary=dict(data.get("result_summary", {})),
        )
    except Exception:
        return None
