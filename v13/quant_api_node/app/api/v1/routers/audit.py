"""V1 - 审计日志路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from v13.quant_api_node.app.core.dependencies import get_sqlite
from v13.quant_state_node.persistence import SqliteStore

router = APIRouter()


def _store() -> SqliteStore:
    return get_sqlite()


@router.get("", summary="读取审计日志")
async def list_audit(
    limit: int = Query(100, ge=1, le=1000),
    store: SqliteStore = Depends(_store),
) -> dict[str, Any]:
    return {"count": limit, "entries": store.fetch_audit(limit=limit)}
