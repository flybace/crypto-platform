"""Redis health projection used by the control plane and future workers."""

from __future__ import annotations

from typing import Any


class RedisRuntime:
    def __init__(self, url: str, *, required: bool = False) -> None:
        self.url = str(url or "").strip()
        self.required = bool(required)

    def status(self) -> dict[str, object]:
        if not self.url:
            return {
                "enabled": False,
                "required": self.required,
                "ok": False,
                "status": "disabled",
                "message": "CRYPTO_REDIS_URL is empty",
            }
        try:
            from redis import Redis

            client = Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            return {
                "enabled": True,
                "required": self.required,
                "ok": True,
                "status": "ready",
                "message": "redis coordination is available",
            }
        except Exception as error:  # pragma: no cover - depends on runtime service
            return {
                "enabled": True,
                "required": self.required,
                "ok": False,
                "status": "unreachable",
                "message": str(error),
            }


def dependency_ready(status: dict[str, Any]) -> bool:
    """Required dependencies only block runtime readiness when explicitly required."""
    return not bool(status.get("required")) or bool(status.get("ok"))
