"""V1.3 FastAPI 主入口。

启动方式::

    uvicorn v13.quant_api_node.app.main:app --host 0.0.0.0 --port 8080

或运行 ``python -m v13.quant_api_node.app.main``（含 APScheduler 启动）。
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from v13.quant_api_node.app.api.v1.legacy_compat import router as legacy_router
from v13.quant_api_node.app.api.v1.routers import api_v1_router, ws_router
from v13.quant_api_node.app.core.config import settings
from v13.quant_api_node.app.core.dependencies import (
    get_redis,
    get_snapshot_compat,
    get_sqlite,
)
from v13.quant_api_node.app.core.logging_setup import setup_logging
from v13.quant_api_node.app.scheduler.daily_runner import start_scheduler, stop_scheduler
from v13.shared.schemas import HealthStatus

# 单次应用启动时间
_START_TIME = time.time()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI 启动钩子：初始化日志、注册调度。"""
    setup_logging()
    if settings.enable_scheduler:
        start_scheduler()
    try:
        yield
    finally:
        if settings.enable_scheduler:
            stop_scheduler()


app = FastAPI(
    title=settings.api_title,
    version=settings.api_version,
    description=(
        "宏观流动性与期权尾部风险监控控制台 V1.3 API。\n\n"
        "三层架构：FastAPI Headless → Redis 热缓存 + SQLite 持久化 → Next.js 前端。\n\n"
        "完整路径前缀：`/api/v1/...` (RESTful)；WebSocket: `/ws/alerts`。"
    ),
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST 注册
app.include_router(api_v1_router, prefix="/api/v1")
if settings.enable_v121_legacy_endpoints:
    app.include_router(legacy_router)

# WebSocket 单独挂载（无前缀）
app.include_router(ws_router)


# ── 健康检查（无 v1 前缀，便于 LB / k8s 探针） ────────────────────


@app.get("/health", response_model=HealthStatus, tags=["meta"])
@app.get("/api/health", response_model=HealthStatus, tags=["meta"])
async def health() -> HealthStatus:
    redis = get_redis()
    sqlite = get_sqlite()
    last_run_raw = redis.get_meta("last_run") or get_snapshot_compat().read_latest_snapshot()
    if isinstance(last_run_raw, dict):
        last_run = last_run_raw.get("updated_at")
    else:
        last_run = last_run_raw if isinstance(last_run_raw, str) else None
    return HealthStatus(
        service="quant-api-node",
        version=settings.api_version,
        redis=redis.ping(),
        sqlite=sqlite.ping(),
        last_pipeline_run=last_run,
        uptime_seconds=time.time() - _START_TIME,
    )


# ── 根端点 ──────────────────────────────────────────────────────────


@app.get("/", tags=["meta"])
async def root() -> dict[str, Any]:
    return {
        "service": settings.api_title,
        "version": settings.api_version,
        "docs": "/docs",
        "openapi": "/openapi.json",
        "health": "/health",
        "websocket": "/ws/alerts",
        "api_v1_prefix": "/api/v1",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "v13.quant_api_node.app.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
        log_level="info",
    )
