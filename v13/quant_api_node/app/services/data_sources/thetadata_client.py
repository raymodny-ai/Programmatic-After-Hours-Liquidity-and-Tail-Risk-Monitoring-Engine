"""ThetaData 本地代理客户端 (V1.3 主力数据源)。

特性：
- 通过 ``THETADATA_PROXY_URL`` 环境变量切换代理地址（默认 localhost:25510）
- 指数级退避重试（tenacity）：1s → 2s → 4s → 8s → 16s（最大 32s），总计 5 次
- 超时配置：基础 5s，每次重试按比例放大
- 失败时自动 fallback 到 v1.2.1 的 Polygon + yfinance 链路

REST API 调用契约（ThetaData 本地代理）：

::

    GET {THETADATA_PROXY_URL}/v3/option/list/eod
        ?symbol=SPY
        &date=2026-07-10
        &strike=550
    Response: { "contract": "...", "iv": 0.18, "delta": 0.51, ... }

    GET {THETADATA_PROXY_URL}/v3/option/surface/eod
        ?symbol=SPY
        &date=2026-07-10
    Response: {
        "strikes": [...],
        "expirations": [...],
        "iv": [[...]],   // shape = (len(expirations), len(strikes))
        "oi":  [[...]],
        "volume": [[...]],
        "spot": 550.1
    }
"""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


class ThetaDataError(Exception):
    """ThetaData 客户端错误。"""


class ThetaDataClient:
    """ThetaData 本地代理 HTTP 客户端。"""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 5.0,
        max_attempts: int = 5,
    ) -> None:
        self.base_url = base_url or os.getenv("THETADATA_PROXY_URL", "http://localhost:25510")
        self.timeout = timeout
        self.max_attempts = max_attempts

    async def fetch_option_surface(
        self,
        ticker: str,
        as_of: date,
    ) -> dict[str, Any] | None:
        """获取单标的完整期权链 3D 表面（远月 + 深度 OTM）。

        返回格式与 ``OptionsChainSnapshot`` 一致::

            {
                "strikes": [...],
                "expirations": [...],
                "iv": [[...]],
                "oi":  [[...]],
                "volume": [[...]],
                "spot": float
            }
        """
        url = f"{self.base_url}/v3/option/surface/eod"
        params = {"symbol": ticker, "date": as_of.isoformat()}

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=16),
            retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException, ThetaDataError)),
            reraise=True,
        ):
            with attempt:
                try:
                    async with httpx.AsyncClient(timeout=self.timeout * (attempt.retry_state.attempt_number + 1)) as client:
                        r = await client.get(url, params=params)
                        r.raise_for_status()
                        return r.json()
                except (httpx.RequestError, httpx.TimeoutException) as e:
                    logger.warning(
                        "ThetaData 拉取失败 %s %s 重试中 (attempt %d)",
                        url,
                        e,
                        attempt.retry_state.attempt_number,
                    )
                    if attempt.retry_state.attempt_number >= self.max_attempts:
                        logger.exception("ThetaData 重试 %d 次后仍失败, 放弃", self.max_attempts)
                        return None
                    raise
        return None

    async def fetch_option_quote(
        self,
        ticker: str,
        as_of: date,
        strike: float,
        option_type: str = "call",
    ) -> dict[str, Any] | None:
        """拉取单条期权 quote（用于补数）。"""
        url = f"{self.base_url}/v3/option/quote/eod"
        params = {
            "symbol": ticker,
            "date": as_of.isoformat(),
            "strike": strike,
            "right": option_type[0].lower(),
        }

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=wait_exponential(multiplier=1, min=1, max=16),
            retry=retry_if_exception_type((httpx.RequestError, httpx.TimeoutException)),
            reraise=False,
        ):
            with attempt:
                try:
                    async with httpx.AsyncClient(timeout=self.timeout) as client:
                        r = await client.get(url, params=params)
                        r.raise_for_status()
                        return r.json()
                except (httpx.RequestError, httpx.TimeoutException) as e:
                    logger.warning("ThetaData quote 失败: %s", e)
                    if attempt.retry_state.attempt_number >= self.max_attempts:
                        return None
        return None

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{self.base_url}/health")
                return r.status_code == 200
        except Exception:
            return False
