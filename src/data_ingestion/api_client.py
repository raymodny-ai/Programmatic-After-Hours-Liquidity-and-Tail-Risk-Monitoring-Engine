"""
异步 HTTP 客户端基类 (api_client.py)

功能：
- 基于 httpx 的异步 HTTP 客户端封装
- 内置令牌桶速率限制器（Token Bucket Rate Limiter）
- 支持使用 tenacity 的指数退避自动重试
- 统一的错误处理与日志记录
"""

import asyncio
import time
from typing import Any, Optional

import httpx
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import MAX_RETRIES, RATE_LIMIT_CALLS_PER_MINUTE, RETRY_BACKOFF_INITIAL


class RateLimiter:
    """
    简单的令牌桶速率限制器。

    每秒钟补充 (calls_per_minute / 60) 个令牌，
    每次 API 调用消耗 1 个令牌。令牌不足时等待。
    """

    def __init__(self, calls_per_minute: int = RATE_LIMIT_CALLS_PER_MINUTE) -> None:
        self._calls_per_minute = calls_per_minute
        self._tokens = float(calls_per_minute)
        self._max_tokens = float(calls_per_minute)
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """获取一个令牌，如果令牌不足则等待。"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            # 按速率补充令牌
            refill_rate = self._calls_per_minute / 60.0
            self._tokens = min(self._max_tokens, self._tokens + elapsed * refill_rate)
            self._last_refill = now

            if self._tokens < 1.0:
                wait_time = (1.0 - self._tokens) / refill_rate
                logger.debug(f"速率限制：等待 {wait_time:.2f}s 以获取令牌")
                await asyncio.sleep(wait_time)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


# 全局共享的速率限制器实例
_rate_limiter = RateLimiter()


def _is_retryable(exception: Exception) -> bool:
    """判断异常是否可重试（HTTP 429 或 5xx）。"""
    if isinstance(exception, httpx.HTTPStatusError):
        status = exception.response.status_code
        return status == 429 or status >= 500
    if isinstance(exception, (httpx.TimeoutException, httpx.ConnectError)):
        return True
    return False


class BaseAPIClient:
    """
    API 客户端基类。

    使用方式:
        client = BaseAPIClient(base_url="https://api.example.com")
        response = await client.get("/v1/data", params={"key": "value"})
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        api_key_param: str = "apiKey",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_key_param = api_key_param
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """延迟初始化 httpx AsyncClient（复用连接）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "User-Agent": "AfterHoursLiquidityMonitor/0.1.0",
                    "Accept": "application/json",
                },
            )
        return self._client

    def _add_auth(self, params: dict[str, Any]) -> dict[str, Any]:
        """向请求参数中添加 API 密钥。"""
        if self.api_key:
            params[self.api_key_param] = self.api_key
        return params

    @retry(
        retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.TimeoutException)),
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(multiplier=RETRY_BACKOFF_INITIAL, min=1, max=60),
        before_sleep=lambda retry_state: logger.warning(
            f"第 {retry_state.attempt_number} 次重试，等待 "
            f"{retry_state.next_action.sleep:.1f}s..."
        ),
    )
    async def _make_request(
        self,
        method: str,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """发出 HTTP 请求（带重试机制）。"""
        await _rate_limiter.acquire()

        client = await self._get_client()
        url = f"{self.base_url}{path}" if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"

        response = await client.request(
            method=method,
            url=url,
            params=params,
            json=json_data,
        )
        response.raise_for_status()
        return response

    async def get(
        self, path: str, params: Optional[dict[str, Any]] = None
    ) -> httpx.Response:
        """GET 请求（自动附加 API 密钥）。"""
        params = self._add_auth(params or {})
        logger.debug(f"GET {path} | params={params}")
        return await self._make_request("GET", path, params=params)

    async def post(
        self,
        path: str,
        params: Optional[dict[str, Any]] = None,
        json_data: Optional[dict[str, Any]] = None,
    ) -> httpx.Response:
        """POST 请求（自动附加 API 密钥）。"""
        params = self._add_auth(params or {})
        logger.debug(f"POST {path} | params={params}")
        return await self._make_request("POST", path, params=params, json_data=json_data)

    async def get_json(
        self, path: str, params: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        """GET 请求并直接返回解析后的 JSON。"""
        response = await self.get(path, params)
        return response.json()

    async def close(self) -> None:
        """关闭 HTTP 客户端。"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
