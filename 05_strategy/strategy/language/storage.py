"""Persistence for strategies — inside VAYREN, not VS Code.

Generic Strategy Library: one file per strategy, stable UUID identity separate
from display name. File format is JSON (id, name, code, timestamps) with
backward compatibility for legacy plain-code .vstrat files.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_NAME = "Untitled Strategy"
DEFAULT_CODE = ""

# Legacy OBR source preserved if file already exists; not used as template for new strategies.
LEGACY_OBR_NAME = "OBR SELL v1.0"
LEGACY_OBR_CODE = """strategy("OBR SELL v1.0")

# --- parameters exposed to UI ---
c1_thresh = input(1.25, "C1 Range")
c4_thresh = input(0.56, "C4 Range")
rsi_thr = input(65, "RSI Threshold")

# --- indicators ---
rsi = RSI(14)
atr = ATR(14)
ch_range = range(20)

# --- entry: short rejection near top ---
sell_condition = close >= (high - ch_range * 0.15 * c1_thresh) and rsi >= rsi_thr and volume >= 1000

if sell_condition:
    sell()
    stop_loss(close + c1_thresh * (high - low))
    take_profit(close - 2 * c1_thresh * (high - low))

# --- exit: cover when mid break ---
if close < (high + low) / 2 and rsi < 50:
    close_position()

time_exit("15:15")
"""


def strategy_dir(data_dir: Path | str | None) -> Path:
    # Use VAYREN_DATA_DIR or cwd strategies folder
    base = Path(data_dir) if data_dir else Path.cwd()
    # store alongside strategies subfolder of data_dir, fallback to cwd/.vayren
    if base.is_dir():  # noqa: SIM108
        d = base / "strategies"
    else:
        d = Path.cwd() / ".vayren" / "strategies"
    d.mkdir(parents=True, exist_ok=True)
    return d


def strategy_path(name: str, data_dir: Path | str | None = None) -> Path:
    safe = "".join(c if c.isalnum() or c in " -_." else "_" for c in name).strip()
    if not safe:
        safe = "strategy"
    return strategy_dir(data_dir) / f"{safe}.vstrat"


@dataclass(frozen=True)
class StrategyRecord:
    """Generic Strategy Library record — stable ID separate from display name."""

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
    # Try JSON with id/code
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
    # Legacy plain-code file: id is stem, name is stem
    return StrategyRecord(
        id=path.stem,
        name=path.stem,
        code=text,
        created_at="",
        updated_at="",
    )


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
    """Save strategy code under `name`, preserving stable ID if file exists.

    New files get a fresh UUID; existing files keep their ID and update
    code/name/timestamps. File content is JSON (id, name, code) with
    backward-compatible plain-text fallback on read.
    """
    p = strategy_path(name, data_dir)
    now = _now_iso()
    existing = _read_record(p) if p.exists() else None
    if existing is not None:
        # Preserve stable ID from existing record (JSON or legacy plain)
        record = StrategyRecord(
            id=existing.id,
            name=name,
            code=code,
            created_at=existing.created_at or now,
            updated_at=now,
            version=existing.version,
        )
    else:
        # New strategy: stable UUID
        record = StrategyRecord(
            id=str(uuid.uuid4()),
            name=name,
            code=code,
            created_at=now,
            updated_at=now,
        )
    return _write_record(p, record)


def load_strategy(name: str = DEFAULT_NAME, data_dir: Path | str | None = None) -> str | None:
    p = strategy_path(name, data_dir)
    if not p.exists():
        return None
    record = _read_record(p)
    return record.code if record else None


def load_strategy_record(name: str, data_dir: Path | str | None = None) -> StrategyRecord | None:
    """Load the full record (id, name, code) for `name`."""
    p = strategy_path(name, data_dir)
    if not p.exists():
        return None
    return _read_record(p)


def list_strategies(data_dir: Path | str | None = None) -> list[str]:
    d = strategy_dir(data_dir)
    names: list[str] = []
    for p in d.glob("*.vstrat"):
        try:
            names.append(p.stem)
        except Exception:
            continue
    return sorted(names)


def list_strategies_with_mtime(data_dir: Path | str | None = None) -> list[tuple[str, float]]:
    """(name, epoch mtime) pairs sorted by name — real file modification times."""
    d = strategy_dir(data_dir)
    items: list[tuple[str, float]] = []
    for p in d.glob("*.vstrat"):
        try:
            items.append((p.stem, p.stat().st_mtime))
        except Exception:
            continue
    return sorted(items)


def delete_strategy(name: str, data_dir: Path | str | None = None) -> bool:
    """Remove a saved strategy file. Returns False when it does not exist."""
    p = strategy_path(name, data_dir)
    if not p.exists():
        return False
    p.unlink()
    return True


def rename_strategy(old_name: str, new_name: str, data_dir: Path | str | None = None) -> Path:
    """Rename a saved strategy file; preserves stable ID, updates name field."""
    src = strategy_path(old_name, data_dir)
    if not src.exists():
        raise FileNotFoundError(f"strategy not found: {old_name}")
    dst = strategy_path(new_name, data_dir)
    if dst.exists():
        raise FileExistsError(f"strategy already exists: {new_name}")
    # Preserve ID but update name in content
    record = _read_record(src)
    if record is not None:
        # Write updated record to new location with same id
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
    """Copy a saved strategy under a new name; new stable ID, same code."""
    record = load_strategy_record(name, data_dir)
    if record is None:
        raise FileNotFoundError(f"strategy not found: {name}")
    # New record with fresh ID, same code
    return save_strategy(record.code, copy_name, data_dir)


def ensure_default(data_dir: Path | str | None = None) -> str:
    code = load_strategy(DEFAULT_NAME, data_dir)
    if code is None:
        return DEFAULT_CODE
    return code


def list_strategy_records(data_dir: Path | str | None = None) -> list[StrategyRecord]:
    """All strategy records with stable IDs, sorted by name."""
    d = strategy_dir(data_dir)
    records: list[StrategyRecord] = []
    for p in d.glob("*.vstrat"):
        rec = _read_record(p)
        if rec is not None:
            records.append(rec)
    return sorted(records, key=lambda r: r.name.lower())


def get_strategy_by_id(
    strategy_id: str, data_dir: Path | str | None = None
) -> StrategyRecord | None:
    """Find a strategy by its stable ID."""
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
    """Update code/name for the strategy with `strategy_id`, preserving ID."""
    for rec in list_strategy_records(data_dir):
        if rec.id == strategy_id:
            code = new_code if new_code is not None else rec.code
            name = new_name if new_name is not None else rec.name
            # If name changed, need to rename file
            if name != rec.name:
                # Write to new path with same id, then remove old
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
            # Same name, just update code
            updated = StrategyRecord(
                id=rec.id, name=name, code=code, created_at=rec.created_at, updated_at=_now_iso()
            )
            _write_record(strategy_path(name, data_dir), updated)
            return updated
    return None


def create_strategy(name: str, code: str, data_dir: Path | str | None = None) -> StrategyRecord:
    """Create a new strategy with stable UUID; raises if name exists."""
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
    """Create initial library records for builtins if directory is empty.

    Uses LEGACY_OBR_CODE for OBR SELL and simple placeholders for OBR/SMA.
    Also creates initial V1 for each strategy in the version graph.
    Safe to call on every startup — creates only when no strategies exist.
    """
    if list_strategies(data_dir):
        return
    # OBR SELL v1.0 — from legacy code
    save_strategy(LEGACY_OBR_CODE, LEGACY_OBR_NAME, data_dir)
    # OBR — generic breakout (VM-only, no Python factory)
    obr_code = """strategy("OBR")
ref_index = input(3, "Reference Candle Index")
exit_hour = input(15, "Exit Hour")
exit_min = input(15, "Exit Minute")
ref_range = range(20)
rsi_val = RSI(14)
is_up_break = close > high - ref_range * 0.10
is_down_break = close < low + ref_range * 0.10
if is_up_break and rsi_val > 55:
    buy()
    stop_loss(low)
    take_profit(close + ref_range)
if is_down_break and rsi_val < 45:
    sell()
    stop_loss(high)
    take_profit(close - ref_range)
time_exit("15:15")
"""
    save_strategy(obr_code, "OBR", data_dir)
    # SMA Crossover — generic cross (VM-only)
    sma_code = """strategy("SMA Crossover")
fast_period = input(10, "Fast period")
slow_period = input(30, "Slow period")
fast = SMA(fast_period)
slow = SMA(slow_period)
if fast > slow and prev_fast <= prev_slow:
    buy()
if fast < slow and prev_fast >= prev_slow:
    sell()
prev_fast = fast
prev_slow = slow
"""
    save_strategy(sma_code, "SMA Crossover", data_dir)
    # Create initial versions for each builtin (generic, not strategy-specific)
    try:
        from strategy.version import create_version  # noqa: I001
        from strategy.language import compile_to_ir
        import hashlib
        import json as _json  # noqa: F401

        for name in [LEGACY_OBR_NAME, "OBR", "SMA Crossover"]:
            rec = load_strategy_record(name, data_dir)
            if rec is None:
                continue
            ir_snapshot: str | None = None
            ir_hash = ""
            ir_version = 1
            params: dict[str, float] = {}
            try:
                ir = compile_to_ir(rec.code)
                ir_snapshot = ir.to_json()
                ir_hash = hashlib.sha256(ir_snapshot.encode("utf-8")).hexdigest()
                ir_version = ir.ir_version
                params = {p.label: float(p.default) for p in ir.parameters}
            except Exception:
                ir_hash = hashlib.sha256(rec.code.encode("utf-8")).hexdigest()
                ir_snapshot = None
            try:  # noqa: SIM105
                create_version(
                    rec.id,
                    rec.code,
                    ir_version=ir_version,
                    ir_hash=ir_hash,
                    ir_snapshot=ir_snapshot,
                    parameters=params,
                    data_dir=data_dir,
                    metadata={"name": name, "bootstrap": True},
                )
            except Exception:
                pass
    except Exception:
        pass
