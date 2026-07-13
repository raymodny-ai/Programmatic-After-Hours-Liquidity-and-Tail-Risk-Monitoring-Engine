"""V1.3 核心数据契约（Pydantic v2 模型）

本模块定义了三个微服务之间共享的核心业务实体。
所有 schema 字段都使用 snake_case，前端 TypeScript 类型将自动转换。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# =====================================================================
# 枚举 (Enums)
# =====================================================================


class DataQualityTier(str, Enum):
    """数据质量层级（前端会作为颜色编码使用）。"""

    PRIMARY = "primary"
    FALLBACK = "fallback"
    UNAVAILABLE = "unavailable"


class SignalQuality(str, Enum):
    """信号质量层级 (4-tier, v1.2.1 延续)。"""

    PRIMARY = "primary"
    FALLBACK_ESTIMATED = "fallback_estimated"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class GreeksSource(str, Enum):
    """Greeks 计算来源。"""

    VENDOR = "vendor"
    BSM_ESTIMATED = "bsm_estimated"
    NONE = "none"


class VxnAlertSeverity(str, Enum):
    """VXN 告警等级（沿用 v1.2.1 六维积分制）。"""

    NORMAL = "normal"
    WATCH = "watch"
    ELEVATED = "elevated"
    HIGH = "high"
    CRITICAL = "critical"


# =====================================================================
# 业务实体 (Entities)
# =====================================================================


class SkewSnapshot(BaseModel):
    """单标的 Skew 数据快照（快照卡片用）。"""

    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    as_of_date: date
    skew_25d: float | None = None
    z_score: float | None = None
    z_score_5d: float | None = None
    z_score_20d: float | None = None
    iv_atm: float | None = None
    status: str = "ok"  # ok | skipped | unavailable
    skip_reason: str | None = None
    data_quality: DataQualityTier = DataQualityTier.PRIMARY
    signal_quality: SignalQuality = SignalQuality.PRIMARY
    greeks_source: GreeksSource = GreeksSource.VENDOR


class OptionsChainSnapshot(BaseModel):
    """单标的全期权链快照（3D 波动率曲面用）。"""

    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    as_of_date: date
    expirations: list[str] = Field(default_factory=list)  # ISO date strings
    strikes: list[float] = Field(default_factory=list)
    iv_surface: list[list[float | None]] = Field(default_factory=list)
    oi_surface: list[list[int | None]] = Field(default_factory=list)
    volume_surface: list[list[int | None]] = Field(default_factory=list)
    data_quality: DataQualityTier = DataQualityTier.PRIMARY
    # 数据完备性审计
    completeness: dict[str, float] = Field(default_factory=dict)
    # 远月合约最深 OTM strike 比率 (例如 0.7 表示 spot × 0.7 为最远点)
    otm_coverage: dict[str, float] = Field(default_factory=dict)


class MacroSeries(BaseModel):
    """宏观基本面时间序列（M2 + FINRA Margin Debt）。"""

    name: str  # "M2" | "FINRA_MARGIN"
    as_of_date: date
    values: dict[str, float | None] = Field(default_factory=dict)
    # key 为 ISO date 字符串
    last_value: float | None = None
    yoy_change: float | None = None
    mom_change: float | None = None
    # 3 月环比动量反转信号
    momentum_reversal: bool = False
    momentum_3m: float | None = None


class LeverageSnapshot(BaseModel):
    """宏观杠杆（Margin Debt / M2）实时截面。"""

    model_config = ConfigDict(use_enum_values=True)

    as_of_date: date
    margin_debt: float | None = None
    m2: float | None = None
    ratio: float | None = None  # 主指标
    ratio_yoy: float | None = None
    ratio_3m_momentum: float | None = None
    momentum_reversal: bool = False  # 3 月环比 vs 前 3 月环比的符号反转
    signal_quality: SignalQuality = SignalQuality.PRIMARY


class VxnAlertRecord(BaseModel):
    """VXN 自动化告警记录（六维积分制）。

    含完整复盘所需字段：原始指标、Z-Score、积分、严重等级、原因列表。"""

    model_config = ConfigDict(use_enum_values=True)

    as_of_date: date
    severity: VxnAlertSeverity = VxnAlertSeverity.NORMAL
    score: int = 0
    threshold_score: int = 2  # 触发 elevated 的阈值
    dimensions: dict[str, float] = Field(default_factory=dict)
    # 维度权重
    weights: dict[str, int] = Field(default_factory=dict)
    # 各维度触发贡献
    contributions: dict[str, int] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    # VXN-VIX 相对压力
    vxn_vix_spread: float | None = None
    vxn_vix_z: float | None = None
    # QQQ 三因子确认
    qqq_confirmation: dict[str, Any] = Field(default_factory=dict)
    # 状态机
    last_reasons: list[str] = Field(default_factory=list)
    notify_action: str = "silent"  # send | upgrade | resolved | cooldown | silent


class AlertRecord(BaseModel):
    """通用告警记录（跨标的，支持 VXN 与 Skew）。"""

    model_config = ConfigDict(use_enum_values=True)

    ticker: str
    as_of_date: date
    severity: VxnAlertSeverity = VxnAlertSeverity.NORMAL
    is_alert: bool = False
    reasons: list[str] = Field(default_factory=list)
    z_score: float | None = None
    last_triggered_at: datetime | None = None


# =====================================================================
# API 响应封装
# =====================================================================


class ApiResponse(BaseModel):
    """统一 API 响应结构。"""

    ok: bool = True
    data: Any | None = None
    error: str | None = None
    as_of_date: date | None = None


class HealthStatus(BaseModel):
    """服务健康状态（/health 端点）。"""

    service: str
    version: str = "1.3.0"
    redis: bool = False
    sqlite: bool = False
    last_pipeline_run: datetime | None = None
    uptime_seconds: float = 0.0
