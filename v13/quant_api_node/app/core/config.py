"""配置：环境变量加载（Pydantic Settings v2）。

可通过以下方式覆盖：

- 环境变量（优先级最高）：``QUANT_REDIS_HOST=redis ...``
- ``.env`` 文件（开发）
- 默认值（开发模式快速上手）
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """V1.3 后端配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="QUANT_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 服务 ─────────────────────────────────────────────────────────
    api_title: str = "V1.3 Risk Console API"
    api_version: str = "1.3.0"
    api_port: int = 8080
    api_host: str = "0.0.0.0"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost", "http://localhost:3000", "http://localhost:8080"]
    )

    # ── 持久化 ───────────────────────────────────────────────────────
    sqlite_path: str = "data/v13_state.db"
    snapshot_dir: str = "data/processed"

    # ── Redis ────────────────────────────────────────────────────────
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_ttl_seconds: int = 300

    # ── 数据源 ───────────────────────────────────────────────────────
    polygon_api_key: str = ""
    fred_api_key: str = ""
    thetadata_proxy_url: str = "http://localhost:25510"
    finra_download_url: str = "https://www.finra.org/sites/default/files/2021-03/margin-statistics"

    # ── 调度 ─────────────────────────────────────────────────────────
    pipeline_cron_hour_et: int = 21  # 美东 21:00（盘后固化）
    pipeline_cron_minute_et: int = 0
    enable_scheduler: bool = True

    # ── 标的 ─────────────────────────────────────────────────────────
    default_tickers: tuple[str, ...] = ("SPY", "QQQ", "IWM", "DIA")

    # ── V1.2.1 兼容 ─────────────────────────────────────────────────
    enable_v121_legacy_endpoints: bool = True

    @property
    def snapshot_dir_path(self) -> Path:
        from pathlib import Path as _P

        return _P(self.snapshot_dir)


settings = Settings()  # 类型化全局实例；测试可覆盖字段
