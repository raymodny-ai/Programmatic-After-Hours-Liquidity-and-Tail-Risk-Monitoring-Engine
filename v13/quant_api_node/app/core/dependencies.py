"""依赖注入（FastAPI Depends）。

提供两个全局单例：
1. SqliteStore - SQLite 持久化
2. RedisCache - Redis 热缓存

注意 Redis 客户端初始化是惰性的（首次调用 ping），
这样在本地无 Redis 环境也能完成应用启动。
"""

from __future__ import annotations

from functools import lru_cache

from v13.quant_api_node.app.core.config import settings
from v13.quant_state_node.persistence import RedisCache, SnapshotCompat, SqliteStore


@lru_cache(maxsize=1)
def get_sqlite() -> SqliteStore:
    """SQLite 存储单例。"""
    return SqliteStore(settings.sqlite_path)


@lru_cache(maxsize=1)
def get_redis() -> RedisCache:
    """Redis 缓存单例（惰性连接）。"""
    return RedisCache(
        host=settings.redis_host,
        port=settings.redis_port,
        db=settings.redis_db,
        ttl_seconds=settings.redis_ttl_seconds,
    )


@lru_cache(maxsize=1)
def get_snapshot_compat() -> SnapshotCompat:
    """v1.2.1 JSON 快照兼容。"""
    return SnapshotCompat(settings.snapshot_dir)


def reset_caches() -> None:
    """测试钩子：清理单例。"""
    get_sqlite.cache_clear()
    get_redis.cache_clear()
    get_snapshot_compat.cache_clear()
