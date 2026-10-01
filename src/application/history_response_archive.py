"""Durable, credential-free archives of successful public history responses."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from domain.candle import HistoryQuery
from ports.rest import PublicJsonResponse


class HistoryResponseArchiveError(RuntimeError):
    """A raw public history response could not be archived safely."""


class HistoryResponseArchive:
    """Store immutable envelopes for every successful paginated history response."""

    CONTRACT_VERSION = "history-raw-response-v1"

    def __init__(self, history_root: str | Path, archive_root: str | Path | None = None) -> None:
        self.history_root = Path(history_root)
        self.archive_root = Path(archive_root) if archive_root is not None else self.history_root / "_raw"

    def archive(
        self,
        query: HistoryQuery,
        responses: Sequence[PublicJsonResponse],
        *,
        dataset_id: str,
    ) -> dict[str, object]:
        """Write all pages atomically and return DB-ready provenance records."""
        if not responses:
            raise HistoryResponseArchiveError("history download did not expose any raw responses")
        envelopes: list[tuple[Path, dict[str, object], bytes]] = []
        for page_index, response in enumerate(responses, start=1):
            envelope, content = self._envelope(query, response, dataset_id=dataset_id, page_index=page_index)
            key = self._archive_key(query, str(envelope["response_id"]))
            path = self.archive_path(key)
            envelopes.append((path, envelope, content))

        items: list[dict[str, object]] = []
        try:
            for path, envelope, content in envelopes:
                state = self._write_idempotent(path, envelope, content)
                items.append(
                    {
                        "response_id": envelope["response_id"],
                        "dataset_id": dataset_id,
                        "storage_key": None,
                        "venue_id": query.venue_id,
                        "market_type": query.market_type.value,
                        "instrument_key": query.instrument_key,
                        "native_symbol": query.native_symbol,
                        "interval": query.interval.value,
                        "page_index": envelope["page_index"],
                        "request_path": envelope["request"]["path"],
                        "request_params": dict(envelope["request"]["params"]),
                        "archive_key": self._relative_key(path),
                        "payload_sha256": envelope["payload_sha256"],
                        "content_sha256": hashlib.sha256(content).hexdigest() if state == "written" else self._file_sha256(path),
                        "size_bytes": path.stat().st_size,
                        "received_at": envelope["received_at"],
                        "state": "READY",
                        "write_state": state,
                    }
                )
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise HistoryResponseArchiveError("raw history response archive failed") from error
        return {
            "status": "COMPLETED",
            "contract_version": self.CONTRACT_VERSION,
            "archive_root": self._relative_root(),
            "response_count": len(items),
            "written_count": sum(1 for item in items if item["write_state"] == "written"),
            "existing_count": sum(1 for item in items if item["write_state"] == "existing"),
            "items": items,
            "error": None,
        }

    def status(self, *, limit: int = 100) -> dict[str, object]:
        paths = sorted(self.archive_root.rglob("*.json")) if self.archive_root.exists() else []
        records: list[dict[str, object]] = []
        invalid_count = 0
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                records.append(self._record(path, payload))
            except (OSError, TypeError, ValueError, KeyError, AttributeError, HistoryResponseArchiveError, json.JSONDecodeError):
                invalid_count += 1
        bounded = max(1, min(int(limit), 500))
        return {
            "status": "READY" if not invalid_count else "DEGRADED",
            "contract_version": self.CONTRACT_VERSION,
            "archive_root": self._relative_root(),
            "response_count": len(paths),
            "invalid_count": invalid_count,
            "items": records[:bounded],
        }

    def archive_path(self, archive_key: str) -> Path:
        relative = Path(str(archive_key))
        if relative.is_absolute() or relative.suffix.lower() != ".json":
            raise HistoryResponseArchiveError("raw archive accepts relative JSON keys only")
        internal_key = bool(relative.parts) and relative.parts[0] == "_raw"
        path = self.history_root / relative if internal_key else self.archive_root / relative
        root = self.history_root.resolve() if internal_key else self.archive_root.resolve()
        try:
            path.resolve().relative_to(root)
        except ValueError as error:
            raise HistoryResponseArchiveError("raw archive path escaped its root") from error
        return path

    def _envelope(
        self,
        query: HistoryQuery,
        response: PublicJsonResponse,
        *,
        dataset_id: str,
        page_index: int,
    ) -> tuple[dict[str, object], bytes]:
        payload_bytes = self._canonical_json(response.payload)
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        safe_params = self._safe_params(response.params)
        safe_headers = self._safe_headers(response.headers)
        identity = {
            "contract_version": self.CONTRACT_VERSION,
            "dataset_id": dataset_id,
            "page_index": page_index,
            "path": response.path,
            "params": safe_params,
            "payload_sha256": payload_sha256,
        }
        response_id = hashlib.sha256(self._canonical_json(identity)).hexdigest()
        envelope: dict[str, object] = {
            "contract_version": self.CONTRACT_VERSION,
            "response_id": response_id,
            "dataset_id": dataset_id,
            "page_index": page_index,
            "received_at": response.received_at.astimezone(UTC).isoformat(),
            "query": {
                "venue_id": query.venue_id,
                "market_type": query.market_type.value,
                "instrument_key": query.instrument_key,
                "native_symbol": query.native_symbol,
                "interval": query.interval.value,
            },
            "request": {"path": response.path, "params": safe_params},
            "response": {"status_code": response.status_code, "headers": safe_headers},
            "payload_sha256": payload_sha256,
            "payload": response.payload,
        }
        return envelope, self._pretty_json(envelope)

    @staticmethod
    def _canonical_json(value: Any) -> bytes:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise HistoryResponseArchiveError("raw history payload is not JSON serializable") from error

    @staticmethod
    def _pretty_json(value: Any) -> bytes:
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"

    def _write_idempotent(self, path: Path, envelope: dict[str, object], content: bytes) -> str:
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if (
                    existing.get("contract_version") == self.CONTRACT_VERSION
                    and existing.get("response_id") == envelope["response_id"]
                    and existing.get("payload_sha256") == envelope["payload_sha256"]
                ):
                    return "existing"
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                pass
            raise HistoryResponseArchiveError(f"raw archive identity conflict: {path.name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
                temp_name = handle.name
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            return "written"
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except FileNotFoundError:
                    pass

    def _archive_key(self, query: HistoryQuery, response_id: str) -> str:
        return "/".join(
            (
                "_raw",
                self._component(query.venue_id),
                self._component(query.market_type.value),
                self._component(query.native_symbol),
                self._component(query.interval.value),
                f"{response_id}.json",
            )
        )

    def _relative_key(self, path: Path) -> str:
        try:
            return path.relative_to(self.history_root).as_posix()
        except ValueError as error:
            raise HistoryResponseArchiveError("raw archive path escaped history root") from error

    def _relative_root(self) -> str:
        try:
            return self.archive_root.relative_to(self.history_root).as_posix()
        except ValueError:
            return str(self.archive_root)

    @staticmethod
    def _component(value: str) -> str:
        result = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
        if not result or result in {".", ".."}:
            raise HistoryResponseArchiveError("raw archive path component is unsafe")
        return result

    @staticmethod
    def _safe_params(params: Any) -> dict[str, str]:
        sensitive = ("api_key", "apikey", "authorization", "secret", "signature", "token")
        return {
            str(key): str(value)
            for key, value in params.items()
            if not any(marker in str(key).lower() for marker in sensitive)
        }

    @staticmethod
    def _safe_headers(headers: Any) -> dict[str, str]:
        allowed = {"content-type", "date", "etag", "last-modified", "retry-after", "x-mbx-used-weight-1m"}
        return {str(key).lower(): str(value) for key, value in headers.items() if str(key).lower() in allowed}

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _record(self, path: Path, payload: dict[str, object]) -> dict[str, object]:
        if not isinstance(payload, dict) or payload.get("contract_version") != self.CONTRACT_VERSION:
            raise HistoryResponseArchiveError("raw archive contract version is invalid")
        query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
        return {
            "response_id": str(payload["response_id"]),
            "dataset_id": str(payload.get("dataset_id") or ""),
            "archive_key": self._relative_key(path),
            "page_index": int(payload["page_index"]),
            "venue_id": str(query.get("venue_id", "")),
            "native_symbol": str(query.get("native_symbol", "")),
            "interval": str(query.get("interval", "")),
            "payload_sha256": str(payload["payload_sha256"]),
            "size_bytes": path.stat().st_size,
            "received_at": str(payload["received_at"]),
        }
