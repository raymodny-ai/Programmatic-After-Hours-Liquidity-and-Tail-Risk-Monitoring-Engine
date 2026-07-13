"""Redis 热缓存层（quant-state-node）

职责：
1. 缓存盘后截面数据（5 分钟 TTL）
2. 提供 WebSocket 推送的 pub/sub 通道（alerts 频道）
3. 优雅降级：Redis 不可用时所有操作返回 safe-default（None / False）

使用 lazy-import redis 以允许本地无 Redis 环境启动（开发模式）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


# 标准键命名空间
class K:
    LATEST_SKEW = "v13:latest:skew"  # Hash ticker -> JSON
    LATEST_MACRO = "v13:latest:macro"  # Hash series -> JSON
    LATEST_LEVERAGE = "v13:latest:leverage"
    LATEST_VXN = "v13:latest:vxn"
    PIPELINE_RUN = "v13:meta:last_run"
    ALERTS_CHANNEL = "v13:ws:alerts"


class RedisCache:
    """Redis 客户端包装（带 safe-default 降级）。

    用法::

        rc = RedisCache(host="localhost", port=6379)
        if rc.available:
            rc.set_latest("SPY", {"skew_25d": 0.55})
            payload = rc.get_latest("SPY")
            rc.publish_alert({"severity": "critical"})
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        ttl_seconds: int = 300,  # 默认 5 分钟
        socket_timeout: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.db = db
        self.ttl = ttl_seconds
        self.socket_timeout = socket_timeout
        self._client: Any | None = None
        self._available: bool = False

    @property
    def available(self) -> bool:
        return self._available

    def connect(self) -> bool:
        """惰性连接。第一次调用可用性检查。"""
        if self._client is not None:
            return self._available
        try:
            import redis  # type: ignore

            client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                socket_timeout=self.socket_timeout,
                socket_connect_timeout=self.socket_timeout,
                decode_responses=True,
            )
            client.ping()
            self._client = client
            self._available = True
            logger.info("Redis 已连接: %s:%s/%s", self.host, self.port, self.db)
        except Exception as e:
            logger.warning("Redis 不可用（降级到纯 SQLite+JSON 模式）: %s", e)
            self._client = None
            self._available = False
        return self._available

    # ── Latest 数据缓存 ─────────────────────────────────────────────────

    def set_latest(self, key: str, value: dict[str, Any]) -> bool:
        if not self.connect() or self._client is None:
            return False
        try:
            self._client.hset(K.LATEST_SKEW, key, json.dumps(value, ensure_ascii=False, default=str))
            self._client.expire(K.LATEST_SKEW, self.ttl)
            return True
        except Exception as e:
            logger.warning("Redis set_latest 失败: %s", e)
            self._available = False
            return False

    def get_latest(self, key: str) -> dict[str, Any] | None:
        if not self.connect() or self._client is None:
            return None
        try:
            raw = self._client.hget(K.LATEST_SKEW, key)
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("Redis get_latest 失败: %s", e)
            self._available = False
            return None

    def get_all_latest(self) -> dict[str, dict[str, Any]]:
        if not self.connect() or self._client is None:
            return {}
        try:
            raw = self._client.hgetall(K.LATEST_SKEW)
            return {k: json.loads(v) for k, v in raw.items()}
        except Exception as e:
            logger.warning("Redis hgetall 失败: %s", e)
            return {}

    def set_meta(self, key: str, value: Any) -> bool:
        if not self.connect() or self._client is None:
            return False
        try:
            self._client.set(
                f"v13:meta:{key}",
                json.dumps(value, ensure_ascii=False, default=str),
                ex=self.ttl,
            )
            return True
        except Exception as e:
            logger.warning("Redis set_meta 失败: %s", e)
            return False

    def get_meta(self, key: str) -> Any | None:
        if not self.connect() or self._client is None:
            return None
        try:
            raw = self._client.get(f"v13:meta:{key}")
            return json.loads(raw) if raw else None
        except Exception as e:
            logger.warning("Redis get_meta 失败: %s", e)
            return None

    def record_pipeline_run(self) -> None:
        """记录最近一次 pipeline 完成时间（供 /health 使用）。"""
        self.set_meta("last_run", datetime.now().isoformat())

    # ── WebSocket 推送通道 ──────────────────────────────────────────────

    def publish_alert(self, payload: dict[str, Any]) -> int:
        """发布告警到 WebSocket 通道。返回订阅者数。"""
        if not self.connect() or self._client is None:
            return 0
        try:
            payload["published_at"] = datetime.now().isoformat()
            return int(self._client.publish(K.ALERTS_CHANNEL, json.dumps(payload, ensure_ascii=False, default=str)))
        except Exception as e:
            logger.warning("Redis publish 失败: %s", e)
            return 0

    def subscribe_alerts(self):
        """订阅告警通道。需配合 thread / asyncio 使用。"""
        if not self.connect() or self._client is None:
            return None
        try:
            pubsub = self._client.pubsub(ignore_subscribe_messages=True)
            pubsub.subscribe(K.ALERTS_CHANNEL)
            return pubsub
        except Exception as e:
            logger.warning("Redis subscribe 失败: %s", e)
            return None

    # ── 健康检查 ─────────────────────────────────────────────────────────

    def ping(self) -> bool:
        if self._client is None:
            return self.connect()
        try:
            return bool(self._client.ping())
        except Exception:
            self._available = False
            return False

    def close(self) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
            self._available = False
