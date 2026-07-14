"""
Polygon.io 期权链数据拉取客户端 (polygon_client.py)

功能：
- 获取指定标的的期权合约列表（按 ticker + expiration_date 查询）
- 支持分页拉取（Polygon API 单次最多返回 250 条）
- 解析 Polygon.io 的 Snapshot Options Chain 响应
"""

from datetime import date, datetime
from typing import Any, Optional

from loguru import logger

from config.settings import POLYGON_API_KEY, POLYGON_BASE_URL
from src.data_ingestion.api_client import BaseAPIClient


class PolygonClient:
    """
    Polygon.io API 客户端。

    主要接口:
        get_option_chain(ticker, expiration_date) -> 期权链数据列表
        get_option_contracts(ticker, expiration_date, limit) -> 期权合约分页数据
    """

    # ── v1.2.2 (OpenClaw patch 2026-07-11): 免费层不含 snapshot 的短路开关 ──
    # class-level 是为了多 ticker 共享:一次探测发现 403,后续 ticker 跳过重试风暴。
    _snapshot_entitled: bool = True

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or POLYGON_API_KEY
        if not self.api_key:
            raise ValueError(
                "Polygon API 密钥未设置。请在 .env 文件中设置 POLYGON_API_KEY，"
                "或通过构造函数参数传入。"
            )
        self._client = BaseAPIClient(
            base_url=POLYGON_BASE_URL,
            api_key=self.api_key,
            api_key_param="apiKey",
        )

    @classmethod
    def is_snapshot_entitled(cls) -> bool:
        """是否启用 Polygon snapshot 端点。免费层为 False,会被 probe 被动 flip。"""
        return cls._snapshot_entitled

    @classmethod
    def disable_snapshot(cls, reason: str = "") -> None:
        """本次进程内关闭 Polygon snapshot。yfinance fallback 会接手。"""
        if cls._snapshot_entitled:
            cls._snapshot_entitled = False
            logger.warning(
                f"Polygon snapshot 端点已关闭（原因: {reason}）。"
                f"后续 ticker 将跳过 Polygon,走 yfinance 备用源。"
            )

    async def probe_snapshot_entitlement(self) -> bool:
        """快速探测 snapshot 端点是否可用。

        返回 True = 有权限；返回 False = free tier 或 key 无权(已翻 flip _snapshot_entitled)。
        选 O:SPY{近未来日}C00500000 这种高概率存在的合约,1 次调用快速判定。
        """
        if not self._snapshot_entitled:
            return False
        # 选 60 天后到期的 SPY call 500 (极大概率存在), 1 个合约
        from datetime import timedelta
        from src.data_ingestion.eod_fetcher import find_nearest_expiration  # 避免循环 import 问题
        try:
            exp = find_nearest_expiration()
        except Exception:
            exp = date.today() + timedelta(days=30)
        exp_str = exp.isoformat().replace("-", "")
        contract = f"O:SPY{exp_str}C00500000"
        path = f"/v3/snapshot/options/{contract}"
        try:
            await self._client.get_json(path)
            return True
        except Exception as e:
            # ── unwrap tenacity.RetryError → 最后一跳的 HTTPStatusError ──
            inner: Exception = e
            last_attempt = getattr(e, "last_attempt", None)
            if last_attempt is not None and hasattr(last_attempt, "exception"):
                inner_exc = last_attempt.exception()
                if inner_exc is not None:
                    inner = inner_exc
            status = getattr(getattr(inner, "response", None), "status_code", None)
            if status in (401, 403):
                self.disable_snapshot(reason=f"probe 返 HTTP {status}")
                return False
            # 其他错误(网络/超时)不算无权,仍当有权限处理
            logger.debug(f"snapshot 探活其他错误: {type(e).__name__}: {e}")
            return True

    async def get_option_contracts(
        self,
        ticker: str,
        expiration_date: str,
        limit: int = 250,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        获取指定标的和到期日的期权合约列表（支持分页）。

        Args:
            ticker: 标的符号，如 "SPY"
            expiration_date: 到期日，格式 "YYYY-MM-DD"
            limit: 每页返回数量（最大 250）
            cursor: 分页游标

        Returns:
            Polygon API 原始 JSON 响应:
            {
                "status": "OK",
                "request_id": "...",
                "results": [...],
                "next_url": "..." or null
            }
        """
        params: dict[str, Any] = {
            "underlying_ticker": ticker,
            "expiration_date": expiration_date,
            "limit": limit,
        }
        if cursor:
            params["cursor"] = cursor

        path = "/v3/reference/options/contracts"
        logger.info(f"拉取期权合约: ticker={ticker}, expiration={expiration_date}")

        response = await self._client.get_json(path, params)
        return response

    async def get_all_option_contracts(
        self,
        ticker: str,
        expiration_date: str,
    ) -> list[dict[str, Any]]:
        """
        获取指定标的和到期日的全部期权合约（自动翻页）。

        Returns:
            所有期权合约的列表
        """
        all_results: list[dict[str, Any]] = []
        cursor: Optional[str] = None

        while True:
            data = await self.get_option_contracts(
                ticker=ticker,
                expiration_date=expiration_date,
                cursor=cursor,
            )

            results = data.get("results", [])
            all_results.extend(results)

            # 检查是否有下一页
            next_url = data.get("next_url")
            if next_url and "cursor=" in next_url:
                # 从 next_url 中提取 cursor 参数
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(next_url)
                cursor = parse_qs(parsed.query).get("cursor", [None])[0]
                logger.debug(f"翻页拉取: cursor={cursor}, 已获取 {len(all_results)} 条")
            else:
                break

        logger.info(f"共获取 {ticker} {expiration_date} 期权合约 {len(all_results)} 条")
        return all_results

    async def get_option_snapshots(
        self,
        ticker: str,
        expiration_date: str,
    ) -> list[dict[str, Any]]:
        """
        获取期权快照数据（含 Greeks、IV、最新报价）。

        Polygon.io 快照端点一次最多支持 250 个合约，
        需要先获取合约列表，再分批查询快照。

        Returns:
            期权快照列表，每个元素包含:
            - ticker, details (contract_type, strike_price, expiration_date)
            - greeks (delta, gamma, theta, vega)
            - implied_volatility
            - day (open, high, low, close)
        """
        # ── v1.2.2: snapshot 端点不可用 → 立即返回空,走 yfinance ──
        if not self._snapshot_entitled:
            logger.debug(f"[{ticker}] snapshot 已关闭,直接返回空,走 fallback")
            return []

        # Step 1: 获取所有合约
        contracts = await self.get_all_option_contracts(ticker, expiration_date)
        if not contracts:
            logger.warning(f"{ticker} {expiration_date} 无期权合约数据")
            return []

        # Step 2: 提取合约 ticker 列表（Polygon 格式如 "O:SPY240119C00450000"）
        contract_tickers = [c["ticker"] for c in contracts]

        # Step 3: 分批查询快照（每批最多 250 个）
        all_snapshots: list[dict[str, Any]] = []
        batch_size = 250

        for i in range(0, len(contract_tickers), batch_size):
            batch = contract_tickers[i : i + batch_size]
            tickers_str = ",".join(batch)

            path = f"/v3/snapshot/options/{tickers_str}"
            logger.debug(
                f"获取快照: batch {i // batch_size + 1}, "
                f"{len(batch)} 个合约"
            )

            try:
                data = await self._client.get_json(path)
                snapshots = data.get("results", [])
                all_snapshots.extend(snapshots)
            except Exception as e:
                logger.error(f"获取快照失败: ticker={ticker}, error={e}")
                # ── v1.2.2: unwrap tenacity.RetryError → 最后一跳的 HTTPStatusError ──
                inner: Exception = e
                last_attempt = getattr(e, "last_attempt", None)
                if last_attempt is not None and hasattr(last_attempt, "exception"):
                    inner_exc = last_attempt.exception()
                    if inner_exc is not None:
                        inner = inner_exc
                status = getattr(getattr(inner, "response", None), "status_code", None)
                if status in (401, 403):
                    self.disable_snapshot(reason=f"HTTP {status} on {ticker} batch {i // batch_size + 1}")
                    return []
                continue

        logger.info(
            f"成功获取 {ticker} 快照 {len(all_snapshots)} 条 "
            f"(共 {len(contracts)} 个合约)"
        )
        return all_snapshots

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.close()
