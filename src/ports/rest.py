"""Small transport ports for public JSON REST requests."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class PublicJsonResponse:
    """A successful public response with enough provenance for raw archiving."""

    path: str
    params: Mapping[str, str]
    payload: Any
    status_code: int = 200
    headers: Mapping[str, str] = field(default_factory=dict)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        path = str(self.path).strip()
        if not path:
            raise ValueError("public response path must not be empty")
        if not 200 <= int(self.status_code) < 300:
            raise ValueError("public response status must be successful")
        received_at = self.received_at
        if received_at.tzinfo is None or received_at.utcoffset() is None:
            raise ValueError("public response received_at must include timezone information")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "status_code", int(self.status_code))
        object.__setattr__(self, "params", {str(key): str(value) for key, value in self.params.items()})
        object.__setattr__(self, "headers", {str(key).lower(): str(value) for key, value in self.headers.items()})
        object.__setattr__(self, "received_at", received_at.astimezone(UTC))


class PublicRestError(RuntimeError):
    """A classified public REST failure without response-body leakage."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = str(kind)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class JsonRestTransport(Protocol):
    def get_json(self, path: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        """Fetch one public JSON object without exposing credentials."""


class JsonValueRestTransport(Protocol):
    def get_json_value(self, path: str, params: Mapping[str, str]) -> Any:
        """Fetch a public JSON value, including array responses."""


class JsonValueResponseTransport(Protocol):
    def get_json_value_with_metadata(
        self,
        path: str,
        params: Mapping[str, str],
    ) -> PublicJsonResponse:
        """Fetch a public JSON value and retain safe response provenance."""

