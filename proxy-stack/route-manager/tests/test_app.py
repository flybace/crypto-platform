from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi.testclient import TestClient
import yaml

import app.main as route_manager
from app.main import (
    DEFAULT_RULE_SOURCES,
    compile_config,
    create_app,
    extract_connection_hosts,
    extract_hosts,
    normalize_imported_policy,
    redact_rendered_config,
    score_delay,
)


class FakeMihomo:
    def __init__(self) -> None:
        self.applied: list[tuple[str, str]] = []
        self.selected: list[tuple[str, str]] = []
        self.delay_calls: list[tuple[str, str, int]] = []

    async def version(self):
        return {"version": "test-kernel"}

    async def config(self):
        return {"mode": "rule", "mixed-port": 7890, "allow-lan": True, "bind-address": "0.0.0.0"}

    async def connections(self):
        return {
            "connections": [
                {"metadata": {"host": "api.example.com"}},
                {"metadata": {"host": "stream.example.com"}},
                {"metadata": {"host": "192.168.1.5"}},
            ]
        }

    async def proxies(self):
        return {
            "proxies": {
                "PROXY": {"type": "Selector", "now": "Node Slow", "all": ["Node Slow", "Node Fast"]},
                "Node Slow": {"type": "Trojan", "now": None, "all": ["Node Slow"]},
                "Node Fast": {"type": "Vmess", "now": None, "all": ["Node Fast"]},
            }
        }

    async def delay(self, proxy_name: str, url: str, timeout_ms: int = 5000, expected_status: int = 200):
        self.delay_calls.append((proxy_name, url, expected_status))
        if proxy_name == "Node Fast":
            return {"delay": 120}
        if proxy_name == "Node Slow":
            return {"delay": 620}
        raise RuntimeError("unknown proxy")

    async def select(self, group_name: str, proxy_name: str):
        self.selected.append((group_name, proxy_name))

    async def dns_query(self, host: str, query_type: str = "A"):
        if query_type == "A":
            return {"Answer": [{"data": "8.8.8.8", "type": 1}]}
        if query_type == "CNAME":
            return {"Answer": [{"data": "edge.example.net.", "type": 5}]}
        return {"Answer": []}

    async def apply(self, payload: str, path: str):
        self.applied.append((payload, path))


def test_extract_hosts_ignores_private_and_collects_embedded_urls():
    html = """
    <a href="https://www.example.com/login">login</a>
    <script src="https://cdn.example.net/app.js"></script>
    <img src="http://127.0.0.1/private.png">
    """
    assert extract_hosts(html) == {"www.example.com", "cdn.example.net"}


def test_extract_connection_hosts_uses_mihomo_host_only():
    payload = {
        "connections": [
            {"metadata": {"host": "api.example.com"}},
            {"metadata": {"host": "localhost"}},
            {"metadata": {"host": "10.0.0.1"}},
        ]
    }
    assert extract_connection_hosts(payload) == {"api.example.com"}


def test_score_delay_prefers_lower_latency():
    assert score_delay(120) > score_delay(620)


def test_compile_keeps_rules_owned_by_the_manager(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MIHOMO_SECRET", "test-secret")
    state = {
        "providers": [
            {
                "id": "provider-a",
                "name": "Provider A",
                "url": "https://subscription.example/a",
                "group": "PROVIDER_A",
                "interval": 86400,
                "enabled": True,
            }
        ],
        "services": [
            {
                "id": "example",
                "name": "Example",
                "scope": "public-api",
                "target": "PROVIDER_A",
                "domains": [{"host": "api.example.com", "match": "domain", "source": "test"}],
            }
        ],
    }
    rendered, warnings = compile_config(state, tmp_path)
    parsed = yaml.safe_load(rendered)
    assert "proxy-providers:" in rendered
    assert "https://subscription.example/a" in rendered
    provider_config = parsed["proxy-providers"]["provider-a"]
    assert set(provider_config) <= {"type", "url", "path", "interval", "health-check"}
    assert provider_config["type"] == "http"
    assert "rules" not in provider_config
    assert "proxy-groups" not in provider_config
    assert "dns" not in provider_config
    assert "tun" not in provider_config
    service_group = next(item for item in parsed["proxy-groups"] if item["name"] == "Example")
    assert service_group["type"] == "select"
    assert service_group["proxies"][:4] == ["PROVIDER_A", "PROXY", "DIRECT", "REJECT"]
    assert service_group["use"] == ["provider-a"]
    assert "DOMAIN,api.example.com,Example" in parsed["rules"]
    assert all(isinstance(item, str) for item in parsed["rules"])
    assert "rules:" in rendered
    assert "subscription.example" not in rendered.split("rules:", 1)[1]
    redacted = redact_rendered_config(rendered)
    assert "test-secret" not in redacted
    assert "secret: '***'" in redacted or "secret: ***" in redacted
    assert any(item["url"].endswith("reject.txt") for item in DEFAULT_RULE_SOURCES)
    global_source = next(item for item in DEFAULT_RULE_SOURCES if item["id"] == "global")
    assert global_source["behavior"] == "classical"
    assert global_source["format"] == "text"
    assert "RULE-SET,global,PROXY" in parsed["rules"]
    assert not warnings


def test_api_observe_assign_preview_and_dry_run(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROUTE_MANAGER_APPLY_MODE", "dry-run")
    fake = FakeMihomo()
    app = create_app(tmp_path / "state", tmp_path / "shared", fake)

    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert client.post(
            "/api/services",
            json={"id": "example", "name": "Example", "scope": "public-api", "target": "Node A"},
        ).status_code == 200
        sync = client.post("/api/capture/sync")
        assert sync.status_code == 200
        assert sync.json()["hosts"] == 2
        assigned = client.post(
            "/api/observations/api.example.com/assign",
            json={"service_id": "example", "match": "domain"},
        )
        assert assigned.status_code == 200
        preview = client.get("/api/preview")
        assert preview.status_code == 200
        parsed = yaml.safe_load(preview.json()["yaml"])
        assert "DOMAIN,api.example.com,Example" in parsed["rules"]
        assert preview.json()["redacted"] is True
        applied = client.post("/api/apply")
        assert applied.status_code == 200
        assert applied.json()["status"] == "draft"
        assert not fake.applied
        assert (tmp_path / "shared" / "rendered" / "config.yaml").exists()


def test_imported_policy_keeps_current_nodes_and_rewrites_groups(tmp_path: Path):
    imported = normalize_imported_policy(
        {
            "proxies": [{"name": "old-node", "type": "vmess", "server": "old.example.com"}],
            "proxy-groups": [
                {"name": "ChatGPT", "type": "select", "proxies": ["old-node", "Proxy", "DIRECT"]},
                {"name": "Proxy", "type": "select", "proxies": ["old-node"]},
            ],
            "rule-providers": {
                "ChatGPT": {
                    "type": "http",
                    "behavior": "classical",
                    "url": "https://rules.example.com/chatgpt.list",
                    "path": "./old-path.yaml",
                }
            },
            "rules": ["RULE-SET,ChatGPT,ChatGPT", "MATCH,DIRECT"],
        },
        "legacy.yaml",
    )
    state = {
        "providers": [
            {
                "id": "provider-a",
                "url": "https://subscription.example/a",
                "group": "PROVIDER_A",
                "interval": 86400,
                "enabled": True,
            }
        ],
        "services": [],
        "imported_policy": imported,
    }
    rendered, warnings = compile_config(state, tmp_path)
    parsed = yaml.safe_load(rendered)
    groups = {item["name"]: item for item in parsed["proxy-groups"]}
    assert "ChatGPT" in groups
    assert "old-node" not in groups["ChatGPT"]["proxies"]
    assert groups["ChatGPT"]["proxies"] == ["PROVIDER_A", "PROXY", "DIRECT"]
    assert groups["ChatGPT"]["use"] == ["provider-a"]
    assert set(parsed["rule-providers"]) == {"ChatGPT"}
    assert "RULE-SET,ChatGPT,ChatGPT" in parsed["rules"]
    assert "MATCH,DIRECT" in parsed["rules"]
    assert "old.example.com" not in rendered
    assert not warnings


def test_api_masks_provider_url(tmp_path: Path):
    app = create_app(tmp_path / "state", tmp_path / "shared", FakeMihomo())
    with TestClient(app) as client:
        response = client.post(
            "/api/providers",
            json={"id": "private", "name": "Private", "url": "https://user:secret@example.com/sub?id=token"},
        )
        assert response.status_code == 200
        state = client.get("/api/state").json()
        assert state["providers"][0]["url"] == "https://example.com/***"
        assert "secret" not in str(state)


def test_api_access_reports_lan_proxy_endpoints(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("PUBLIC_HOST", "192.168.68.99")
    app = create_app(tmp_path / "state", tmp_path / "shared", FakeMihomo())

    with TestClient(app) as client:
        response = client.get("/api/access")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ready"
    assert payload["lan_exposed"] is True
    assert payload["urls"]["http_proxy"] == "http://192.168.68.99:17890"
    assert payload["urls"]["socks5_proxy"] == "socks5://192.168.68.99:17890"
    assert payload["runtime"]["bind_address"] == "0.0.0.0"


def wait_for_analysis(client: TestClient, analysis_id: str, terminal: set[str]):
    for _ in range(80):
        payload = client.get(f"/api/analysis/{analysis_id}").json()
        if payload.get("status") in terminal:
            return payload
        time.sleep(0.025)
    raise AssertionError(f"analysis did not reach {terminal}: {payload}")


def test_guided_analysis_tests_nodes_and_keeps_draft_pending(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROUTE_MANAGER_APPLY_MODE", "dry-run")
    monkeypatch.delenv("DISCOVERY_HTTP_PROXY", raising=False)
    fake = FakeMihomo()
    app = create_app(tmp_path / "state", tmp_path / "shared", fake)

    async def fake_fetch(url: str, proxy_url: str):
        assert url == "https://site.example.com/login"
        assert proxy_url == "http://metacubexd:7890"
        return {
            "final_url": url,
            "status_code": 200,
            "body": '<script src="https://cdn.example.com/app.js"></script><script>new WebSocket("wss://stream.example.com/socket")</script>',
            "redirects": [],
            "resources": [{"url": "https://cdn.example.com/app.js", "body": "fetch('https://api.example.com/v1')"}],
        }

    monkeypatch.setattr(route_manager, "fetch_discovery_content", fake_fetch)
    with TestClient(app) as client:
        started = client.post(
            "/api/analysis/start",
            json={"url": "https://site.example.com/login", "expected_status": 200},
        )
        assert started.status_code == 200
        analysis_id = started.json()["id"]
        tested = wait_for_analysis(client, analysis_id, {"ready"})
        assert tested["recommendation"]["proxy"] == "Node Fast"
        assert tested["recommendation"]["delay_ms"] == 120
        assert [item[0] for item in fake.delay_calls] == ["Node Slow", "Node Fast"]
        assert client.get("/api/state").json()["services"] == []

        probe_started = client.post(
            f"/api/analysis/{analysis_id}/probe",
            json={"proxy": "Node Fast"},
        )
        assert probe_started.status_code == 200
        probed = wait_for_analysis(client, analysis_id, {"completed"})
        hosts = {item["host"] for item in probed["candidates"]}
        assert {"site.example.com", "cdn.example.com", "api.example.com", "stream.example.com"} <= hosts
        assert "8.8.8.8" in hosts
        assert fake.selected == [("PROXY", "Node Fast"), ("PROXY", "Node Slow")]
        assert client.get("/api/state").json()["services"] == []
        assert not fake.applied

        draft = client.post(
            f"/api/analysis/{analysis_id}/draft",
            json={
                "candidates": [
                    {"host": "site.example.com", "match": "suffix"},
                    {"host": "api.example.com", "match": "domain"},
                    {"host": "8.8.8.8", "match": "ip"},
                ]
            },
        )
        assert draft.status_code == 200
        service = draft.json()["service"]
        assert service["target"] == "Node Fast"
        assert {item["host"] for item in service["domains"]} == {
            "site.example.com",
            "api.example.com",
            "8.8.8.8",
        }
        state = client.get("/api/state").json()
        rendered, _ = compile_config(state, tmp_path / "shared")
        assert "DOMAIN-SUFFIX,site.example.com," in rendered
        assert "DOMAIN,api.example.com," in rendered
        assert "IP-CIDR,8.8.8.8/32," in rendered


def test_auto_add_creates_service_and_runs_probe_in_background(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROUTE_MANAGER_APPLY_MODE", "dry-run")
    monkeypatch.delenv("DISCOVERY_HTTP_PROXY", raising=False)
    fake = FakeMihomo()
    app = create_app(tmp_path / "state", tmp_path / "shared", fake)

    async def fake_fetch(url: str, proxy_url: str):
        return {
            "final_url": url,
            "status_code": 200,
            "body": '<script src="https://cdn.example.com/app.js"></script>',
            "redirects": [],
            "resources": [{"url": "https://cdn.example.com/app.js", "body": "fetch('https://api.example.com/v1')"}],
        }

    monkeypatch.setattr(route_manager, "fetch_discovery_content", fake_fetch)
    with TestClient(app) as client:
        started = client.post(
            "/api/analysis/start",
            json={"url": "https://site.example.com/login", "selection_mode": "auto", "service_name": "站点服务"},
        )
        assert started.status_code == 200
        analysis = wait_for_analysis(client, started.json()["id"], {"completed"})
        assert analysis["selection_mode"] == "auto"
        assert analysis["recommendation"]["proxy"] == "Node Fast"
        assert [item[0] for item in fake.delay_calls] == ["Node Slow", "Node Fast"]

        services = client.get("/api/state").json()["services"]
        assert len(services) == 1
        assert services[0]["name"] == "站点服务"
        assert services[0]["target"] == "Node Fast"
        assert {item["host"] for item in services[0]["domains"]} == {"site.example.com"}
        assert {item["host"] for item in analysis["candidates"]} >= {"site.example.com", "cdn.example.com", "api.example.com"}


def test_manual_add_only_tests_selected_target(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ROUTE_MANAGER_APPLY_MODE", "dry-run")
    monkeypatch.delenv("DISCOVERY_HTTP_PROXY", raising=False)
    fake = FakeMihomo()
    app = create_app(tmp_path / "state", tmp_path / "shared", fake)

    async def fake_fetch(url: str, proxy_url: str):
        return {"final_url": url, "status_code": 200, "body": "", "redirects": [], "resources": []}

    monkeypatch.setattr(route_manager, "fetch_discovery_content", fake_fetch)
    with TestClient(app) as client:
        started = client.post(
            "/api/analysis/start",
            json={"url": "https://manual.example.com", "selection_mode": "manual", "manual_target": "Node Slow"},
        )
        assert started.status_code == 200
        analysis = wait_for_analysis(client, started.json()["id"], {"completed"})
        assert analysis["recommendation"]["proxy"] == "Node Slow"
        assert [item[0] for item in fake.delay_calls] == ["Node Slow"]
        service = client.get("/api/state").json()["services"][0]
        assert service["target"] == "Node Slow"
