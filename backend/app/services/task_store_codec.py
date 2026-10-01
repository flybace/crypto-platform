"""Small JSON codecs shared by the SQL task-store modules."""

from __future__ import annotations

import json
from typing import Any


UNSET = object()


def dump(value: Any) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def load(value: Any) -> Any:
    if not value:
        return {}
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"raw": str(value)}


def now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()
