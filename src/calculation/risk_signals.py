"""
风险预警信号模块 (risk_signals.py)

功能：
- 基于滚动窗口计算 Skew 的 Z-Score
- 检测当前值是否突破 +2σ 阈值
- 生成结构化的预警信号输出

核心算法：
    1. 加载历史 Skew 数据（从 Parquet 主数据帧）
    2. 计算过去 N 个交易日的滚动均值和标准差
    3. Z-Score = (当前值 - 滚动均值) / 滚动标准差
    4. 判断 |Z-Score| > 阈值 → 触发预警
"""

from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import SKEW_ZSCORE_WINDOW, SKEW_ZSCORE_THRESHOLD


def compute_rolling_zscore(
    historical_df: pd.DataFrame,
    current_value: float,
    window: int = SKEW_ZSCORE_WINDOW,
    value_column: str = "skew_spread",
    ticker_column: str = "ticker",
) -> dict[str, Any]:
    """
    计算 Skew 的滚动 Z-Score。

    Args:
        historical_df: 历史数据 DataFrame，必须包含 value_column 列
        current_value: 当前观测值
        window: 滚动窗口大小（交易日数）
        value_column: 数值列名
        ticker_column: 标的列名（如按标的分组计算）

    Returns:
        {
            "z_score": float,
            "rolling_mean": float,
            "rolling_std": float,
            "current_value": float,
            "window_size": int,
            "is_alert": bool,
            "alert_direction": str or None,  # "above" | "below"
        }
    """
    if historical_df.empty or value_column not in historical_df.columns:
        logger.warning("历史数据为空或无有效列，无法计算 Z-Score")
        return {
            "z_score": np.nan,
            "rolling_mean": np.nan,
            "rolling_std": np.nan,
            "current_value": current_value,
            "window_size": window,
            "is_alert": False,
            "alert_direction": None,
        }

    # 取最近 window 天的数据
    recent = historical_df.tail(window)[value_column].dropna()

    if len(recent) < 10:
        logger.warning(f"历史数据点不足 ({len(recent)} < 10)，Z-Score 估算不可靠")
        if len(recent) < 2:
            return {
                "z_score": np.nan,
                "rolling_mean": np.nan,
                "rolling_std": np.nan,
                "current_value": current_value,
                "window_size": window,
                "is_alert": False,
                "alert_direction": None,
            }

    rolling_mean = float(recent.mean())
    rolling_std = float(recent.std(ddof=1))

    if rolling_std == 0 or np.isnan(rolling_std):
        z_score = 0.0
    else:
        z_score = (current_value - rolling_mean) / rolling_std

    # 判断是否触发预警
    is_alert = abs(z_score) >= SKEW_ZSCORE_THRESHOLD
    alert_direction = None
    if is_alert:
        alert_direction = "above" if z_score > 0 else "below"

    return {
        "z_score": round(z_score, 4),
        "rolling_mean": round(rolling_mean, 6),
        "rolling_std": round(rolling_std, 6),
        "current_value": current_value,
        "window_size": window,
        "actual_data_points": len(recent),
        "is_alert": is_alert,
        "alert_direction": alert_direction,
    }


def check_all_ticker_alerts(
    current_skews: dict[str, Optional[float]],
    historical_df: pd.DataFrame,
    window: int = SKEW_ZSCORE_WINDOW,
    threshold: float = SKEW_ZSCORE_THRESHOLD,
) -> list[dict[str, Any]]:
    """
    检查所有标的的 Skew 是否触发预警。

    Args:
        current_skews: {ticker: skew_value} 当前 Skew 值
        historical_df: 历史数据 DataFrame
        window: 滚动窗口大小
        threshold: Z-Score 阈值

    Returns:
        预警列表，按严重程度降序排列
    """
    alerts = []

    for ticker, skew_value in current_skews.items():
        if skew_value is None:
            continue

        # 筛选该标的的历史数据
        ticker_history = historical_df[historical_df["ticker"] == ticker].copy()
        ticker_history = ticker_history.sort_values("date")

        result = compute_rolling_zscore(
            ticker_history,
            skew_value,
            window=window,
            value_column="skew_spread",
        )

        alert_info = {
            "ticker": ticker,
            "skew_value": skew_value,
            "z_score": result["z_score"],
            "rolling_mean": result["rolling_mean"],
            "rolling_std": result["rolling_std"],
            "is_alert": result["is_alert"],
            "alert_direction": result["alert_direction"],
            "threshold": threshold,
            "window": window,
            "severity": _classify_severity(result["z_score"], threshold),
        }

        if result["is_alert"]:
            alerts.append(alert_info)
            logger.warning(
                f"[预警] {ticker}: Skew={skew_value:.4f}, "
                f"Z-Score={result['z_score']:.2f} (阈值={threshold}), "
                f"方向={result['alert_direction']}"
            )

    # 按 Z-Score 绝对值降序排列（最严重的排前面）
    alerts.sort(key=lambda x: abs(x["z_score"]), reverse=True)
    return alerts


def _classify_severity(z_score: float, threshold: float) -> str:
    """
    根据 Z-Score 超出阈值的程度分级。

    Args:
        z_score: Z-Score 值
        threshold: 阈值

    Returns:
        "normal" | "elevated" | "high" | "extreme"
    """
    if np.isnan(z_score):
        return "unknown"

    abs_z = abs(z_score)

    if abs_z < threshold:
        return "normal"
    elif abs_z < threshold * 1.5:
        return "elevated"
    elif abs_z < threshold * 2.0:
        return "high"
    else:
        return "extreme"


def format_alert_summary(alerts: list[dict[str, Any]]) -> str:
    """
    将预警列表格式化为可读的摘要字符串。

    Args:
        alerts: check_all_ticker_alerts() 的返回结果

    Returns:
        格式化的预警摘要文本
    """
    if not alerts:
        return "所有标的的 Skew 值处于正常范围内，未触发预警。"

    lines = [f"共 {len(alerts)} 个标的触发预警:\n"]
    for a in alerts:
        lines.append(
            f"  [{a['severity'].upper()}] {a['ticker']}: "
            f"Skew={a['skew_value']:.4f}, "
            f"Z-Score={a['z_score']:.2f} (均值={a['rolling_mean']:.4f}, "
            f"σ={a['rolling_std']:.4f})"
        )
    return "\n".join(lines)
