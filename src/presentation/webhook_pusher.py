"""
Webhook 推送模块 (webhook_pusher.py)

功能：
- 构造标准化的 JSON Payload 并通过 HTTP POST 推送到 Google Apps Script URL
- 作为 gspread 直接写入 Google Sheets 的备选方案
- 支持向多个 Webhook 目标广播

使用场景:
    当无法通过 Service Account 直接访问 Google Sheets 时，
    可以部署 Google Apps Script 作为中间层，
    Python 后端通过 Webhook 将 JSON 数据推送给 Apps Script，
    由 Apps Script 负责写入 Google Sheets。
"""

from datetime import date, datetime
from typing import Any, Optional

import httpx
from loguru import logger


class WebhookPusher:
    """
    Webhook 推送器。

    将计算结果打包为 JSON 并通过 HTTP POST 发送到配置的 Webhook URL。

    用法:
        pusher = WebhookPusher(webhook_url="https://script.google.com/...")
        await pusher.push(payload)
    """

    def __init__(self, webhook_url: str, timeout: float = 30.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout

    def build_payload(
        self,
        aggregated_result: dict[str, Any],
    ) -> dict[str, Any]:
        """
        从聚合结果构建标准的 JSON Payload。

        Payload 结构:
        {
            "timestamp": "2024-01-15T17:00:00-05:00",
            "date": "2024-01-15",
            "metrics": {
                "SPY": {"skew_spread": 0.05, "iv_put_25d": 0.18, ...},
                "QQQ": {...},
                ...
            },
            "cross_asset": [
                {"pair": ["QQQ", "SPY"], "spread": 0.03},
            ],
            "alerts": [
                {"ticker": "QQQ", "severity": "high", "z_score": 2.5, ...},
            ],
            "term_structure": {...},
            "macro_leverage": {...},
        }

        Args:
            aggregated_result: aggregate_results() 的输出

        Returns:
            标准化的 JSON Payload
        """
        # 简化 ticker_results 中的日期对象
        ticker_results = {}
        for ticker, result in aggregated_result.get("ticker_results", {}).items():
            ticker_results[ticker] = {
                "skew_spread": result.get("skew_spread"),
                "iv_put_25d": result.get("iv_put_25d"),
                "iv_call_25d": result.get("iv_call_25d"),
                "error": result.get("error"),
            }

        # 简化 alerts
        alerts = []
        for alert in aggregated_result.get("alerts", []):
            if alert.get("is_alert"):
                alerts.append({
                    "ticker": alert["ticker"],
                    "skew_value": alert["skew_value"],
                    "z_score": alert["z_score"],
                    "severity": alert["severity"],
                    "direction": alert.get("alert_direction"),
                })

        payload = {
            "timestamp": datetime.now().isoformat(),
            "date": aggregated_result.get("date", date.today().isoformat()),
            "metrics": ticker_results,
            "cross_asset": aggregated_result.get("cross_asset_spreads", []),
            "alerts": alerts,
            "term_structure": aggregated_result.get("term_structure"),
            "macro_leverage": aggregated_result.get("macro_leverage"),
        }

        return payload

    async def push(
        self,
        payload: dict[str, Any],
        webhook_url: Optional[str] = None,
    ) -> bool:
        """
        推送 JSON Payload 到 Webhook 端点。

        Args:
            payload: 待推送的 JSON 数据
            webhook_url: 可选的覆盖 URL

        Returns:
            推送是否成功
        """
        url = webhook_url or self.webhook_url

        if not url:
            logger.error("Webhook URL 未设置")
            return False

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                logger.info(f"Webhook 推送成功: {url} (状态码: {response.status_code})")
                return True

        except httpx.HTTPStatusError as e:
            logger.error(f"Webhook 推送失败 (HTTP {e.response.status_code}): {e.response.text[:200]}")
            return False
        except Exception as e:
            logger.error(f"Webhook 推送失败: {type(e).__name__}: {e}")
            return False

    async def push_to_multiple(
        self,
        payload: dict[str, Any],
        urls: list[str],
    ) -> dict[str, bool]:
        """
        向多个 Webhook URL 广播 Payload。

        Args:
            payload: 待推送的 JSON 数据
            urls: Webhook URL 列表

        Returns:
            {url: success} 映射
        """
        results = {}
        for url in urls:
            results[url] = await self.push(payload, webhook_url=url)
        return results
