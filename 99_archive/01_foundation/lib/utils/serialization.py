import json
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any


class _VayrenEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Decimal):
            return str(obj)
        if isinstance(obj, set):
            return list(obj)
        return super().default(obj)


def to_json(obj: Any, indent: int = 2) -> str:
    """Serialize object to JSON with Vayren Core type support."""
    return json.dumps(obj, cls=_VayrenEncoder, indent=indent)


def from_json(content: str | Path) -> Any:
    """Deserialize JSON string or file to Python object."""
    if isinstance(content, Path):
        content = content.read_text(encoding="utf-8")
    return json.loads(content)
