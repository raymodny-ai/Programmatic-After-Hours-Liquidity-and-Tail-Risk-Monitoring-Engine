"""
全局配置模块 (settings.py)

集中管理 API base URL、标的池列表、速率限制参数等所有配置项。
配置来源优先级：环境变量 > YAML 配置文件 > 默认值。
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# 项目根目录与路径计算
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
LOG_DIR = PROJECT_ROOT / "logs"

# 确保必要目录存在
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# 加载 .env 环境变量
# ---------------------------------------------------------------------------
load_dotenv(PROJECT_ROOT / ".env")


def _load_yaml_config(path: Path) -> dict[str, Any]:
    """加载 YAML 配置文件，文件不存在时返回空字典。"""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


# 加载 tickers.yaml
_tickers_config = _load_yaml_config(CONFIG_DIR / "tickers.yaml")

# ---------------------------------------------------------------------------
# API 密钥
# ---------------------------------------------------------------------------
POLYGON_API_KEY: str = os.getenv("POLYGON_API_KEY", "")
FRED_API_KEY: str = os.getenv("FRED_API_KEY", "")
GOOGLE_SERVICE_ACCOUNT_FILE: str = os.getenv(
    "GOOGLE_SERVICE_ACCOUNT_FILE", "credentials/google_service_account.json"
)
GOOGLE_SPREADSHEET_ID: str = os.getenv("GOOGLE_SPREADSHEET_ID", "")

# ---------------------------------------------------------------------------
# VIX 数据源
# ---------------------------------------------------------------------------
VIX_DATA_SOURCE: str = os.getenv("VIX_DATA_SOURCE", "vix_central")

# ---------------------------------------------------------------------------
# 日志配置
# ---------------------------------------------------------------------------
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR_STR: str = os.getenv("LOG_DIR", str(LOG_DIR))

# ---------------------------------------------------------------------------
# 速率限制与重试配置
# ---------------------------------------------------------------------------
RATE_LIMIT_CALLS_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_CALLS_PER_MINUTE", "4"))
MAX_RETRIES: int = int(os.getenv("MAX_RETRIES", "5"))
RETRY_BACKOFF_INITIAL: float = float(os.getenv("RETRY_BACKOFF_INITIAL", "2"))

# ---------------------------------------------------------------------------
# 监控标的池（从 YAML 加载）
# ---------------------------------------------------------------------------
TARGET_TICKERS: list[dict[str, str]] = _tickers_config.get("target_tickers", [
    {"symbol": "SPY", "name": "标普500 ETF", "category": "large_cap"},
    {"symbol": "QQQ", "name": "纳斯达克100 ETF", "category": "tech_growth"},
    {"symbol": "IWM", "name": "罗素2000 ETF", "category": "small_cap"},
    {"symbol": "DIA", "name": "道琼斯工业平均指数 ETF", "category": "blue_chip"},
])

# 纯符号列表，便于批量轮询
TARGET_SYMBOLS: list[str] = [t["symbol"] for t in TARGET_TICKERS]

# ---------------------------------------------------------------------------
# 跨标的 Skew 剪刀差对
# ---------------------------------------------------------------------------
CROSS_ASSET_PAIRS: list[dict] = _tickers_config.get("cross_asset_pairs", [
    {"pair": ["QQQ", "SPY"], "description": "科技股 vs 全市场"},
])

# ---------------------------------------------------------------------------
# 波动率指数映射
# ---------------------------------------------------------------------------
VOLATILITY_INDEX_MAPPING: dict[str, str] = _tickers_config.get(
    "volatility_index_mapping", {"SPY": "VIX", "QQQ": "VXN"}
)

# ---------------------------------------------------------------------------
# 期权筛选参数
# ---------------------------------------------------------------------------
_options_filter = _tickers_config.get("options_filter", {})
TARGET_DTE: int = _options_filter.get("target_dte", 30)
DTE_TOLERANCE: int = _options_filter.get("dte_tolerance", 5)
MIN_VOLUME: int = _options_filter.get("min_volume", 1)
MAX_BID_ASK_SPREAD_PCT: float = _options_filter.get("max_bid_ask_spread_pct", 0.5)

# ---------------------------------------------------------------------------
# Delta 插值参数
# ---------------------------------------------------------------------------
_delta_cfg = _tickers_config.get("delta_interpolation", {})
TARGET_DELTA_PUT: float = _delta_cfg.get("target_delta_put", 0.25)
TARGET_DELTA_CALL: float = _delta_cfg.get("target_delta_call", 0.25)
INTERPOLATION_METHOD: str = _delta_cfg.get("interpolation_method", "cubic_spline")

# ---------------------------------------------------------------------------
# 风险预警阈值
# ---------------------------------------------------------------------------
_risk_cfg = _tickers_config.get("risk_alert", {})
SKEW_ZSCORE_WINDOW: int = _risk_cfg.get("skew_zscore_window", 90)
SKEW_ZSCORE_THRESHOLD: float = _risk_cfg.get("skew_zscore_threshold", 2.0)
MACRO_LEVERAGE_RATIO_THRESHOLD: float = _risk_cfg.get("macro_leverage_ratio_threshold", 6.0)

# ---------------------------------------------------------------------------
# API 端点定义
# ---------------------------------------------------------------------------
POLYGON_BASE_URL: str = "https://api.polygon.io"
FRED_BASE_URL: str = "https://api.stlouisfed.org/fred"

# Cboe VIX 期货结算价历史 CSV（公开数据）
CBOE_VIX_FUTURES_URL: str = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)


def validate_config() -> list[str]:
    """
    验证关键配置项是否已正确设置。
    返回缺失/无效配置的警告信息列表。
    """
    warnings: list[str] = []

    if not POLYGON_API_KEY or POLYGON_API_KEY == "your_polygon_api_key_here":
        warnings.append("POLYGON_API_KEY 未设置，模块一（期权数据）将无法工作")

    if not FRED_API_KEY or FRED_API_KEY == "your_fred_api_key_here":
        warnings.append("FRED_API_KEY 未设置，模块二（M2数据）将无法工作")

    if not GOOGLE_SPREADSHEET_ID or GOOGLE_SPREADSHEET_ID == "your_spreadsheet_id_here":
        warnings.append("GOOGLE_SPREADSHEET_ID 未设置，模块三（Sheets推送）将无法工作")

    return warnings
