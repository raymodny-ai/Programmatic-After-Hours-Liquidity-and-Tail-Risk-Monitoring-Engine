"""V1 - 期权 Skew / Surface 路由。

GET /api/v1/options/skew          所有 / 单标的最新 Skew 截面
GET /api/v1/options/skew/{ticker} 历史 Skew
GET /api/v1/options/surface/{ticker} 单标的 3D 表面（VXN QQQ 等）
"""

from __future__ import annotations

from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from v13.quant_api_node.app.services.data_service import DataService

router = APIRouter()


def _svc() -> DataService:
    return DataService()


@router.get("/skew", summary="获取最新 Skew 截面")
async def get_skew(
    ticker: str | None = Query(None, description="为空则返回全部标的"),
    limit: int = Query(50, ge=1, le=1000),
    svc: DataService = Depends(_svc),
) -> dict[str, Any]:
    items = await svc.get_latest_skew(ticker)
    return {
        "as_of_date": date.today().isoformat(),
        "count": len(items),
        "items": items[:limit],
    }


@router.get("/skew/{ticker}", summary="获取单标的 Skew 历史")
async def get_skew_history(
    ticker: str,
    days: int = Query(120, ge=1, le=730),
    svc: DataService = Depends(_svc),
) -> dict[str, Any]:
    history = svc.sqlite.fetch_skew_history(ticker, limit=days)
    return {
        "ticker": ticker,
        "count": len(history),
        "history": history,
    }


@router.get("/surface/{ticker}", summary="获取单标的 3D 表面快照")
async def get_surface(
    ticker: str,
    svc: DataService = Depends(_svc),
) -> dict[str, Any]:
    snap = await svc.get_options_surface(ticker)
    return snap.model_dump()
