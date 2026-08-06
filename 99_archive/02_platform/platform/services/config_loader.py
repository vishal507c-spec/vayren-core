from pathlib import Path
from typing import Any

from lib.config.schemas import AppConfig


class ConfigLoader:
    def __init__(self, config_dir: str | Path | None = None) -> None:
        self._config_dir = Path(config_dir) if config_dir else Path.home() / ".vayren" / "config"

    def load(self, name: str = "config.yaml") -> AppConfig:
        path = self._config_dir / name
        if not path.exists():
            return AppConfig()
        import yaml
        with path.open(encoding="utf-8") as f:
            data: dict[str, Any] = yaml.safe_load(f)
        return AppConfig(**data)

    def default_config(self) -> AppConfig:
        return AppConfig()
