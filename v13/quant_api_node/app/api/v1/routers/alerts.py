"""V1 - 告警路由。

GET /api/v1/alerts/recent        最近告警
GET /api/v1/alerts/stats         统计分布
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from v13.quant_api_node.app.services.data_service import DataService

router = APIRouter()


def _svc() -> DataService:
    return DataService()


@router.get("/recent", summary="最近告警流水")
async def recent(
    severity_min: str = Query("elevated", description="最低严重度: normal/watch/elevated/high/critical"),
    limit: int = Query(50, ge=1, le=500),
    svc: DataService = Depends(_svc),
) -> dict[str, Any]:
    items = await svc.get_recent_alerts(severity_min=severity_min, limit=limit)
    return {"severity_min": severity_min, "count": len(items), "items": items}


@router.get("/stats", summary="告警统计分布")
async def stats(svc: DataService = Depends(_svc)) -> dict[str, Any]:
    all_alerts = svc.sqlite.fetch_alerts(severity_min=None, limit=1000)
    by_severity: dict[str, int] = {}
    by_ticker: dict[str, int] = {}
    for a in all_alerts:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1
        by_ticker[a["ticker"]] = by_ticker.get(a["ticker"], 0) + 1
    return {"total": len(all_alerts), "by_severity": by_severity, "by_ticker": by_ticker}
