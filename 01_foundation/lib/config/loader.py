import os
from pathlib import Path
from typing import Any

import yaml

from lib.config.schemas import AppConfig


_CONFIG_DIR: Path | None = None


def set_config_dir(path: str | Path) -> None:
    global _CONFIG_DIR
    _CONFIG_DIR = Path(path)


def config_path() -> Path:
    if _CONFIG_DIR is not None:
        return _CONFIG_DIR
    env = os.environ.get("VAYREN_CONFIG_DIR")
    if env:
        return Path(env)
    return Path.home() / ".vayren" / "config"


def load_config(name: str = "config.yaml") -> AppConfig:
    path = config_path() / name
    if not path.exists():
        return AppConfig()
    with path.open(encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f)
    return AppConfig(**data)


def save_config(config: AppConfig, name: str = "config.yaml") -> Path:
    path = config_path() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(mode="python"), f, default_flow_style=False)
    return path
