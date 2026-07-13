"""v1.2.1 兼容路由 — 把 v1.2.1 的端点迁移到 V1.3 但保留无前缀路径。

| v1.2.1 路径             | V1.3 路径                          |
|--------------------------|--------------------------------------|
| /api/latest              | /api/v1/options/skew                |
| /api/stats               | /api/v1/alerts/stats                |
| /api/skipped             | /api/v1/alerts/recent               |
| /api/vxn_alert           | (复用) /api/v1/alerts/recent        |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from v13.quant_api_node.app.services.data_service import DataService

router = APIRouter()


def _svc() -> DataService:
    return DataService()


@router.get("/api/latest", summary="v1.2.1 兼容 - 最新 Skew 截面")
async def legacy_latest(
    ticker: str | None = None,
    svc: DataService = Depends(_svc),
) -> dict[str, Any]:
    """返回 v1.2.1 等价结构 ``{updated_at, snapshots, ...}``。"""
    snap = svc.snapshots.read_latest_snapshot() or {}
    if not snap:
        # 当 JSON 快照尚未生成时，回退到 Redis / SQLite 列表并包装
        items = await svc.get_latest_skew(ticker)
        if ticker:
            items = [it for it in items if it.get("ticker") == ticker]
        return {
            "updated_at": None,
            "snapshots": {it.get("ticker", f"item_{i}"): it for i, it in enumerate(items)},
            "source": "live",
        }
    if ticker:
        snaps_dict = snap.get("snapshots") or {}
        snap = {**snap, "snapshots": {k: v for k, v in snaps_dict.items() if k == ticker}}
    return snap


@router.get("/api/stats", summary="v1.2.1 兼容 - 告警统计")
async def legacy_stats(svc: DataService = Depends(_svc)) -> dict[str, Any]:
    all_alerts = svc.sqlite.fetch_alerts(severity_min=None, limit=1000)
    by_severity: dict[str, int] = {}
    by_quality: dict[str, int] = {}
    for a in all_alerts:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1
        raw = a.get("raw") or {}
        q = raw.get("signal_quality", "primary")
        by_quality[q] = by_quality.get(q, 0) + 1
    return {
        "total": len(all_alerts),
        "by_severity": by_severity,
        "signal_quality_distribution": by_quality,
    }


@router.get("/api/skipped", summary="v1.2.1 兼容 - 跳过标的清单")
async def legacy_skipped(svc: DataService = Depends(_svc)) -> dict[str, Any]:
    skipped = svc.snapshots.read_skipped_tickers()
    return {"count": len(skipped), "items": skipped}


@router.get("/api/vxn_alert", summary="v1.2.1 兼容 - VXN 最新告警")
async def legacy_vxn_alert(svc: DataService = Depends(_svc)) -> dict[str, Any]:
    items = svc.sqlite.fetch_alerts(severity_min=None, limit=10)
    vxn = [a for a in items if a["ticker"] == "VXN"] or items
    if not vxn:
        snap = svc.snapshots.read_volatility_regime()
        if snap:
            return {"ok": True, "data": snap.get("vxn_alert", snap.get("vxn", {}))}
        return {"ok": True, "data": None}
    return {"ok": True, "data": vxn[0]}
