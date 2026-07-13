"""V1.3 State Layer - quant-state-node

提供三大能力：
1. Redis 热缓存 (WebSocket 推送队列 + 盘后截面)
2. SQLite 持久化 (宏观基本面 + 用户风控配置 + 历史 Skew)
3. 文件快照兼容层 (v1.2.1 的 JSON 快照)

依赖注入通过 RedisCache / SqliteStore 单例实现。
"""

from v13.quant_state_node.persistence.sqlite_store import SqliteStore
from v13.quant_state_node.persistence.redis_cache import RedisCache
from v13.quant_state_node.persistence.snapshot_compat import SnapshotCompat

__all__ = ["SqliteStore", "RedisCache", "SnapshotCompat"]
