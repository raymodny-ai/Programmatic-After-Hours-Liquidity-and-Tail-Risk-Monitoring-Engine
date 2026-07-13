"""V1 - Pipeline 手动触发路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from v13.quant_api_node.app.services.data_service import DataService

router = APIRouter()


def _svc() -> DataService:
    return DataService()


@router.post("/run", summary="手动触发完整盘后流水线")
async def run_now(svc: DataService = Depends(_svc)) -> dict[str, Any]:
    return await svc.run_pipeline()
