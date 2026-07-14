"""DataService - 业务编排层。

调用 v1.2.1 已有的计算模块（``src.calculation``），对外屏蔽数据源细节。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from v13.quant_api_node.app.core.dependencies import (
    get_redis,
    get_snapshot_compat,
    get_sqlite,
)
from v13.shared.schemas import (
    AlertRecord,
    LeverageSnapshot,
    OptionsChainSnapshot,
    SkewSnapshot,
)

logger = logging.getLogger(__name__)


class DataService:
    """聚合数据获取 / 计算 / 持久化的高层服务。"""

    def __init__(self) -> None:
        self.sqlite = get_sqlite()
        self.redis = get_redis()
        self.snapshots = get_snapshot_compat()

    # ── Skew 截面 ───────────────────────────────────────────────────────

    async def get_latest_skew(self, ticker: str | None = None) -> list[dict[str, Any]]:
        """获取所有/单标的最新 Skew（Redis → SQLite → Snapshot compat 降级）。"""
        if self.redis.available:
            cached = self.redis.get_all_latest()
            if cached:
                items = list(cached.values())
                if ticker:
                    items = [it for it in items if it.get("ticker") == ticker]
                return items
        # 优先使用快照 JSON（兼容 v1.2.1）
        snap = self.snapshots.read_latest_snapshot()
        if snap:
            items = list((snap.get("snapshots") or {}).values()) if isinstance(snap.get("snapshots"), dict) else []
            if ticker:
                items = [it for it in items if it.get("ticker") == ticker]
            return items
        # 最后回退到 SQLite
        try:
            history = self.sqlite.fetch_skew_history(ticker or "ANY", limit=1)
            if history:
                return history
        except Exception:
            pass
        return []

    # ── 期权链 3D 表面 ─────────────────────────────────────────────────

    async def get_options_surface(self, ticker: str) -> OptionsChainSnapshot:
        """返回单标的期权链完整数据。

        数据源优先级：
        1. Redis 缓存（5 分钟 TTL）
        2. SQLite / SnapshotCompat（待 V1.3 后续阶段从 ThetaData 拉取）
        """
        cached = None
        if self.redis.available:
            cached = self.redis.get_latest(f"surface:{ticker}")
        if cached:
            return OptionsChainSnapshot.model_validate(cached)
        # 默认空快照（保持接口契约稳定）
        return OptionsChainSnapshot(
            ticker=ticker,
            as_of_date=date.today(),
            data_quality="unavailable",
            completeness={"otm_deep_pct": 0.0, "far_months_pct": 0.0},
        )

    # ── 宏观杠杆 ───────────────────────────────────────────────────────

    async def get_latest_leverage(self) -> dict[str, Any]:
        """返回 Margin Debt / M2 / Ratio 截面（按 V1.2.1 已有逻辑计算）。"""
        snap = self.snapshots.read_latest_snapshot() or {}
        macro_block = snap.get("macro", {})
        return {
            "as_of_date": snap.get("date"),
            "ratio": macro_block.get("leverage_ratio"),
            "ratio_yoy": macro_block.get("leverage_ratio_yoy"),
            "ratio_3m_momentum": macro_block.get("leverage_3m_momentum"),
            "momentum_reversal": macro_block.get("leverage_momentum_reversal", False),
            "m2": macro_block.get("m2"),
            "margin_debt": macro_block.get("margin_debt"),
            "signal_quality": macro_block.get("signal_quality", "primary"),
        }

    # ── 告警流水 ───────────────────────────────────────────────────────

    async def get_recent_alerts(
        self,
        severity_min: str | None = "elevated",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.sqlite.fetch_alerts(severity_min=severity_min, limit=limit)

    # ── 触发 Pipeline ──────────────────────────────────────────────────

    async def run_pipeline(self) -> dict[str, Any]:
        """触发完整的盘后流水线（通过 src.main.run_full_pipeline 包装）。"""
        try:
            from src.main import run_full_pipeline  # 复用 v1.2.1 主流程

            result = await run_full_pipeline()
            self.redis.record_pipeline_run()
            # 发布一条通用事件
            self.redis.publish_alert(
                {
                    "type": "pipeline_completed",
                    "as_of_date": date.today().isoformat(),
                    "ok": True,
                }
            )
            # v1.2.1 run_full_pipeline 返回 aggregated dict,内含 DataFrame / Path / datetime
            # 等不可 JSON 序列化的字段。为避免 FastAPI 500,只返回顶层“是否成功”+ pipeline
            # 写入的最新 JSON 快照路径(下游从 /api/latest 读详情)。
            ticker_count = 0
            if isinstance(result, dict):
                df = result.get("daily_snapshot_df")
                if hasattr(df, "shape"):
                    ticker_count = int(df.shape[0])
                elif isinstance(df, list):
                    ticker_count = len(df)
            return {
                "ok": True,
                "as_of_date": date.today().isoformat(),
                "tickers_aggregated": ticker_count,
                "snapshot_path": "/api/latest",
                "skew_endpoint": "/api/v1/options/skew",
            }
        except Exception as e:
            logger.exception("pipeline 执行失败: %s", e)
            return {"ok": False, "error": str(e)}
