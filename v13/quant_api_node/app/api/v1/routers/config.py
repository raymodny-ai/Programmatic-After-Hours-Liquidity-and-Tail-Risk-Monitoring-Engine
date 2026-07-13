"""V1 - 风控配置 (YAML) 路由。

GET   /api/v1/config/{key}       读取
PUT   /api/v1/config/{key}       写入 (YAML 文本 + JSON 值)
GET   /api/v1/config              列出全部
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pydantic import BaseModel, Field

from v13.quant_api_node.app.core.dependencies import get_sqlite
from v13.quant_state_node.persistence import SqliteStore

router = APIRouter()


def _store() -> SqliteStore:
    return get_sqlite()


class ConfigUpsert(BaseModel):
    """配置写入载荷。"""

    yaml_text: str | None = Field(None, description="原始 YAML 文本")
    value: dict[str, Any] = Field(default_factory=dict, description="结构化 JSON 值")


@router.get("", summary="列出全部风控配置")
async def list_configs(store: SqliteStore = Depends(_store)) -> dict[str, Any]:
    return {"configs": store.list_configs()}


@router.get("/{key}", summary="读取单条风控配置")
async def get_config(key: str, store: SqliteStore = Depends(_store)) -> dict[str, Any]:
    cfg = store.get_config(key)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"配置不存在: {key}")
    return {"key": key, **cfg}


@router.put("/{key}", summary="写入单条风控配置")
async def put_config(
    key: str,
    payload: ConfigUpsert = Body(...),
    actor: str = "api",
    store: SqliteStore = Depends(_store),
) -> dict[str, Any]:
    store.put_config(key, payload.value, payload.yaml_text)
    store.append_audit("config_upsert", {"key": key}, actor=actor)
    return {"key": key, "ok": True}
