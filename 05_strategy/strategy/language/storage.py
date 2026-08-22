"""Persistence for strategies — inside VAYREN, not VS Code."""

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
    if base.is_dir():
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


def save_strategy(code: str, name: str = DEFAULT_NAME, data_dir: Path | str | None = None) -> Path:
    p = strategy_path(name, data_dir)
    p.write_text(code, encoding="utf-8")
    return p


def load_strategy(name: str = DEFAULT_NAME, data_dir: Path | str | None = None) -> str | None:
    p = strategy_path(name, data_dir)
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None


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
    """Rename a saved strategy file; raises FileNotFoundError when missing."""
    src = strategy_path(old_name, data_dir)
    if not src.exists():
        raise FileNotFoundError(f"strategy not found: {old_name}")
    dst = strategy_path(new_name, data_dir)
    if dst.exists():
        raise FileExistsError(f"strategy already exists: {new_name}")
    return src.rename(dst)


def duplicate_strategy(name: str, copy_name: str, data_dir: Path | str | None = None) -> Path:
    """Copy a saved strategy under a new name; raises FileNotFoundError when missing."""
    code = load_strategy(name, data_dir)
    if code is None:
        raise FileNotFoundError(f"strategy not found: {name}")
    return save_strategy(code, copy_name, data_dir)


def ensure_default(data_dir: Path | str | None = None) -> str:
    code = load_strategy(DEFAULT_NAME, data_dir)
    if code is None:
        return DEFAULT_CODE
    return code
