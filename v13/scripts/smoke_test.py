"""V1.3 冒烟测试脚本 - 验证所有路由 + WebSocket 可达。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QUANT_ENABLE_SCHEDULER", "false")
_tmpdir = tempfile.mkdtemp(prefix="v13_smoke_")
os.environ["QUANT_SQLITE_PATH"] = str(Path(_tmpdir) / "smoke.db")
os.environ["QUANT_SNAPSHOT_DIR"] = str(Path(_tmpdir) / "processed")
Path(_tmpdir, "processed").mkdir()


def main() -> int:
    print("=" * 60)
    print("V1.3 冒烟测试")
    print("=" * 60)

    from v13.quant_api_node.app.core import dependencies
    dependencies.reset_caches()

    from fastapi.testclient import TestClient
    from v13.quant_api_node.app.main import app

    OK = "[OK]"
    NG = "[FAIL]"
    failed = 0

    with TestClient(app) as client:
        # 1. Meta
        print("\n[1] Meta 端点")
        r = client.get("/")
        if r.status_code == 200:
            d = r.json()
            print(f"    {OK} GET /          -> {d['service']} v{d['version']}")
        else:
            print(f"    {NG} GET / -> {r.status_code}")
            failed += 1

        r = client.get("/health")
        if r.status_code == 200:
            h = r.json()
            print(f"    {OK} GET /health    -> service={h['service']} redis={h['redis']} sqlite={h['sqlite']}")
        else:
            print(f"    {NG} GET /health -> {r.status_code}")
            failed += 1

        # 2. OpenAPI
        print("\n[2] OpenAPI")
        r = client.get("/openapi.json")
        if r.status_code == 200:
            paths = sorted(r.json()["paths"].keys())
            print(f"    {OK} GET /openapi.json -> {len(paths)} paths registered")
            for p in paths:
                print(f"        {p}")
        else:
            print(f"    {NG} GET /openapi.json -> {r.status_code}")
            failed += 1

        # 3. v1
        print("\n[3] v1 业务端点")
        for p in [
            "/api/v1/options/skew",
            "/api/v1/macro/leverage",
            "/api/v1/macro/series/M2",
            "/api/v1/alerts/recent",
            "/api/v1/alerts/stats",
            "/api/v1/config",
            "/api/v1/audit",
        ]:
            r = client.get(p)
            sym = OK if r.status_code == 200 else NG
            if r.status_code != 200:
                failed += 1
            print(f"    {sym} GET {p}  -> {r.status_code}")

        # 4. Legacy
        print("\n[4] v1.2.1 兼容端点")
        for p in ["/api/latest", "/api/stats", "/api/skipped"]:
            r = client.get(p)
            sym = OK if r.status_code == 200 else NG
            if r.status_code != 200:
                failed += 1
            print(f"    {sym} GET {p}  -> {r.status_code}")

        # 5. WebSocket
        print("\n[5] WebSocket /ws/alerts")
        try:
            with client.websocket_connect("/ws/alerts") as ws:
                ws.send_text('{"type": "hello", "client": "smoke"}')
                msg = ws.receive_text()
                print(f"    {OK} WS 收到消息    -> {msg[:80]}")
        except Exception as e:
            print(f"    {NG} WS 连接失败    -> {e}")
            failed += 1

    dependencies.reset_caches()
    print("\n" + "=" * 60)
    if failed == 0:
        print(f"{OK} 冒烟测试全部通过 (0 失败)")
        return 0
    print(f"{NG} 冒烟测试有 {failed} 项失败")
    return 1


if __name__ == "__main__":
    sys.exit(main())