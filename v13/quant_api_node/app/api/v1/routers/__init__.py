"""V1 API 路由聚合器。"""

from __future__ import annotations

from fastapi import APIRouter

from v13.quant_api_node.app.api.v1.routers.alerts import router as alerts_router
from v13.quant_api_node.app.api.v1.routers.audit import router as audit_router
from v13.quant_api_node.app.api.v1.routers.config import router as config_router
from v13.quant_api_node.app.api.v1.routers.macro import router as macro_router
from v13.quant_api_node.app.api.v1.routers.options import router as options_router
from v13.quant_api_node.app.api.v1.routers.pipeline import router as pipeline_router
from v13.quant_api_node.app.api.v1.routers.ws_alerts import router as ws_router

api_v1_router = APIRouter()
api_v1_router.include_router(options_router, prefix="/options", tags=["options"])
api_v1_router.include_router(macro_router, prefix="/macro", tags=["macro"])
api_v1_router.include_router(alerts_router, prefix="/alerts", tags=["alerts"])
api_v1_router.include_router(config_router, prefix="/config", tags=["config"])
api_v1_router.include_router(audit_router, prefix="/audit", tags=["audit"])
api_v1_router.include_router(pipeline_router, prefix="/pipeline", tags=["pipeline"])

# WebSocket 端点（单独挂载，无 v1 前缀），通过 main.py 顶层 `app.include_router(ws_router)` 引入。
__all__ = ["api_v1_router", "ws_router"]
"""V1 Routers 包。"""
