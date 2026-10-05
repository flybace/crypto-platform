"""Explicit auto-trading toggle.

Auto-trading can ONLY be enabled through an explicit user action on the
settings API. It defaults to disabled. This global switch is ANDed with each
paper-live instance's own ``auto_trading`` flag: the engine places a simulated
order only when both are on. When off, the engine still ticks and records
signals, but never touches the (simulated) account. The real-order execution
path (M6, not yet implemented) must check this flag as well.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock


class TradingSettingsStore:
    """Persist the explicit auto-trading toggle as a small JSON document."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def _read(self) -> dict:
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def is_enabled(self) -> bool:
        with self._lock:
            data = self._read()
            if isinstance(data.get("auto_trading_enabled"), bool):
                return bool(data["auto_trading_enabled"])
            env = os.getenv("CRYPTO_AUTO_TRADING_ENABLED", "").strip().lower()
            return env in ("1", "true", "yes", "on")

    def set_enabled(self, enabled: bool) -> bool:
        with self._lock:
            data = self._read()
            data["auto_trading_enabled"] = bool(enabled)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".trading-settings-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle)
                os.replace(tmp, self._path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return bool(data["auto_trading_enabled"])
