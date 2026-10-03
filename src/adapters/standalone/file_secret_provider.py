"""File-based secret provider for exchange API credentials.

Stores per-venue API key/secret pairs in a JSON file with 0600 permissions.
Falls back to environment variables (CRYPTO_<VENUE>_API_KEY / _API_SECRET)
when the file has no entry — env vars take precedence so existing deployments
keep working.

The file lives outside git (see .gitignore). It survives VM resets because
it sits under ~/workspace.

Security notes:
- The file is created with 0600; we chmod on every write as a safety net.
- This module never logs secret values.
- get_api_key returns the value; get_api_secret returns the value.
  Callers must never expose secrets via API responses — only masked prefixes.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class FileSecretProvider:
    def __init__(self, path: str | Path, *, env_prefix: str = "CRYPTO") -> None:
        self._path = Path(path)
        self._env_prefix = str(env_prefix or "CRYPTO").strip().upper() or "CRYPTO"

    def _env_var(self, venue_id: str, kind: str) -> str:
        venue = str(venue_id or "").strip().upper()
        return f"{self._env_prefix}_{venue}_{kind}"

    def _read_file(self) -> dict:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    def _write_file(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), prefix=".secrets-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
            os.chmod(self._path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get_api_key(self, venue_id: str) -> str | None:
        env = os.getenv(self._env_var(venue_id, "API_KEY"), "").strip()
        if env:
            return env
        entry = self._read_file().get(str(venue_id).strip().lower())
        if isinstance(entry, dict):
            value = str(entry.get("api_key", "")).strip()
            return value or None
        return None

    def get_api_secret(self, venue_id: str) -> str | None:
        env = os.getenv(self._env_var(venue_id, "API_SECRET"), "").strip()
        if env:
            return env
        entry = self._read_file().get(str(venue_id).strip().lower())
        if isinstance(entry, dict):
            value = str(entry.get("api_secret", "")).strip()
            return value or None
        return None

    def is_configured(self, venue_id: str) -> bool:
        return self.get_api_key(venue_id) is not None and self.get_api_secret(venue_id) is not None

    def key_prefix(self, venue_id: str, chars: int = 4) -> str | None:
        """Return a masked prefix for UI display, never the full key."""
        key = self.get_api_key(venue_id)
        if not key:
            return None
        return key[:max(1, chars)] + "****"

    def save(self, venue_id: str, api_key: str, api_secret: str) -> None:
        key = str(api_key or "").strip()
        secret = str(api_secret or "").strip()
        if not key or not secret:
            raise ValueError("api_key and api_secret must not be empty")
        if len(key) < 8 or len(secret) < 8:
            raise ValueError("credential looks too short to be valid")
        data = self._read_file()
        data[str(venue_id).strip().lower()] = {"api_key": key, "api_secret": secret}
        self._write_file(data)

    def delete(self, venue_id: str) -> bool:
        data = self._read_file()
        vid = str(venue_id).strip().lower()
        if vid not in data:
            return False
        del data[vid]
        self._write_file(data)
        return True
