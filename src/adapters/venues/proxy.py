"""Validation and safe display helpers for explicit public HTTP proxies."""

from __future__ import annotations

from urllib.parse import urlsplit


MAX_PROXY_URL_LENGTH = 500
SUPPORTED_PROXY_SCHEMES = frozenset({"http", "https"})


def normalize_public_proxy(value: str | None) -> str:
    """Return a validated proxy URL or an empty string for direct access."""
    normalized = "" if value is None else str(value).strip()
    if not normalized:
        return ""
    if len(normalized) > MAX_PROXY_URL_LENGTH:
        raise ValueError(f"proxy must be at most {MAX_PROXY_URL_LENGTH} characters")
    try:
        parsed = urlsplit(normalized)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("proxy must be an absolute HTTP or HTTPS URL") from error
    if (
        parsed.scheme.lower() not in SUPPORTED_PROXY_SCHEMES
        or not hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("proxy must be an absolute HTTP or HTTPS URL")
    return normalized


def redact_public_proxy(value: str | None) -> str | None:
    """Return a display-safe proxy URL without credentials."""
    normalized = normalize_public_proxy(value)
    if not normalized:
        return None
    parsed = urlsplit(normalized)
    hostname = parsed.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    credentials = "***:***@" if parsed.username or parsed.password else ""
    port_suffix = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{credentials}{hostname}{port_suffix}"
