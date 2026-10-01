from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import inspect
import json
import logging
import os
import re
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode, urljoin, urlparse

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator


LOGGER = logging.getLogger("proxy-route-manager")
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

DEFAULT_RULE_SOURCES = [
    {
        "id": "reject",
        "name": "Loyalsoldier reject",
        "url": "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/reject.txt",
        "behavior": "domain",
        "format": "yaml",
        "action": "REJECT",
    },
    {
        "id": "direct",
        "name": "Loyalsoldier direct",
        "url": "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/direct.txt",
        "behavior": "domain",
        "format": "yaml",
        "action": "DIRECT",
    },
    {
        "id": "proxy",
        "name": "Loyalsoldier proxy",
        "url": "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/proxy.txt",
        "behavior": "domain",
        "format": "yaml",
        "action": "PROXY",
    },
    {
        "id": "applications",
        "name": "Loyalsoldier applications",
        "url": "https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/applications.txt",
        "behavior": "classical",
        "format": "yaml",
        "action": "DIRECT",
    },
    {
        "id": "global",
        "name": "BlackMatrix7 Global",
        "url": "https://raw.githubusercontent.com/blackmatrix7/ios_rule_script/master/rule/Clash/Global/Global.list",
        "behavior": "classical",
        "format": "text",
        "action": "PROXY",
    },
]

SERVICE_CATALOG = [
    {
        "id": "binance-public",
        "name": "Binance 公共行情",
        "scope": "public-market",
        "domains": [
            "api.binance.com",
            "data-api.binance.vision",
            "stream.binance.com",
        ],
        "sources": ["crypto-platform seed"],
    },
    {
        "id": "okx-public",
        "name": "OKX 公共行情",
        "scope": "public-market",
        "domains": ["www.okx.com", "ws.okx.com"],
        "sources": ["crypto-platform seed"],
    },
    {
        "id": "bybit-public",
        "name": "Bybit 公共行情",
        "scope": "public-market",
        "domains": ["api.bybit-tr.com", "stream.bybit.kz"],
        "sources": ["crypto-platform seed"],
    },
]

DEFAULT_STATE: dict[str, Any] = {
    "version": 2,
    "providers": [],
    "services": [],
    "imported_policy": None,
    "observations": [],
    "analyses": [],
    "capture_enabled": False,
    "rule_checks": {},
    "last_apply": None,
}

HOST_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,252}$")
URL_RE = re.compile(r"(?i)(?:https?|wss?)://[^\s\"'<>\\]+")
ATTR_URL_RE = re.compile(
    r"(?is)(?:href|src|action|poster|data-url|data-src)\s*=\s*[\"']([^\"']+)[\"']"
)
CSS_URL_RE = re.compile(r"(?is)url\(\s*[\"']?([^\)\"']+)[\"']?\s*\)")
PROXY_GROUP_TYPES = {
    "COMPATIBLE",
    "FALLBACK",
    "LOADBALANCE",
    "RELAY",
    "SELECTOR",
    "URLTEST",
}
NON_NODE_TYPES = {"DIRECT", "REJECT", "PASS", "DNS", "PASSRULE", "REJECTDROP"}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slug(value: str, fallback: str = "item") -> str:
    result = re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-").lower()
    return result[:48] or fallback


def normalize_host(value: str) -> str | None:
    raw = value.strip().strip("[]").rstrip(".")
    if not raw:
        return None
    try:
        return str(ipaddress.ip_address(raw))
    except ValueError:
        pass
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    host = parsed.hostname
    if not host:
        return None
    host = host.rstrip(".").lower()
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".lan")):
        return None
    if not HOST_RE.fullmatch(host):
        return None
    return host


def normalize_http_url(value: str) -> str:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("only http:// and https:// URLs are supported")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not accepted")
    host = normalize_host(parsed.hostname)
    if not host:
        raise ValueError("the URL host is not a public hostname")
    return value.strip()


def is_private_host(host: str) -> bool:
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".lan")):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved


def mask_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.hostname:
        return "configured"
    return f"{parsed.scheme}://{parsed.hostname}/***"


def extract_hosts(text: str, base_url: str | None = None) -> set[str]:
    candidates: set[str] = set()
    for raw in URL_RE.findall(text):
        clean = raw.rstrip(".,);]}'\"")
        host = normalize_host(urlparse(clean).hostname or "")
        if host and not is_private_host(host):
            candidates.add(host)
    if base_url:
        for raw in ATTR_URL_RE.findall(text):
            if raw.startswith(("javascript:", "mailto:", "data:", "#")):
                continue
            absolute = urljoin(base_url, raw)
            host = normalize_host(urlparse(absolute).hostname or "")
            if host and not is_private_host(host):
                candidates.add(host)
    return candidates


def extract_resource_urls(text: str, base_url: str) -> set[str]:
    """Return fetchable static resources without retaining query or body data."""
    resources: set[str] = set()
    raw_values = list(ATTR_URL_RE.findall(text)) + list(CSS_URL_RE.findall(text))
    for raw in raw_values:
        value = raw.strip()
        if value.startswith(("javascript:", "mailto:", "data:", "#")):
            continue
        absolute = urljoin(base_url, value)
        parsed = urlparse(absolute)
        if parsed.scheme in {"http", "https"} and parsed.hostname and not is_private_host(parsed.hostname):
            resources.add(absolute)
    return resources


class ProviderRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=2048)
    group: str | None = Field(default=None, max_length=120)
    interval: int = Field(default=86400, ge=300, le=604800)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        result = slug(value)
        if result != value.strip().lower():
            raise ValueError("id may contain only letters, numbers, underscores and hyphens")
        return result

    @field_validator("url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("provider URL must use http:// or https://")
        return value.strip()


class ServiceRequest(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    scope: str = Field(default="custom", min_length=1, max_length=80)
    target: str = Field(default="PROXY", min_length=1, max_length=120)

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        return slug(value)


class DomainRequest(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    match: Literal["domain", "suffix", "ip", "ip-cidr"] = "domain"
    source: str = Field(default="manual", max_length=120)

    @field_validator("host")
    @classmethod
    def valid_host(cls, value: str) -> str:
        result = normalize_host(value)
        if not result or is_private_host(result):
            raise ValueError("host must be a public hostname or IP")
        return result


class DiscoverRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)


class AnalysisStartRequest(BaseModel):
    url: str = Field(min_length=10, max_length=2048)
    expected_status: int = Field(default=200, ge=100, le=599)
    selection_mode: Literal["auto", "manual"] | None = None
    manual_target: str | None = Field(default=None, min_length=1, max_length=255)
    service_name: str | None = Field(default=None, max_length=120)


class AnalysisProbeRequest(BaseModel):
    proxy: str | None = Field(default=None, min_length=1, max_length=255)


class AnalysisCandidateSelection(BaseModel):
    host: str = Field(min_length=1, max_length=253)
    match: Literal["domain", "suffix", "ip", "ip-cidr"] = "domain"


class AnalysisDraftRequest(BaseModel):
    service_id: str | None = Field(default=None, max_length=64)
    service_name: str | None = Field(default=None, max_length=120)
    target: str | None = Field(default=None, max_length=120)
    candidates: list[AnalysisCandidateSelection] = Field(default_factory=list, max_length=500)


class CaptureRequest(BaseModel):
    enabled: bool


class AssignRequest(BaseModel):
    service_id: str
    match: Literal["domain", "suffix", "ip", "ip-cidr"] = "domain"


class PolicyImportRequest(BaseModel):
    name: str = Field(default="imported-clash-config.yaml", min_length=1, max_length=255)
    yaml_text: str = Field(min_length=1, max_length=8_000_000)


def normalize_imported_policy(document: dict[str, Any], source_name: str) -> dict[str, Any]:
    """Keep policy data from a profile while dropping its runtime and node data."""
    raw_groups = document.get("proxy-groups") or []
    raw_rule_sources = document.get("rule-providers") or {}
    raw_rules = document.get("rules") or []
    raw_proxies = document.get("proxies") or []
    if not isinstance(raw_groups, list):
        raise ValueError("proxy-groups must be a list")
    if not isinstance(raw_rule_sources, dict):
        raise ValueError("rule-providers must be a mapping")
    if not isinstance(raw_rules, list) or not all(isinstance(item, str) for item in raw_rules):
        raise ValueError("rules must be a list of strings")

    legacy_proxy_names = sorted(
        {
            str(item.get("name", "")).strip()
            for item in raw_proxies
            if isinstance(item, dict) and str(item.get("name", "")).strip()
        }
    )
    groups: list[dict[str, Any]] = []
    for item in raw_groups:
        if not isinstance(item, dict):
            raise ValueError("each proxy-group must be a mapping")
        name = str(item.get("name", "")).strip()
        group_type = str(item.get("type", "select")).strip()
        if not name or not group_type:
            raise ValueError("every proxy-group needs name and type")
        if name.casefold() == "proxy":
            # The current provider-backed PROXY group is authoritative.
            continue
        group: dict[str, Any] = {
            "name": name,
            "type": group_type,
            "proxies": [str(value).strip() for value in item.get("proxies", []) or [] if str(value).strip()],
        }
        for field in (
            "url",
            "interval",
            "tolerance",
            "lazy",
            "expected-status",
            "strategy",
            "disable-udp",
            "interface-name",
            "routing-mark",
            "max-failed-times",
            "hidden",
            "icon",
        ):
            if field in item:
                group[field] = item[field]
        if "url" in group:
            group["url"] = normalize_http_url(str(group["url"]))
        if "interval" in group:
            try:
                group["interval"] = int(group["interval"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"proxy-group {name} has an invalid interval") from exc
        groups.append(group)

    rule_sources: list[dict[str, Any]] = []
    for name, item in raw_rule_sources.items():
        if not isinstance(item, dict):
            raise ValueError(f"rule-provider {name} must be a mapping")
        provider_type = str(item.get("type", "http")).strip().lower()
        if provider_type not in {"http", "https"}:
            raise ValueError(f"rule-provider {name} is not a remote HTTP provider")
        url = normalize_http_url(str(item.get("url", "")))
        behavior = str(item.get("behavior", "classical")).strip().lower()
        source: dict[str, Any] = {
            "id": str(name),
            "name": str(name),
            "type": "http",
            "behavior": behavior,
            "url": url,
            "interval": int(item.get("interval", 86400)),
        }
        if item.get("format"):
            source["format"] = str(item["format"]).strip().lower()
        rule_sources.append(source)

    rules = [item.strip() for item in raw_rules if item.strip() and not item.lstrip().startswith("#")]
    return {
        "source_name": source_name,
        "imported_at": now_iso(),
        "groups": groups,
        "rule_sources": rule_sources,
        "rules": rules,
        "legacy_proxy_names": legacy_proxy_names,
        "ignored_proxy_count": len(legacy_proxy_names),
    }


def active_rule_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    imported = state.get("imported_policy") or {}
    if imported:
        return list(imported.get("rule_sources", []))
    return list(DEFAULT_RULE_SOURCES)


def public_rule_sources(state: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for source in active_rule_sources(state):
        public = dict(source)
        if public.get("url"):
            public["url"] = mask_url(str(public["url"]))
        sources.append(public)
    return sources


class StateStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "state.json"
        self.lock = threading.RLock()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_STATE))
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            merged = json.loads(json.dumps(DEFAULT_STATE))
            merged.update(payload)
            return merged
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot load route-manager state: {exc}") from exc

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def save(self) -> None:
        with self.lock:
            temporary = self.path.with_suffix(".json.tmp")
            temporary.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)

    def mutate(self, callback: Any) -> Any:
        with self.lock:
            result = callback(self.state)
            self.save()
            return result

    def record_candidates(self, candidates: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
        timestamp = now_iso()
        added: list[dict[str, Any]] = []

        def update(state: dict[str, Any]) -> None:
            index = {entry["host"]: entry for entry in state["observations"]}
            for candidate in candidates:
                host = normalize_host(str(candidate.get("host", "")))
                if not host or is_private_host(host):
                    continue
                requested_match = str(candidate.get("match") or "domain")
                is_ip = False
                try:
                    ipaddress.ip_address(host)
                    is_ip = True
                except ValueError:
                    pass
                match = requested_match if requested_match in {"domain", "suffix", "ip", "ip-cidr"} else "domain"
                if is_ip and match in {"domain", "suffix"}:
                    match = "ip"
                if not is_ip and match in {"ip", "ip-cidr"}:
                    match = "domain"
                current = index.get(host)
                if current:
                    current["last_seen"] = timestamp
                    current["hits"] = int(current.get("hits", 0)) + 1
                    sources = set(current.get("sources", []))
                    sources.add(source)
                    current["sources"] = sorted(sources)[:8]
                    evidence = set(current.get("evidence", []))
                    evidence.update(str(item) for item in candidate.get("evidence", []) if str(item).strip())
                    current["evidence"] = sorted(evidence)[:12]
                    if current.get("status") == "pending" and current.get("match") in {None, "domain"}:
                        current["match"] = match
                    added.append(current.copy())
                    continue
                entry = {
                    "host": host,
                    "status": "pending",
                    "service_id": None,
                    "match": match,
                    "sources": [source],
                    "evidence": sorted(
                        {str(item) for item in candidate.get("evidence", []) if str(item).strip()}
                    )[:12],
                    "first_seen": timestamp,
                    "last_seen": timestamp,
                    "hits": 1,
                }
                state["observations"].append(entry)
                index[host] = entry
                added.append(entry.copy())
            state["observations"] = sorted(
                state["observations"], key=lambda item: item["last_seen"], reverse=True
            )[:5000]

        self.mutate(update)
        return added

    def record_observations(self, hosts: set[str], source: str) -> list[dict[str, Any]]:
        return self.record_candidates(
            [{"host": host, "match": "domain", "evidence": []} for host in sorted(hosts)],
            source,
        )


class MihomoClient:
    def __init__(self, base_url: str, secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {secret}"} if secret else {}

    async def _get(self, path: str) -> Any:
        async with httpx.AsyncClient(timeout=8, trust_env=False) as client:
            response = await client.get(f"{self.base_url}{path}", headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def version(self) -> dict[str, Any]:
        return await self._get("/version")

    async def config(self) -> dict[str, Any]:
        return await self._get("/configs")

    async def connections(self) -> dict[str, Any]:
        return await self._get("/connections")

    async def proxies(self) -> dict[str, Any]:
        return await self._get("/proxies")

    async def delay(
        self,
        proxy_name: str,
        url: str,
        timeout_ms: int = 5000,
        expected_status: int = 200,
    ) -> dict[str, Any]:
        query = urlencode({"url": url, "timeout": timeout_ms, "expected": expected_status})
        path = f"/proxies/{quote(proxy_name, safe='')}/delay?{query}"
        return await self._get(path)

    async def dns_query(self, host: str, query_type: str = "A") -> dict[str, Any]:
        query = urlencode({"name": host, "type": query_type})
        return await self._get(f"/dns/query?{query}")

    async def select(self, group_name: str, proxy_name: str) -> None:
        async with httpx.AsyncClient(timeout=10, trust_env=False) as client:
            response = await client.put(
                f"{self.base_url}/proxies/{quote(group_name, safe='')}",
                headers=self.headers,
                json={"name": proxy_name},
            )
            response.raise_for_status()

    async def apply(self, payload: str, path: str) -> None:
        async with httpx.AsyncClient(timeout=20, trust_env=False) as client:
            response = await client.put(
                f"{self.base_url}/configs?force=true",
                headers=self.headers,
                json={"path": path, "payload": payload},
            )
            response.raise_for_status()


class AppContext:
    def __init__(self, data_dir: Path, shared_dir: Path, client: MihomoClient) -> None:
        self.store = StateStore(data_dir)
        self.shared_dir = shared_dir
        self.shared_dir.mkdir(parents=True, exist_ok=True)
        self.client = client
        self.apply_mode = os.getenv("ROUTE_MANAGER_APPLY_MODE", "dry-run").lower()
        self.poll_seconds = max(2, int(os.getenv("ROUTE_MANAGER_CAPTURE_POLL_SECONDS", "5")))
        self.poll_task: asyncio.Task[None] | None = None
        self.analysis_tasks: dict[str, asyncio.Task[None]] = {}
        self.analysis_lock = asyncio.Lock()


def env_port(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 65535 else default


def advertised_host(request: Request) -> str:
    configured = os.getenv("PUBLIC_HOST", "").strip()
    if configured and configured not in {"0.0.0.0", "::", "localhost"}:
        return configured
    request_host = request.url.hostname
    if request_host and request_host not in {"0.0.0.0", "::", "localhost", "127.0.0.1"}:
        return request_host
    return "192.168.68.99"


def compile_config(state: dict[str, Any], shared_dir: Path) -> tuple[str, list[str]]:
    shared_dir.mkdir(parents=True, exist_ok=True)
    providers: dict[str, Any] = {}
    provider_groups: list[dict[str, Any]] = []
    provider_group_names: set[str] = set()
    enabled_provider_ids: list[str] = []
    warnings: list[str] = []

    for provider in state.get("providers", []):
        if not provider.get("enabled", True):
            continue
        provider_id = provider["id"]
        group_name = provider.get("group") or f"PROVIDER_{provider_id.upper()}"
        enabled_provider_ids.append(provider_id)
        provider_group_names.add(group_name)
        providers[provider_id] = {
            "type": "http",
            "url": provider["url"],
            "path": f"/shared/providers/{provider_id}.yaml",
            "interval": int(provider.get("interval", 86400)),
            "health-check": {
                "enable": True,
                "url": "https://www.gstatic.com/generate_204",
                "interval": 300,
            },
        }
        provider_groups.append({"name": group_name, "type": "select", "use": [provider_id]})

    if enabled_provider_ids:
        proxy_group: dict[str, Any] = {
            "name": "PROXY",
            "type": "select",
            "use": enabled_provider_ids,
        }
    else:
        proxy_group = {"name": "PROXY", "type": "select", "proxies": ["DIRECT"]}
        warnings.append("no enabled provider is configured; PROXY currently resolves to DIRECT")

    imported_policy = state.get("imported_policy") or {}
    imported_groups = imported_policy.get("groups", []) if isinstance(imported_policy, dict) else []
    imported_group_names = {
        str(item.get("name", "")).strip()
        for item in imported_groups
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    }
    legacy_proxy_names = set(imported_policy.get("legacy_proxy_names", [])) if isinstance(imported_policy, dict) else set()
    runtime_provider_group = sorted(provider_group_names)[0] if provider_group_names else None

    def map_policy_reference(value: str) -> str | None:
        reference = str(value).strip()
        if not reference:
            return None
        if reference.casefold() == "proxy":
            return "PROXY"
        if reference.upper() in {"DIRECT", "REJECT", "GLOBAL"}:
            return reference.upper()
        if reference in provider_group_names or reference in imported_group_names:
            return reference
        if reference in legacy_proxy_names:
            return runtime_provider_group
        return None

    service_groups: list[dict[str, Any]] = []
    custom_rules: list[list[str]] = []
    reserved_group_names = {"PROXY", "DIRECT", "REJECT", "GLOBAL"} | provider_group_names | imported_group_names
    reserved_group_names_upper = {name.upper() for name in reserved_group_names}
    used_service_group_names: set[str] = set()
    for service in state.get("services", []):
        service_id = service["id"]
        target = str(service.get("target") or "PROXY").strip()
        requested_name = str(service.get("group") or service.get("name") or service_id).strip()
        group_name = requested_name or f"SERVICE_{slug(service_id).upper()}"
        if group_name.upper() in reserved_group_names_upper or group_name in used_service_group_names:
            group_name = f"SERVICE_{slug(service_id).upper()}"
        used_service_group_names.add(group_name)

        # A service rule points to its own selectable strategy group. The
        # default target is listed first; the remaining choices stay visible
        # in MetaCubeXD so the user can switch without editing YAML.
        choices: list[str] = []
        for choice in [target, "PROXY", "DIRECT", "REJECT", *sorted(provider_group_names)]:
            if choice and choice not in choices:
                choices.append(choice)
        service_group: dict[str, Any] = {
            "name": group_name,
            "type": "select",
            "proxies": choices,
        }
        if enabled_provider_ids:
            service_group["use"] = enabled_provider_ids
        service_groups.append(service_group)

        if target not in {"DIRECT", "REJECT", "PROXY"} and target not in provider_group_names:
            warnings.append(f"service {service_id} targets {target}; verify this proxy exists")
        for domain in service.get("domains", []):
            host = str(domain["host"])
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None or domain.get("match") in {"ip", "ip-cidr"}:
                if address is None:
                    try:
                        network = ipaddress.ip_network(host, strict=False)
                    except ValueError:
                        warnings.append(f"invalid IP rule host for service {service_id}: {host}")
                        continue
                    cidr = str(network)
                    rule_kind = "IP-CIDR6" if network.version == 6 else "IP-CIDR"
                else:
                    cidr = f"{address}/{128 if address.version == 6 else 32}"
                    rule_kind = "IP-CIDR6" if address.version == 6 else "IP-CIDR"
                custom_rules.append([rule_kind, cidr, group_name, "no-resolve"])
            else:
                kind = "DOMAIN-SUFFIX" if domain.get("match") == "suffix" else "DOMAIN"
                custom_rules.append([kind, host, group_name])

    imported_proxy_groups: list[dict[str, Any]] = []
    for source_group in imported_groups:
        group_name = str(source_group.get("name", "")).strip()
        if not group_name:
            continue
        choices: list[str] = []
        for reference in source_group.get("proxies", []) or []:
            mapped = map_policy_reference(str(reference))
            if mapped and mapped not in choices:
                choices.append(mapped)
        if runtime_provider_group and runtime_provider_group not in choices:
            choices.append(runtime_provider_group)
        if not choices:
            choices.append("PROXY" if enabled_provider_ids else "DIRECT")
        group: dict[str, Any] = {
            "name": group_name,
            "type": str(source_group.get("type", "select")),
            "proxies": choices,
        }
        if enabled_provider_ids:
            group["use"] = enabled_provider_ids
        for field in (
            "url",
            "interval",
            "tolerance",
            "lazy",
            "expected-status",
            "strategy",
            "disable-udp",
            "interface-name",
            "routing-mark",
            "max-failed-times",
            "hidden",
            "icon",
        ):
            if field in source_group:
                group[field] = source_group[field]
        imported_proxy_groups.append(group)

    if imported_policy:
        rule_providers: dict[str, Any] = {}
        for index, item in enumerate(imported_policy.get("rule_sources", [])):
            source_id = str(item["id"])
            provider_config: dict[str, Any] = {
                "type": "http",
                "behavior": item["behavior"],
                "url": item["url"],
                "path": f"/shared/rules/imported-{index:03d}-{slug(source_id)}.yaml",
                "interval": int(item.get("interval", 86400)),
            }
            if item.get("format"):
                provider_config["format"] = item["format"]
            rule_providers[source_id] = provider_config
    else:
        rule_providers = {
            item["id"]: {
                "type": "http",
                "behavior": item["behavior"],
                "format": item["format"],
                "url": item["url"],
                "path": f"/shared/rules/{item['id']}.yaml",
                "interval": 86400,
            }
            for item in DEFAULT_RULE_SOURCES
        }

    custom_rules.sort(key=lambda item: (item[0] != "DOMAIN", -len(item[1]), item[1]))
    if imported_policy:
        rules: list[list[str]] = []
        rules.extend(custom_rules)
        for raw_rule in imported_policy.get("rules", []):
            parts = [part.strip() for part in raw_rule.split(",")]
            if len(parts) < 2:
                warnings.append(f"ignored malformed imported rule: {raw_rule}")
                continue
            rule_type = parts[0].upper()
            target_index = 1 if rule_type in {"MATCH", "FINAL"} else 2 if len(parts) >= 3 else len(parts) - 1
            target = parts[target_index]
            mapped_target = map_policy_reference(target)
            if mapped_target:
                parts[target_index] = mapped_target
            elif target in legacy_proxy_names and runtime_provider_group:
                parts[target_index] = runtime_provider_group
            elif target.casefold() not in {"direct", "reject"}:
                warnings.append(f"imported rule target {target} is not present; kept as-is")
            rules.append(parts)
    else:
        rules = [
            ["DOMAIN-SUFFIX", "localhost", "DIRECT"],
            ["IP-CIDR", "127.0.0.0/8", "DIRECT", "no-resolve"],
            ["IP-CIDR", "10.0.0.0/8", "DIRECT", "no-resolve"],
            ["IP-CIDR", "172.16.0.0/12", "DIRECT", "no-resolve"],
            ["IP-CIDR", "192.168.0.0/16", "DIRECT", "no-resolve"],
        ]
        rules.extend(custom_rules)
        rules.extend(
            [
                ["RULE-SET", "reject", "REJECT"],
                ["RULE-SET", "direct", "DIRECT"],
                ["RULE-SET", "global", "PROXY"],
                ["RULE-SET", "proxy", "PROXY"],
                ["RULE-SET", "applications", "DIRECT"],
                ["MATCH", "DIRECT"],
            ]
        )

    config = {
        "mixed-port": 7890,
        "allow-lan": True,
        "bind-address": "0.0.0.0",
        "mode": "rule",
        "log-level": "info",
        "ipv6": False,
        "external-controller": "0.0.0.0:9090",
        "secret": os.getenv("MIHOMO_SECRET", ""),
        "external-controller-cors": {
            "allow-private-network": True,
            "allow-origins": [
                "http://127.0.0.1:17880",
                "http://localhost:17880",
                "http://192.168.68.99:17880",
                "http://127.0.0.1:17881",
                "http://localhost:17881",
                "http://192.168.68.99:17881",
            ],
        },
        "profile": {"store-selected": True, "store-fake-ip": True},
        "dns": {
            "enable": True,
            "ipv6": False,
            "enhanced-mode": "fake-ip",
            "nameserver": ["223.5.5.5", "119.29.29.29"],
            "fallback": ["1.1.1.1", "8.8.8.8"],
        },
        "tun": {"enable": False},
        "proxy-providers": providers,
        "proxy-groups": provider_groups + imported_proxy_groups + [proxy_group] + service_groups,
        "rule-providers": rule_providers,
        "rules": [",".join(entry) for entry in rules],
    }
    if not state.get("services") and not imported_policy:
        warnings.append("no service route has been confirmed yet; unknown traffic remains DIRECT")
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False), warnings


def redact_rendered_config(rendered: str) -> str:
    config = yaml.safe_load(rendered) or {}
    if config.get("secret"):
        config["secret"] = "***"
    for provider in (config.get("proxy-providers") or {}).values():
        if provider.get("url"):
            provider["url"] = mask_url(str(provider["url"]))
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


async def apply_compiled_state(context: AppContext) -> dict[str, Any]:
    """Persist the manager-owned config and apply it only in controller mode."""
    payload = context.store.snapshot()
    rendered, warnings = compile_config(payload, context.shared_dir)
    rendered_dir = context.shared_dir / "rendered"
    rendered_dir.mkdir(parents=True, exist_ok=True)
    rendered_path = rendered_dir / "config.yaml"
    temporary = rendered_path.with_suffix(".yaml.tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(rendered_path)
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    result: dict[str, Any] = {
        "mode": context.apply_mode,
        "status": "draft",
        "sha256": digest,
        "path": str(rendered_path),
        "warnings": warnings,
    }
    if context.apply_mode == "controller":
        await context.client.apply(rendered, "/shared/rendered/config.yaml")
        result["status"] = "applied"
    context.store.mutate(
        lambda state: state.update(
            {"last_apply": {"status": result["status"], "sha256": digest, "at": now_iso()}}
        )
    )
    return result


def extract_connection_hosts(payload: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for connection in payload.get("connections", []):
        metadata = connection.get("metadata") or {}
        host = normalize_host(str(metadata.get("host", "")))
        if host and not is_private_host(host):
            result.add(host)
    return result


async def sync_connections(context: AppContext) -> dict[str, Any]:
    payload = await context.client.connections()
    hosts = extract_connection_hosts(payload)
    entries = context.store.record_observations(hosts, "mihomo")
    return {"connections": len(payload.get("connections", [])), "hosts": len(hosts), "entries": entries}


def candidate_for_host(host: str, evidence: str) -> dict[str, Any] | None:
    normalized = normalize_host(host)
    if not normalized or is_private_host(normalized):
        return None
    try:
        ipaddress.ip_address(normalized)
        match = "ip"
    except ValueError:
        match = "domain"
    return {"host": normalized, "match": match, "evidence": [evidence]}


def extract_connection_candidates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for connection in payload.get("connections", []):
        metadata = connection.get("metadata") or {}
        for field in ("host", "destinationIP", "remoteDestination", "destination"):
            raw = str(metadata.get(field) or "").strip()
            if not raw:
                continue
            candidate = candidate_for_host(raw, f"mihomo:{field}")
            if candidate:
                candidates.append(candidate)
    return candidates


def extract_dns_candidates(payload: dict[str, Any], queried_host: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for answer in payload.get("Answer", []) or payload.get("answer", []) or []:
        if not isinstance(answer, dict):
            continue
        raw = str(answer.get("data") or "").strip().rstrip(".")
        candidate = candidate_for_host(raw, f"dns:{queried_host}")
        if candidate:
            candidates.append(candidate)
    return candidates


def extract_text_candidates(text: str, base_url: str, evidence: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for host in sorted(extract_hosts(text, base_url)):
        candidate = candidate_for_host(host, evidence)
        if candidate:
            candidates.append(candidate)
    return candidates


def proxy_nodes(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Return leaf proxies only; selector and URL-test groups are not nodes."""
    result: list[dict[str, str]] = []
    for name, value in (payload.get("proxies") or {}).items():
        if not isinstance(value, dict):
            continue
        proxy_type = str(value.get("type") or "").strip()
        if proxy_type.upper() in PROXY_GROUP_TYPES or proxy_type.upper() in NON_NODE_TYPES:
            continue
        if str(name).upper() in NON_NODE_TYPES:
            continue
        result.append({"name": str(name), "type": proxy_type or "unknown"})
    return result


def select_probe_group(payload: dict[str, Any], proxy_name: str) -> tuple[str, str] | None:
    if proxy_name == "PROXY":
        proxy_group = (payload.get("proxies") or {}).get("PROXY") or {}
        if str(proxy_group.get("type") or "").upper() in PROXY_GROUP_TYPES:
            current = str(proxy_group.get("now") or "").strip()
            if current:
                return ("PROXY", current)
    groups: list[tuple[str, dict[str, Any]]] = []
    for name, value in (payload.get("proxies") or {}).items():
        if not isinstance(value, dict):
            continue
        if str(value.get("type") or "").upper() not in PROXY_GROUP_TYPES:
            continue
        if proxy_name in (value.get("all") or []):
            groups.append((str(name), value))
    if not groups:
        return None
    preferred = {"PROXY": 0, "GLOBAL": 1, "CLASHVPN": 2}
    groups.sort(key=lambda item: (preferred.get(item[0].upper(), 3), item[0]))
    group_name, group = groups[0]
    current = str(group.get("now") or "").strip()
    return (group_name, current) if current else None


def analysis_proxy_url() -> str:
    return os.getenv("DISCOVERY_HTTP_PROXY", "").strip() or "http://metacubexd:7890"


def httpx_proxy_kwargs(proxy_url: str) -> dict[str, Any]:
    if not proxy_url:
        return {}
    if "proxy" in inspect.signature(httpx.AsyncClient).parameters:
        return {"proxy": proxy_url}
    return {"proxies": proxy_url}


async def fetch_discovery_content(url: str, proxy_url: str) -> dict[str, Any]:
    """Fetch the target and a bounded set of static resources through Mihomo."""
    kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "timeout": 20,
        "trust_env": False,
        "headers": {"User-Agent": "crypto-proxy-route-manager/0.2"},
        **httpx_proxy_kwargs(proxy_url),
    }
    async with httpx.AsyncClient(**kwargs) as client:
        response = await client.get(url)
        if response.status_code >= 500:
            response.raise_for_status()
        body = response.content[:2_000_000].decode("utf-8", errors="replace")
        final_url = str(response.url)
        redirect_urls = [str(item.url) for item in response.history]
        resources: list[dict[str, str]] = []
        resource_urls = sorted(extract_resource_urls(body, final_url))
        for resource_url in resource_urls[:24]:
            try:
                resource_response = await client.get(resource_url)
                if resource_response.status_code >= 500:
                    continue
                resource_body = resource_response.content[:700_000].decode("utf-8", errors="replace")
                content_type = resource_response.headers.get("content-type", "")
                if "text" not in content_type and "javascript" not in content_type and "json" not in content_type:
                    parsed_path = urlparse(resource_url).path.lower()
                    if not parsed_path.endswith((".js", ".css", ".json", ".map")):
                        continue
                resources.append({"url": resource_url, "body": resource_body})
            except httpx.HTTPError:
                continue
    return {
        "final_url": final_url,
        "status_code": response.status_code,
        "body": body,
        "redirects": redirect_urls,
        "resources": resources,
    }


def analysis_snapshot(context: AppContext, analysis_id: str) -> dict[str, Any] | None:
    for item in context.store.snapshot().get("analyses", []):
        if item.get("id") == analysis_id:
            return item
    return None


def public_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
    result = json.loads(json.dumps(analysis))
    result["nodes"] = [
        item
        for item in result.get("nodes", [])
        if str(item.get("type", "")).upper() not in {"PASSRULE", "REJECTDROP"}
        and str(item.get("name", "")).upper() not in {"PASS-RULE", "REJECT-DROP"}
    ]
    result["current_index"] = min(int(result.get("current_index", 0)), len(result["nodes"]))
    return result


def update_analysis(context: AppContext, analysis_id: str, callback: Any) -> dict[str, Any] | None:
    found: dict[str, Any] | None = None

    def update(state: dict[str, Any]) -> None:
        nonlocal found
        for item in state.setdefault("analyses", []):
            if item.get("id") == analysis_id:
                callback(item)
                item["updated_at"] = now_iso()
                found = json.loads(json.dumps(item))
                break

    context.store.mutate(update)
    return found


def score_delay(delay_ms: int) -> int:
    return max(1, 100_000 - max(0, delay_ms))


def analysis_node_order(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Put the currently selected PROXY leaf first, then preserve Mihomo order."""
    nodes = proxy_nodes(payload)
    selection = select_probe_group(payload, "PROXY")
    current = selection[1] if selection else ""
    if current:
        nodes.sort(key=lambda item: (item["name"] != current,))
    return nodes


def upsert_analysis_service(context: AppContext, analysis_id: str, target: str) -> dict[str, Any] | None:
    analysis = analysis_snapshot(context, analysis_id)
    if not analysis or not analysis.get("service_id"):
        return None
    service_id = str(analysis["service_id"])
    target_host = str(analysis.get("target_host") or "").strip()
    service_name = str(analysis.get("service_name") or f"{target_host} 服务").strip()
    source = f"analysis:{analysis_id}:target"
    try:
        ipaddress.ip_address(target_host)
        target_match = "ip"
    except ValueError:
        target_match = "domain"

    def update(state: dict[str, Any]) -> None:
        service = next((item for item in state["services"] if item["id"] == service_id), None)
        if service is None:
            service = {
                "id": service_id,
                "name": service_name,
                "scope": "discovered",
                "target": target,
                "analysis_id": analysis_id,
                "domains": [],
            }
            state["services"].append(service)
        else:
            service.update({
                "name": service_name,
                "target": target,
                "analysis_id": analysis_id,
            })
        if target_host and not any(item.get("host") == target_host for item in service["domains"]):
            service["domains"].append({
                "host": target_host,
                "match": target_match,
                "source": source,
                "added_at": now_iso(),
            })
        for item in state.get("analyses", []):
            if item.get("id") == analysis_id:
                item["service_target"] = target

    context.store.mutate(update)
    return next(
        (item for item in context.store.snapshot().get("services", []) if item.get("id") == service_id),
        None,
    )


async def run_node_analysis(context: AppContext, analysis_id: str) -> None:
    job = analysis_snapshot(context, analysis_id)
    if not job:
        return
    target_url = str(job["url"])
    expected_status = int(job.get("expected_status", 200))
    timeout_ms = int(job.get("timeout_ms", 5000))
    health_url = os.getenv("NODE_HEALTH_URL", "https://www.gstatic.com/generate_204")
    try:
        nodes = list(job.get("nodes", []))
        mode = str(job.get("selection_mode") or "legacy")
        tested_count = 0

        async def test_node(index: int, node: dict[str, Any]) -> None:
            nonlocal tested_count
            update_analysis(
                context,
                analysis_id,
                lambda item, index=index, tested_count=tested_count: item.update({
                    "current_index": tested_count + 1,
                    "phase": "node-test",
                    "nodes": [
                        dict(result, status="testing") if row_index == index else result
                        for row_index, result in enumerate(item.get("nodes", []))
                    ],
                }),
            )
            result = dict(node)
            result.update({"status": "failed", "ok": False, "tested_at": now_iso()})
            try:
                if str(node["name"]).upper() == "DIRECT":
                    result.update({"status": "success", "ok": True, "response_check": "手动选择 DIRECT"})
                else:
                    payload = await context.client.delay(
                        node["name"], target_url, timeout_ms=timeout_ms, expected_status=expected_status
                    )
                    delay_ms = int(payload.get("delay", 0))
                    result.update({
                        "status": "success",
                        "ok": delay_ms > 0,
                        "delay_ms": delay_ms,
                        "score": score_delay(delay_ms),
                        "response_check": f"HTTP {expected_status}",
                    })
                    if delay_ms <= 0:
                        result["error"] = "Mihomo returned no positive delay"
            except Exception as target_error:
                result["error"] = str(target_error)
                if mode == "manual":
                    result["status"] = "unreachable"
                else:
                    try:
                        await context.client.delay(node["name"], health_url, timeout_ms=timeout_ms, expected_status=204)
                        result["baseline_ok"] = True
                        result["status"] = "target-failed-node-reachable"
                        result["error"] = f"target check failed; health probe passed: {target_error}"
                    except Exception as health_error:
                        result["status"] = "unreachable"
                        result["error"] = f"target and health probes failed: {target_error}; {health_error}"
            update_analysis(
                context,
                analysis_id,
                lambda item, index=index, result=result: item["nodes"].__setitem__(index, result),
            )
            tested_count += 1

        if mode == "manual":
            await test_node(0, nodes[0])
        elif mode == "auto":
            initial_limit = max(1, min(len(nodes), int(os.getenv("ANALYSIS_AUTO_INITIAL_NODES", "6"))))
            max_successes = max(1, int(os.getenv("ANALYSIS_AUTO_SUCCESS_TARGET", "2")))
            for index in range(initial_limit):
                await test_node(index, nodes[index])
                successful = sum(1 for item in (analysis_snapshot(context, analysis_id) or {}).get("nodes", []) if item.get("ok"))
                if successful >= max_successes:
                    break
            successful = sum(1 for item in (analysis_snapshot(context, analysis_id) or {}).get("nodes", []) if item.get("ok"))
            if successful < max_successes:
                for index in range(initial_limit, len(nodes)):
                    await test_node(index, nodes[index])
                    successful = sum(1 for item in (analysis_snapshot(context, analysis_id) or {}).get("nodes", []) if item.get("ok"))
                    if successful >= max_successes:
                        break
        else:
            for index, node in enumerate(nodes):
                await test_node(index, node)

        finished = analysis_snapshot(context, analysis_id) or {}
        successful = [item for item in finished.get("nodes", []) if item.get("ok")]
        successful.sort(key=lambda item: (int(item.get("delay_ms", 10**9)), -int(item.get("score", 0))))
        recommendation = None
        if successful:
            best = successful[0]
            recommendation = {
                "proxy": best["name"],
                "type": best.get("type"),
                "delay_ms": best.get("delay_ms"),
                "score": best.get("score"),
                "reason": "目标网址返回预期 HTTP 状态，且延迟最低",
            }
        update_analysis(
            context,
            analysis_id,
            lambda item: item.update({
                "status": "ready" if recommendation else "failed",
                "phase": "recommendation" if recommendation else "node-test",
                "current_index": tested_count,
                "recommendation": recommendation,
                "error": None if recommendation else "没有节点通过目标网址测试；可检查目标状态码或订阅节点",
            }),
        )
        if recommendation and mode in {"auto", "manual"}:
            target = str(recommendation.get("proxy") or "PROXY")
            upsert_analysis_service(context, analysis_id, target)
            try:
                await apply_compiled_state(context)
            except Exception as exc:
                LOGGER.exception("failed to apply service route for analysis %s", analysis_id)
                update_analysis(
                    context,
                    analysis_id,
                    lambda item, exc=exc: item.update({
                        "status": "failed",
                        "phase": "apply",
                        "error": f"已选出口，但配置应用失败: {exc}",
                    }),
                )
                return
            await run_content_probe(context, analysis_id, target)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        LOGGER.exception("analysis %s failed", analysis_id)
        update_analysis(
            context,
            analysis_id,
            lambda item: item.update({"status": "failed", "phase": "node-test", "error": str(exc)}),
        )


async def run_content_probe(context: AppContext, analysis_id: str, proxy_name: str) -> None:
    async with context.analysis_lock:
        job = analysis_snapshot(context, analysis_id)
        if not job:
            return
        update_analysis(
            context,
            analysis_id,
            lambda item: item.update({
                "status": "probing",
                "phase": "content-probe",
                "probe_proxy": proxy_name,
                "probe_started_at": now_iso(),
                "error": None,
            }),
        )
        switched = False
        group_name = ""
        previous_proxy = ""
        restore_error: str | None = None
        try:
            proxies_payload = await context.client.proxies()
            if proxy_name.upper() == "DIRECT":
                selection = ("", "")
            else:
                selection = select_probe_group(proxies_payload, proxy_name)
                if not selection:
                    target_proxy = (proxies_payload.get("proxies") or {}).get(proxy_name) or {}
                    if str(target_proxy.get("type") or "").upper() in PROXY_GROUP_TYPES:
                        selection = (proxy_name, str(target_proxy.get("now") or ""))
            if not selection:
                raise RuntimeError("没有找到包含推荐节点的可切换策略组")
            group_name, previous_proxy = selection
            if group_name and previous_proxy != proxy_name and group_name != proxy_name:
                await context.client.select(group_name, proxy_name)
                switched = True

            fetched = await fetch_discovery_content(job["url"], analysis_proxy_url())
            candidates: dict[str, dict[str, Any]] = {}

            def add_candidate(candidate: dict[str, Any] | None) -> None:
                if not candidate:
                    return
                host = candidate["host"]
                current = candidates.get(host)
                if not current:
                    candidates[host] = dict(candidate)
                    return
                current["evidence"] = sorted(
                    set(current.get("evidence", [])) | set(candidate.get("evidence", []))
                )[:12]

            final_url = str(fetched.get("final_url") or job["url"])
            for candidate in extract_text_candidates(fetched.get("body", ""), final_url, "入口 HTML"):
                add_candidate(candidate)
            for redirect_url in fetched.get("redirects", []) or []:
                add_candidate(candidate_for_host(urlparse(redirect_url).hostname or "", "重定向"))
            for resource in fetched.get("resources", []) or []:
                resource_url = str(resource.get("url") or "")
                for candidate in extract_text_candidates(
                    str(resource.get("body") or ""), resource_url, f"静态资源 {urlparse(resource_url).path or '/'}"
                ):
                    add_candidate(candidate)
                add_candidate(candidate_for_host(urlparse(resource_url).hostname or "", "静态资源"))
            add_candidate(candidate_for_host(urlparse(final_url).hostname or "", "最终网址"))

            connections_payload = await context.client.connections()
            for candidate in extract_connection_candidates(connections_payload):
                add_candidate(candidate)

            domain_hosts = [host for host, item in candidates.items() if item.get("match") == "domain"]
            dns_answers = 0
            for host in domain_hosts[:80]:
                for query_type in ("A", "AAAA", "CNAME"):
                    try:
                        dns_payload = await context.client.dns_query(host, query_type)
                        dns_candidates = extract_dns_candidates(dns_payload, host)
                        dns_answers += len(dns_candidates)
                        for candidate in dns_candidates:
                            add_candidate(candidate)
                    except Exception as exc:
                        LOGGER.debug("DNS discovery failed for %s/%s: %s", host, query_type, exc)

            entries = context.store.record_candidates(list(candidates.values()), f"analysis:{analysis_id}")
            entry_index = {entry["host"]: entry for entry in entries}
            stored_candidates = []
            for host in sorted(candidates):
                candidate = dict(candidates[host])
                stored = entry_index.get(host) or next(
                    (item for item in context.store.snapshot().get("observations", []) if item.get("host") == host),
                    {},
                )
                candidate.update({
                    "status": stored.get("status", "pending"),
                    "match": stored.get("match", candidate.get("match", "domain")),
                    "hits": stored.get("hits", 1),
                })
                stored_candidates.append(candidate)
            update_analysis(
                context,
                analysis_id,
                lambda item: item.update({
                    "status": "completed",
                    "phase": "candidate-review",
                    "final_url": final_url,
                    "http_status": fetched.get("status_code"),
                    "resource_count": len(fetched.get("resources", []) or []),
                    "connection_count": len(connections_payload.get("connections", [])),
                    "dns_answer_count": dns_answers,
                    "candidates": stored_candidates,
                    "candidate_count": len(stored_candidates),
                    "probe_completed_at": now_iso(),
                }),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.exception("content probe %s failed", analysis_id)
            update_analysis(
                context,
                analysis_id,
                lambda item: item.update({"status": "failed", "phase": "content-probe", "error": str(exc)}),
            )
        finally:
            if switched:
                try:
                    await context.client.select(group_name, previous_proxy)
                except Exception as exc:
                    restore_error = str(exc)
                    LOGGER.exception("failed to restore Mihomo selection for analysis %s", analysis_id)
            if restore_error:
                update_analysis(
                    context,
                    analysis_id,
                    lambda item: item.update({
                        "status": "restore-failed",
                        "error": f"探测完成但无法恢复 {group_name} 原选择 {previous_proxy}: {restore_error}",
                        "restore_error": restore_error,
                    }),
                )


async def discover_url(context: AppContext, url: str) -> dict[str, Any]:
    normalized_url = normalize_http_url(url)
    parsed = urlparse(normalized_url)
    if is_private_host(parsed.hostname or ""):
        raise ValueError("private and local discovery targets are disabled")
    kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "timeout": 15,
        "trust_env": False,
        "headers": {"User-Agent": "crypto-proxy-route-manager/0.1"},
    }
    discovery_proxy = os.getenv("DISCOVERY_HTTP_PROXY", "").strip()
    if discovery_proxy:
        if "proxy" in inspect.signature(httpx.AsyncClient).parameters:
            kwargs["proxy"] = discovery_proxy
        else:
            kwargs["proxies"] = discovery_proxy
    async with httpx.AsyncClient(**kwargs) as client:
        response = await client.get(normalized_url)
        response.raise_for_status()
        body = response.content[:2_000_000].decode("utf-8", errors="replace")
        final_url = str(response.url)
    hosts = extract_hosts(body, final_url)
    final_host = normalize_host(urlparse(final_url).hostname or "")
    if final_host and not is_private_host(final_host):
        hosts.add(final_host)
    entries = context.store.record_observations(hosts, "url-fetch")
    return {"url": normalized_url, "final_url": final_url, "hosts": sorted(hosts), "entries": entries}


async def poll_capture(context: AppContext) -> None:
    while True:
        try:
            if context.store.snapshot().get("capture_enabled"):
                await sync_connections(context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.debug("capture poll failed: %s", exc)
        await asyncio.sleep(context.poll_seconds)


def create_app(
    data_dir: Path | None = None,
    shared_dir: Path | None = None,
    client: MihomoClient | None = None,
) -> FastAPI:
    resolved_data_dir = data_dir or Path(os.getenv("ROUTE_MANAGER_DATA_DIR", "./data/route-manager"))
    resolved_shared_dir = shared_dir or Path(os.getenv("ROUTE_MANAGER_SHARED_DIR", "./data/shared"))
    resolved_client = client or MihomoClient(
        os.getenv("MIHOMO_CONTROLLER_URL", "http://127.0.0.1:17990"),
        os.getenv("MIHOMO_SECRET", ""),
    )
    context = AppContext(resolved_data_dir, resolved_shared_dir, resolved_client)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        context.poll_task = asyncio.create_task(poll_capture(context))
        yield
        if context.poll_task:
            context.poll_task.cancel()
            await asyncio.gather(context.poll_task, return_exceptions=True)
        tasks = list(context.analysis_tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title="Crypto Proxy Route Manager", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        return Response(status_code=204)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "apply_mode": context.apply_mode, "capture": context.store.snapshot()["capture_enabled"]}

    @app.get("/api/access")
    async def access(request: Request) -> dict[str, Any]:
        version: dict[str, Any] = {}
        runtime_config: dict[str, Any] = {}
        errors: list[str] = []
        try:
            version = await context.client.version()
        except Exception as exc:
            errors.append(f"core version unavailable: {exc}")
        try:
            runtime_config = await context.client.config()
        except Exception as exc:
            errors.append(f"core config unavailable: {exc}")

        bind_address = str(runtime_config.get("bind-address") or "").strip()
        allow_lan_value = runtime_config.get("allow-lan")
        allow_lan = allow_lan_value is True or str(allow_lan_value).lower() == "true"
        local_only = bind_address.casefold() in {"127.0.0.1", "::1", "localhost"}
        lan_exposed = bool(runtime_config) and allow_lan and bool(bind_address) and not local_only
        core_connected = bool(version)
        status = "ready" if core_connected and lan_exposed else "degraded" if core_connected else "offline"
        host = advertised_host(request)
        urls = {
            "http_proxy": f"http://{host}:{env_port('PUBLIC_PROXY_PORT', 17890)}",
            "socks5_proxy": f"socks5://{host}:{env_port('PUBLIC_PROXY_PORT', 17890)}",
            "route_manager": f"http://{host}:{env_port('PUBLIC_MANAGER_PORT', 17881)}",
            "metacubexd": f"http://{host}:{env_port('PUBLIC_DASHBOARD_PORT', 17880)}",
            "clash_api": f"http://{host}:{env_port('PUBLIC_API_PORT', 17990)}",
        }
        return {
            "status": status,
            "host": host,
            "lan_exposed": lan_exposed,
            "urls": urls,
            "runtime": {
                "version": version.get("version"),
                "mode": runtime_config.get("mode"),
                "allow_lan": allow_lan,
                "bind_address": bind_address or None,
                "mixed_port": runtime_config.get("mixed-port"),
                "config_verified": bool(runtime_config),
            },
            "errors": errors,
        }

    @app.get("/api/state")
    async def state() -> dict[str, Any]:
        payload = context.store.snapshot()
        providers = []
        for provider in payload["providers"]:
            public = dict(provider)
            public["url"] = mask_url(public["url"])
            providers.append(public)
        payload["providers"] = providers
        imported = payload.get("imported_policy") or {}
        if imported:
            payload["imported_policy"] = {
                "source_name": imported.get("source_name"),
                "imported_at": imported.get("imported_at"),
                "proxy_group_count": len(imported.get("groups", [])),
                "rule_source_count": len(imported.get("rule_sources", [])),
                "rule_count": len(imported.get("rules", [])),
                "ignored_proxy_count": int(imported.get("ignored_proxy_count", 0)),
            }
        payload["rule_sources"] = public_rule_sources(context.store.snapshot())
        return payload

    @app.get("/api/catalog")
    async def catalog() -> list[dict[str, Any]]:
        return SERVICE_CATALOG

    @app.post("/api/import/policy")
    async def import_policy(request: PolicyImportRequest) -> dict[str, Any]:
        try:
            document = yaml.safe_load(request.yaml_text) or {}
            if not isinstance(document, dict):
                raise ValueError("the imported YAML root must be a mapping")
            imported = normalize_imported_policy(document, request.name)
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid policy YAML: {exc}") from exc

        context.store.mutate(lambda state: state.update({"imported_policy": imported, "rule_checks": {}}))
        return {
            "status": "imported",
            "source_name": imported["source_name"],
            "proxy_group_count": len(imported["groups"]),
            "rule_source_count": len(imported["rule_sources"]),
            "rule_count": len(imported["rules"]),
            "ignored_proxy_count": imported["ignored_proxy_count"],
            "requires_apply": True,
        }

    @app.get("/api/core")
    async def core() -> dict[str, Any]:
        try:
            return {"status": "connected", "version": await context.client.version()}
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}

    @app.get("/api/proxies")
    async def proxies() -> dict[str, Any]:
        try:
            payload = await context.client.proxies()
            result = []
            for name, proxy in (payload.get("proxies") or {}).items():
                result.append(
                    {
                        "name": name,
                        "type": proxy.get("type"),
                        "now": proxy.get("now"),
                        "all": proxy.get("all", [])[:100],
                    }
                )
            return {"status": "connected", "proxies": result}
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc), "proxies": []}

    @app.post("/api/capture")
    async def capture(request: CaptureRequest) -> dict[str, Any]:
        context.store.mutate(lambda state: state.update({"capture_enabled": request.enabled}))
        return {"capture_enabled": request.enabled}

    @app.post("/api/capture/sync")
    async def capture_sync() -> dict[str, Any]:
        try:
            return await sync_connections(context)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Mihomo connections unavailable: {exc}") from exc

    @app.get("/api/analysis/latest")
    async def latest_analysis() -> dict[str, Any]:
        analyses = context.store.snapshot().get("analyses", [])
        return {"analysis": public_analysis(analyses[0]) if analyses else None}

    @app.get("/api/analysis/{analysis_id}")
    async def get_analysis(analysis_id: str) -> dict[str, Any]:
        analysis = analysis_snapshot(context, analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="analysis not found")
        return public_analysis(analysis)

    @app.post("/api/analysis/start")
    async def start_analysis(request: AnalysisStartRequest) -> dict[str, Any]:
        try:
            normalized_url = normalize_http_url(request.url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if is_private_host(urlparse(normalized_url).hostname or ""):
            raise HTTPException(status_code=400, detail="private and local analysis targets are disabled")
        try:
            proxies_payload = await context.client.proxies()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Mihomo proxies unavailable: {exc}") from exc
        mode = request.selection_mode or "legacy"
        available_nodes: list[dict[str, str]]
        if mode == "manual":
            manual_target = str(request.manual_target or "").strip()
            available = proxies_payload.get("proxies") or {}
            if not manual_target or manual_target not in available:
                raise HTTPException(status_code=400, detail="请选择当前 Mihomo 中存在的节点或策略组")
            selected = available.get(manual_target) or {}
            available_nodes = [{
                "name": manual_target,
                "type": str(selected.get("type") or "manual"),
            }]
        else:
            available_nodes = analysis_node_order(proxies_payload)
            if mode == "auto":
                max_nodes = max(1, min(12, int(os.getenv("ANALYSIS_AUTO_MAX_NODES", "12"))))
            else:
                max_nodes = max(1, min(100, int(os.getenv("ANALYSIS_MAX_NODES", "40"))))
            available_nodes = available_nodes[:max_nodes]
        if not available_nodes:
            raise HTTPException(status_code=409, detail="当前 Mihomo 没有可测试的订阅节点")
        analysis_id = uuid.uuid4().hex[:12]
        timestamp = now_iso()
        target_host = normalize_host(urlparse(normalized_url).hostname or "")
        service_id = slug(target_host or "discovered-service", "discovered-service") if mode in {"auto", "manual"} else None
        job = {
            "id": analysis_id,
            "url": normalized_url,
            "target_host": target_host,
            "service_id": service_id,
            "service_name": (request.service_name or "").strip() or (f"{target_host} 服务" if target_host else "网址服务"),
            "selection_mode": mode,
            "manual_target": request.manual_target if mode == "manual" else None,
            "auto_probe": mode in {"auto", "manual"},
            "expected_status": request.expected_status,
            "timeout_ms": 5000,
            "status": "testing",
            "phase": "node-test",
            "created_at": timestamp,
            "updated_at": timestamp,
            "current_index": 0,
            "nodes": [
                {"name": item["name"], "type": item["type"], "status": "queued", "ok": False}
                for item in available_nodes
            ],
            "recommendation": None,
            "candidates": [],
        }
        context.store.mutate(
            lambda state: state.update(
                {"analyses": [job, *state.get("analyses", [])][:10]}
            )
        )
        if mode in {"auto", "manual"}:
            initial_target = str(request.manual_target or "PROXY") if mode == "manual" else "PROXY"
            upsert_analysis_service(context, analysis_id, initial_target)
        task = asyncio.create_task(run_node_analysis(context, analysis_id))
        context.analysis_tasks[analysis_id] = task

        def cleanup(_: asyncio.Task[None], analysis_id: str = analysis_id) -> None:
            context.analysis_tasks.pop(analysis_id, None)

        task.add_done_callback(cleanup)
        return job

    @app.post("/api/analysis/{analysis_id}/probe")
    async def probe_analysis(analysis_id: str, request: AnalysisProbeRequest) -> dict[str, Any]:
        analysis = analysis_snapshot(context, analysis_id)
        if not analysis:
            raise HTTPException(status_code=404, detail="analysis not found")
        if analysis.get("status") not in {"ready", "completed"}:
            raise HTTPException(status_code=409, detail="节点测试尚未完成或没有推荐节点")
        recommendation = analysis.get("recommendation") or {}
        proxy_name = request.proxy or recommendation.get("proxy")
        if not proxy_name:
            raise HTTPException(status_code=409, detail="没有可用的推荐节点")
        if not any(item.get("name") == proxy_name and item.get("ok") for item in analysis.get("nodes", [])):
            raise HTTPException(status_code=400, detail="只能使用本次测试成功的节点进行探测")
        running = context.analysis_tasks.get(analysis_id)
        if running and not running.done():
            raise HTTPException(status_code=409, detail="该分析任务仍在运行")
        task = asyncio.create_task(run_content_probe(context, analysis_id, proxy_name))
        context.analysis_tasks[analysis_id] = task

        def cleanup(_: asyncio.Task[None], analysis_id: str = analysis_id) -> None:
            context.analysis_tasks.pop(analysis_id, None)

        task.add_done_callback(cleanup)
        current = analysis_snapshot(context, analysis_id) or analysis
        return public_analysis(current)

    @app.post("/api/analysis/{analysis_id}/draft")
    async def draft_analysis(analysis_id: str, request: AnalysisDraftRequest) -> dict[str, Any]:
        analysis = analysis_snapshot(context, analysis_id)
        if not analysis or analysis.get("status") != "completed":
            raise HTTPException(status_code=409, detail="请先完成节点测试和网址探测")
        if not request.candidates:
            raise HTTPException(status_code=400, detail="至少选择一个候选域名或 IP")
        discovered = {item.get("host"): item for item in analysis.get("candidates", [])}
        selected: list[dict[str, Any]] = []
        for item in request.candidates:
            normalized = normalize_host(item.host)
            if not normalized or normalized not in discovered or is_private_host(normalized):
                raise HTTPException(status_code=400, detail=f"候选不属于本次探测结果: {item.host}")
            selected.append({"host": normalized, "match": item.match})
        target_host = analysis.get("target_host") or "discovered-service"
        service_id = slug(request.service_id or target_host, "discovered-service")
        service_name = request.service_name or f"{target_host} 引导策略"
        target = request.target or (analysis.get("recommendation") or {}).get("proxy") or "PROXY"
        source = f"analysis:{analysis_id}"

        def update(state: dict[str, Any]) -> None:
            service = next((item for item in state["services"] if item["id"] == service_id), None)
            if service is None:
                service = {
                    "id": service_id,
                    "name": service_name,
                    "scope": "discovered",
                    "target": target,
                    "domains": [],
                }
                state["services"].append(service)
            else:
                service.update({"name": service_name, "target": target})
            for item in selected:
                domain = {
                    "host": item["host"],
                    "match": item["match"],
                    "source": source,
                    "added_at": now_iso(),
                }
                existing = next((entry for entry in service["domains"] if entry["host"] == item["host"]), None)
                if existing:
                    existing.update(domain)
                else:
                    service["domains"].append(domain)
                for observation in state.get("observations", []):
                    if observation.get("host") == item["host"]:
                        observation.update({"status": "accepted", "service_id": service_id, "match": item["match"]})
            for stored in state.get("analyses", []):
                if stored.get("id") == analysis_id:
                    selected_hosts = {item["host"] for item in selected}
                    for candidate in stored.get("candidates", []):
                        if candidate.get("host") in selected_hosts:
                            candidate.update({"status": "accepted"})
                    stored.update({
                        "drafted_at": now_iso(),
                        "draft_service_id": service_id,
                        "draft_hosts": [item["host"] for item in selected],
                    })

        context.store.mutate(update)
        result_state = context.store.snapshot()
        service = next(item for item in result_state["services"] if item["id"] == service_id)
        return {"status": "drafted", "service": service, "added": selected, "requires_apply": True}

    @app.post("/api/discover/url")
    async def discover(request: DiscoverRequest) -> dict[str, Any]:
        try:
            return await discover_url(context, request.url)
        except (ValueError, httpx.HTTPError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/providers")
    async def add_provider(request: ProviderRequest) -> dict[str, Any]:
        provider = request.model_dump()
        provider["enabled"] = True

        def update(state: dict[str, Any]) -> None:
            state["providers"] = [item for item in state["providers"] if item["id"] != provider["id"]]
            state["providers"].append(provider)

        context.store.mutate(update)
        public = dict(provider)
        public["url"] = mask_url(public["url"])
        return public

    @app.delete("/api/providers/{provider_id}")
    async def delete_provider(provider_id: str) -> dict[str, Any]:
        removed = False

        def update(state: dict[str, Any]) -> None:
            nonlocal removed
            before = len(state["providers"])
            state["providers"] = [item for item in state["providers"] if item["id"] != provider_id]
            removed = len(state["providers"]) != before

        context.store.mutate(update)
        if not removed:
            raise HTTPException(status_code=404, detail="provider not found")
        return {"deleted": provider_id}

    @app.post("/api/services")
    async def add_service(request: ServiceRequest) -> dict[str, Any]:
        service = request.model_dump()
        service["domains"] = []

        def update(state: dict[str, Any]) -> None:
            existing = next((item for item in state["services"] if item["id"] == service["id"]), None)
            if existing:
                existing.update({"name": service["name"], "scope": service["scope"], "target": service["target"]})
            else:
                state["services"].append(service)

        context.store.mutate(update)
        return next(item for item in context.store.snapshot()["services"] if item["id"] == service["id"])

    @app.delete("/api/services/{service_id}")
    async def delete_service(service_id: str) -> dict[str, Any]:
        removed = False

        def update(state: dict[str, Any]) -> None:
            nonlocal removed
            before = len(state["services"])
            state["services"] = [item for item in state["services"] if item["id"] != service_id]
            removed = len(state["services"]) != before

        context.store.mutate(update)
        if not removed:
            raise HTTPException(status_code=404, detail="service not found")
        return {"deleted": service_id}

    @app.post("/api/services/{service_id}/domains")
    async def add_domain(service_id: str, request: DomainRequest) -> dict[str, Any]:
        domain = request.model_dump()
        domain["added_at"] = now_iso()
        found = False

        def update(state: dict[str, Any]) -> None:
            nonlocal found
            service = next((item for item in state["services"] if item["id"] == service_id), None)
            if not service:
                return
            found = True
            if not any(item["host"] == domain["host"] for item in service["domains"]):
                service["domains"].append(domain)
            for observation in state["observations"]:
                if observation["host"] == domain["host"]:
                    observation["status"] = "accepted"
                    observation["service_id"] = service_id
                    observation["match"] = domain["match"]

        context.store.mutate(update)
        if not found:
            raise HTTPException(status_code=404, detail="service not found")
        return next(item for item in context.store.snapshot()["services"] if item["id"] == service_id)

    @app.post("/api/observations/{host}/assign")
    async def assign_observation(host: str, request: AssignRequest) -> dict[str, Any]:
        normalized = normalize_host(host)
        if not normalized:
            raise HTTPException(status_code=400, detail="invalid host")
        return await add_domain(request.service_id, DomainRequest(host=normalized, match=request.match, source="mihomo-observation"))

    @app.post("/api/observations/{host}/ignore")
    async def ignore_observation(host: str) -> dict[str, Any]:
        normalized = normalize_host(host)
        changed = False

        def update(state: dict[str, Any]) -> None:
            nonlocal changed
            for item in state["observations"]:
                if item["host"] == normalized:
                    item["status"] = "ignored"
                    changed = True

        context.store.mutate(update)
        if not changed:
            raise HTTPException(status_code=404, detail="observation not found")
        return {"host": normalized, "status": "ignored"}

    @app.get("/api/preview")
    async def preview() -> dict[str, Any]:
        payload = context.store.snapshot()
        rendered, warnings = compile_config(payload, context.shared_dir)
        digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
        return {"yaml": redact_rendered_config(rendered), "sha256": digest, "warnings": warnings, "redacted": True}

    @app.post("/api/rule-sources/check")
    async def check_rule_sources() -> dict[str, Any]:
        results: dict[str, Any] = {}
        sources = active_rule_sources(context.store.snapshot())
        async with httpx.AsyncClient(timeout=15, follow_redirects=True, trust_env=False) as http_client:
            for source in sources:
                try:
                    response = await http_client.get(source["url"])
                    response.raise_for_status()
                    results[source["id"]] = {
                        "status": response.status_code,
                        "bytes": len(response.content),
                        "sha256": hashlib.sha256(response.content).hexdigest(),
                        "checked_at": now_iso(),
                    }
                except Exception as exc:
                    results[source["id"]] = {"status": "error", "error": str(exc), "checked_at": now_iso()}
        context.store.mutate(lambda state: state.update({"rule_checks": results}))
        return results

    @app.post("/api/apply")
    async def apply() -> dict[str, Any]:
        try:
            return await apply_compiled_state(context)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    return app


app = create_app()
