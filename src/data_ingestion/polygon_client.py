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
                continue

        logger.info(
            f"成功获取 {ticker} 快照 {len(all_snapshots)} 条 "
            f"(共 {len(contracts)} 个合约)"
        )
        return all_snapshots

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        await self._client.close()
