"""V1 - 宏观杠杆 / 时间序列路由。

GET /api/v1/macro/leverage         当前 Margin Debt / M2 / Ratio + 动量反转
GET /api/v1/macro/series/{name}    时间序列
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from v13.quant_api_node.app.services.data_service import DataService

router = APIRouter()


def _svc() -> DataService:
    return DataService()


@router.get("/leverage", summary="当前宏观杠杆截面")
async def get_leverage(svc: DataService = Depends(_svc)) -> dict[str, Any]:
    return await svc.get_latest_leverage()


@router.get("/series/{name}", summary="宏观时间序列（M2 / FINRA_MARGIN）")
async def get_series(
    name: str,
    days: int = Query(365, ge=1, le=3650),
    svc: DataService = Depends(_svc),
) -> dict[str, Any]:
    rows = svc.sqlite.fetch_macro(name, limit=days)
    return {
        "name": name,
        "count": len(rows),
        "values": rows,
    }
