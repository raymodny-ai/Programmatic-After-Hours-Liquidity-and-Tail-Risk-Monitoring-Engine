"""V1.3 共享 Schemas

跨微服务的契约层（quant-api-node ↔ quant-state-node ↔ quant-ui-node）。
任何字段变更需要同步更新前端 TypeScript 类型。
"""

from v13.shared.schemas.contracts import (
    AlertRecord,
    ApiResponse,
    DataQualityTier,
    GreeksSource,
    HealthStatus,
    LeverageSnapshot,
    MacroSeries,
    OptionsChainSnapshot,
    SignalQuality,
    SkewSnapshot,
    VxnAlertRecord,
    VxnAlertSeverity,
)

__all__ = [
    "AlertRecord",
    "ApiResponse",
    "DataQualityTier",
    "GreeksSource",
    "HealthStatus",
    "LeverageSnapshot",
    "MacroSeries",
    "OptionsChainSnapshot",
    "SignalQuality",
    "SkewSnapshot",
    "VxnAlertRecord",
    "VxnAlertSeverity",
]
"""V1.3 共享 Schemas

跨微服务的契约层（quant-api-node ↔ quant-state-node ↔ quant-ui-node）。
任何字段变更需要同步更新前端 TypeScript 类型。
"""

from v13.shared.schemas.contracts import (
    AlertRecord,
    DataQualityTier,
    GreeksSource,
    LeverageSnapshot,
    MacroSeries,
    OptionsChainSnapshot,
    SignalQuality,
    SkewSnapshot,
    VxnAlertRecord,
    VxnAlertSeverity,
)

__all__ = [
    "AlertRecord",
    "DataQualityTier",
    "GreeksSource",
    "LeverageSnapshot",
    "MacroSeries",
    "OptionsChainSnapshot",
    "SignalQuality",
    "SkewSnapshot",
    "VxnAlertRecord",
    "VxnAlertSeverity",
]
