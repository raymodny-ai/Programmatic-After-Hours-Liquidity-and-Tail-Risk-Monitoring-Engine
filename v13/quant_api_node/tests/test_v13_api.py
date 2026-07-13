"""V1.3 API 路由冒烟测试。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("QUANT_ENABLE_SCHEDULER", "false")
    monkeypatch.setenv("QUANT_SQLITE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("QUANT_SNAPSHOT_DIR", str(tmp_path / "processed"))
    (tmp_path / "processed").mkdir()

    # 清理之前测试残留的单例
    from v13.quant_api_node.app.core import dependencies

    dependencies.reset_caches()

    from v13.quant_api_node.app.main import app

    with TestClient(app) as c:
        yield c
    dependencies.reset_caches()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "quant-api-node"
    assert data["version"] == "1.3.0"


def test_root(client):
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["api_v1_prefix"] == "/api/v1"
    assert data["websocket"] == "/ws/alerts"


def test_legacy_compat_endpoints(client):
    # /api/latest
    r = client.get("/api/latest")
    assert r.status_code == 200
    # /api/stats
    r = client.get("/api/stats")
    assert r.status_code == 200
    # /api/skipped
    r = client.get("/api/skipped")
    assert r.status_code == 200


def test_v1_endpoints_registered(client):
    paths = [
        "/api/v1/options/skew",
        "/api/v1/macro/leverage",
        "/api/v1/alerts/recent",
        "/api/v1/alerts/stats",
        "/api/v1/config",
        "/api/v1/audit",
    ]
    for p in paths:
        r = client.get(p)
        assert r.status_code == 200, f"{p} 返回 {r.status_code}"


def test_config_put_and_get(client):
    key = "thresholds_demo"
    r = client.put(
        f"/api/v1/config/{key}",
        json={"yaml_text": "z_alert: 1.5\n", "value": {"z_alert": 1.5}},
    )
    assert r.status_code == 200
    r = client.get(f"/api/v1/config/{key}")
    assert r.status_code == 200
    body = r.json()
    assert body["value"]["z_alert"] == 1.5
    assert "z_alert" in body["yaml"]
