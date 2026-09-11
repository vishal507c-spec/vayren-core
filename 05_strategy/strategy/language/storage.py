"""Persistence for strategies — Python-native."""

from __future__ import annotations

import json
import os
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_NAME = "Untitled Strategy"
DEFAULT_CODE = """from strategy.strategies.base import PythonStrategy

class Strategy(PythonStrategy):
    def on_bar_logic(self, view):
        # Example: buy when close > open
        if view.bar.close > view.bar.open:
            self.buy()
"""

# Python-native built-in strategies
LEGACY_OBR_NAME = "OBR SELL v1.0"
LEGACY_OBR_CODE = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_range, calc_rsi

class Strategy(PythonStrategy):
    @staticmethod
    def param_specs():
        from strategy.models.parameters import ParameterSpec
        return (
            ParameterSpec(
                key="c1_thresh", label="C1 Range", default=1.25, minimum=0.1, maximum=5.0,
                decimals=2,
            ),
            ParameterSpec(
                key="c4_thresh", label="C4 Range", default=0.56, minimum=0.1, maximum=5.0,
                decimals=2,
            ),
            ParameterSpec(
                key="rsi_thr", label="RSI Threshold", default=65, minimum=30, maximum=90,
                decimals=0,
            ),
        )
    def on_bar_logic(self, view):
        bar = view.bar
        c1 = float(self.params.get("c1_thresh", 1.25))
        rsi_thr = float(self.params.get("rsi_thr", 65))
        rsi = calc_rsi(self.closes, 14)
        ch_range = calc_range(self.highs, self.lows, 20)
        if bar.close >= (bar.high - ch_range * 0.15 * c1) and rsi >= rsi_thr and bar.volume >= 1000:
            self.sell()
            self.stop_loss(bar.close + c1 * (bar.high - bar.low))
            self.take_profit(bar.close - 2 * c1 * (bar.high - bar.low))
            return
        if bar.close < (bar.high + bar.low) / 2 and rsi < 50:
            self.close_position(view)
            return
        self.time_exit("15:15")
"""


def _library_root() -> Path:
    """The strategy library root, resolved once per call.

    Precedence: ``VAYREN_STRATEGIES`` env var → ``<data_dir>/strategies`` when a
    usable ``data_dir`` is supplied → a per-user default under the home
    directory. There is deliberately **no** machine-specific absolute path
    here: the previous ``D:\\VAYREN_STRATEGIES`` literal broke on any machine
    without a ``D:`` drive and contradicted the documented "every path is
    derived from ``data_dir`` and overridable" contract.
    """
    override = os.environ.get("VAYREN_STRATEGIES")
    if override:
        return Path(override)
    return Path.home() / ".vayren" / "strategies"


def strategy_dir(data_dir: Path | str | None = None) -> Path:
    """Resolve the strategy library folder.

    ``VAYREN_STRATEGIES`` (env) always wins. Otherwise the library is
    ``<data_dir>/strategies`` — colocated with the candle store so a single
    data root is self-contained — except under a test/temp data dir, where
    the same rule applies but is created eagerly. When no ``data_dir`` is
    given, a per-user default is used.
    """
    env_root = os.environ.get("VAYREN_STRATEGIES")
    if env_root:
        d = Path(env_root)
        d.mkdir(parents=True, exist_ok=True)
        return d

    if data_dir is not None:
        p = Path(data_dir)
        # Colocate the library with the data root; never escape to a global path.
        d = p / "strategies"
        with suppress(Exception):
            d.mkdir(parents=True, exist_ok=True)
        return d

    d = _library_root()
    with suppress(Exception):
        d.mkdir(parents=True, exist_ok=True)
    return d


def strategy_path(name: str, data_dir: Path | str | None = None) -> Path:
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip()
    if not safe:
        safe = "strategy"
    return strategy_dir(data_dir) / f"{safe}.py"


@dataclass(frozen=True)
class StrategyRecord:
    id: str
    name: str
    code: str
    created_at: str
    updated_at: str
    version: str = "1.0"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _read_record(path: Path) -> StrategyRecord | None:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "id" in data and "code" in data:
            return StrategyRecord(
                id=str(data["id"]),
                name=str(data.get("name", path.stem)),
                code=str(data["code"]),
                created_at=str(data.get("created_at", "")),
                updated_at=str(data.get("updated_at", "")),
                version=str(data.get("version", "1.0")),
            )
    except Exception:
        pass
    # Fallback: plain Python file
    return StrategyRecord(id=path.stem, name=path.stem, code=text, created_at="", updated_at="")


def _write_record(path: Path, record: StrategyRecord) -> Path:
    payload = {
        "id": record.id,
        "name": record.name,
        "code": record.code,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "version": record.version,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def save_strategy(code: str, name: str = DEFAULT_NAME, data_dir: Path | str | None = None) -> Path:
    p = strategy_path(name, data_dir)
    now = _now_iso()
    existing = _read_record(p) if p.exists() else None
    if existing is not None:
        record = StrategyRecord(
            id=existing.id,
            name=name,
            code=code,
            created_at=existing.created_at or now,
            updated_at=now,
            version=existing.version,
        )
    else:
        record = StrategyRecord(
            id=str(uuid.uuid4()), name=name, code=code, created_at=now, updated_at=now
        )
    return _write_record(p, record)


def load_strategy(name: str = DEFAULT_NAME, data_dir: Path | str | None = None) -> str | None:
    p = strategy_path(name, data_dir)
    if not p.exists():
        return None
    record = _read_record(p)
    return record.code if record else None


def load_strategy_record(name: str, data_dir: Path | str | None = None) -> StrategyRecord | None:
    p = strategy_path(name, data_dir)
    if not p.exists():
        return None
    return _read_record(p)


def list_strategies(data_dir: Path | str | None = None) -> list[str]:
    d = strategy_dir(data_dir)
    names: list[str] = []
    for p in d.glob("*.py"):
        try:
            names.append(p.stem)
        except Exception:
            continue
    return sorted(names)


def list_strategies_with_mtime(data_dir: Path | str | None = None) -> list[tuple[str, float]]:
    d = strategy_dir(data_dir)
    items: list[tuple[str, float]] = []
    for p in d.glob("*.py"):
        try:
            items.append((p.stem, p.stat().st_mtime))
        except Exception:
            continue
    return sorted(items)


def delete_strategy(name: str, data_dir: Path | str | None = None) -> bool:
    p = strategy_path(name, data_dir)
    if not p.exists():
        return False
    p.unlink()
    return True


def rename_strategy(old_name: str, new_name: str, data_dir: Path | str | None = None) -> Path:
    src = strategy_path(old_name, data_dir)
    if not src.exists():
        raise FileNotFoundError(f"strategy not found: {old_name}")
    dst = strategy_path(new_name, data_dir)
    if dst.exists():
        raise FileExistsError(f"strategy already exists: {new_name}")
    record = _read_record(src)
    if record is not None:
        updated = StrategyRecord(
            id=record.id,
            name=new_name,
            code=record.code,
            created_at=record.created_at or _now_iso(),
            updated_at=_now_iso(),
            version=record.version,
        )
        _write_record(dst, updated)
        src.unlink()
        return dst
    return src.rename(dst)


def duplicate_strategy(name: str, copy_name: str, data_dir: Path | str | None = None) -> Path:
    record = load_strategy_record(name, data_dir)
    if record is None:
        raise FileNotFoundError(f"strategy not found: {name}")
    return save_strategy(record.code, copy_name, data_dir)


def ensure_default(data_dir: Path | str | None = None) -> str:
    code = load_strategy(DEFAULT_NAME, data_dir)
    if code is None:
        return DEFAULT_CODE
    return code


def list_strategy_records(data_dir: Path | str | None = None) -> list[StrategyRecord]:
    d = strategy_dir(data_dir)
    records: list[StrategyRecord] = []
    for p in d.glob("*.py"):
        rec = _read_record(p)
        if rec is not None:
            records.append(rec)
    return sorted(records, key=lambda r: r.name.lower())


def get_strategy_by_id(
    strategy_id: str, data_dir: Path | str | None = None
) -> StrategyRecord | None:
    for rec in list_strategy_records(data_dir):
        if rec.id == strategy_id:
            return rec
    return None


def update_strategy(
    strategy_id: str,
    new_code: str | None = None,
    new_name: str | None = None,
    data_dir: Path | str | None = None,
) -> StrategyRecord | None:
    for rec in list_strategy_records(data_dir):
        if rec.id == strategy_id:
            code = new_code if new_code is not None else rec.code
            name = new_name if new_name is not None else rec.name
            if name != rec.name:
                old_path = strategy_path(rec.name, data_dir)
                new_path = strategy_path(name, data_dir)
                if new_path.exists() and new_path != old_path:
                    raise FileExistsError(f"strategy already exists: {name}")
                updated = StrategyRecord(
                    id=rec.id,
                    name=name,
                    code=code,
                    created_at=rec.created_at,
                    updated_at=_now_iso(),
                )
                _write_record(new_path, updated)
                if old_path != new_path:
                    old_path.unlink(missing_ok=True)
                return updated
            updated = StrategyRecord(
                id=rec.id, name=name, code=code, created_at=rec.created_at, updated_at=_now_iso()
            )
            _write_record(strategy_path(name, data_dir), updated)
            return updated
    return None


def create_strategy(name: str, code: str, data_dir: Path | str | None = None) -> StrategyRecord:
    p = strategy_path(name, data_dir)
    if p.exists():
        raise FileExistsError(f"strategy already exists: {name}")
    now = _now_iso()
    record = StrategyRecord(
        id=str(uuid.uuid4()), name=name, code=code, created_at=now, updated_at=now
    )
    _write_record(p, record)
    return record


def ensure_builtin_strategies(data_dir: Path | str | None = None) -> None:
    if data_dir is None:
        return
    p = Path(data_dir)
    s = str(p).lower()
    if not ("tmp" in s or "temp" in s or "pytest" in s):
        return
    if list_strategies(data_dir):
        return
    save_strategy(LEGACY_OBR_CODE, LEGACY_OBR_NAME, data_dir)
    obr_code = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_range, calc_rsi

class Strategy(PythonStrategy):
    @staticmethod
    def param_specs():
        from strategy.models.parameters import ParameterSpec
        return (
            ParameterSpec(key="ref_index", label="Reference Candle Index", default=3, minimum=1, maximum=10, decimals=0),
            ParameterSpec(key="exit_hour", label="Exit Hour", default=15, minimum=0, maximum=23, decimals=0),
            ParameterSpec(key="exit_min", label="Exit Minute", default=15, minimum=0, maximum=59, decimals=0),
        )
    def on_bar_logic(self, view):
        bar = view.bar
        ref_range = calc_range(self.highs, self.lows, 20)
        rsi_val = calc_rsi(self.closes, 14)
        is_up_break = bar.close > bar.high - ref_range * 0.10
        is_down_break = bar.close < bar.low + ref_range * 0.10
        if is_up_break and rsi_val > 55:
            self.buy()
            self.stop_loss(bar.low)
            self.take_profit(bar.close + ref_range)
            return
        if is_down_break and rsi_val < 45:
            self.sell()
            self.stop_loss(bar.high)
            self.take_profit(bar.close - ref_range)
            return
        self.time_exit("15:15")
"""
    save_strategy(obr_code, "OBR", data_dir)
    sma_code = """from strategy.strategies.base import PythonStrategy
from strategy.strategies.indicators import calc_sma

class Strategy(PythonStrategy):
    @staticmethod
    def param_specs():
        from strategy.models.parameters import ParameterSpec
        return (
            ParameterSpec(key="fast_period", label="Fast period", default=10, minimum=2, maximum=50, decimals=0),
            ParameterSpec(key="slow_period", label="Slow period", default=30, minimum=5, maximum=100, decimals=0),
        )
    def __init__(self, params=None):
        super().__init__(params)
        self.prev_fast = None
        self.prev_slow = None
    def on_bar_logic(self, view):
        fast_period = int(self.params.get("fast_period", 10))
        slow_period = int(self.params.get("slow_period", 30))
        fast = calc_sma(self.closes, fast_period)
        slow = calc_sma(self.closes, slow_period)
        if self.prev_fast is None:
            self.prev_fast = fast
            self.prev_slow = slow
            return
        if fast > slow and self.prev_fast <= self.prev_slow:
            self.buy()
        elif fast < slow and self.prev_fast >= self.prev_slow:
            self.sell()
        self.prev_fast = fast
        self.prev_slow = slow
"""
    save_strategy(sma_code, "SMA Crossover", data_dir)
    try:
        import hashlib

        from strategy.version import create_version

        for name in [LEGACY_OBR_NAME, "OBR", "SMA Crossover"]:
            rec = load_strategy_record(name, data_dir)
            if rec is None:
                continue
            params = {}
            with suppress(Exception):
                from strategy.language.compiler import compile_strategy

                compiled = compile_strategy(rec.code)
                params = compiled.param_defaults
            with suppress(Exception):
                create_version(
                    rec.id,
                    rec.code,
                    ir_snapshot=None,
                    ir_hash=hashlib.sha256(rec.code.encode()).hexdigest(),
                    ir_version=1,
                    parameters=params,
                    data_dir=data_dir,
                    metadata={"name": name, "bootstrap": True},
                )
    except Exception:
        pass
