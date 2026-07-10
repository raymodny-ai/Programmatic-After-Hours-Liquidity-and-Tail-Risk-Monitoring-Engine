"""
跨标的 Skew 剪刀差统计检验模块 (cross_asset_signals.py)

功能:
- 对 QQQ-SPY 等跨标的剪刀差序列计算滚动 Z-Score
- 提供独立的统计显著性预警判断和语义解释
- 补充原有 skew_calculator 仅计算差值而无统计检验的不足

核心逻辑:
    Z_Score = (current_spread - rolling_mean) / rolling_std
    
    当 |Z_Score| >= 阈值时触发预警，并针对不同对给出金融语义解释。
"""

from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import SKEW_ZSCORE_WINDOW, SKEW_ZSCORE_THRESHOLD


def compute_cross_asset_zscore(
    historical_df: pd.DataFrame,
    current_spread: float,
    pair_name: str,
    window: int = SKEW_ZSCORE_WINDOW,
) -> dict[str, Any]:
    """
    计算跨标的剪刀差的滚动 Z-Score。

    Args:
        historical_df: 历史主数据帧（含 CROSS: 前缀的行）
        current_spread: 当日剪刀差值
        pair_name: 如 "QQQ-SPY"
        window: 滚动窗口大小（交易日）

    Returns:
        {
            "pair": str,
            "current_spread": float,
            "z_score": float,
            "rolling_mean": float,
            "rolling_std": float,
            "window_size": int,
            "actual_data_points": int,
            "is_alert": bool,
            "alert_level": str,
            "interpretation": str,
        }
    """
    ticker_label = f"CROSS:{pair_name}"
    history = pd.DataFrame()

    if not historical_df.empty and "ticker" in historical_df.columns:
        sub = historical_df[historical_df["ticker"] == ticker_label].copy()
        if "skew_spread" in sub.columns:
            history = sub[["date", "skew_spread"]].dropna().sort_values("date")

    # 数据不足时返回不可靠标记
    if len(history) < 10:
        logger.warning(
            f"跨标的 {pair_name} 历史数据不足 ({len(history)} 条 < 10)，"
            "Z-Score 估计不可靠"
        )
        return {
            "pair": pair_name,
            "current_spread": current_spread,
            "z_score": np.nan if len(history) < 2 else _quick_zscore(history["skew_spread"], current_spread),
            "rolling_mean": float(history["skew_spread"].mean()) if not history.empty else np.nan,
            "rolling_std": float(history["skew_spread"].std(ddof=1)) if len(history) >= 2 else np.nan,
            "window_size": window,
            "actual_data_points": len(history),
            "is_alert": False,
            "alert_level": "unknown",
            "interpretation": f"历史数据不足 ({len(history)} 条记录)，无法进行可靠的统计检验",
        }

    recent = history.tail(window)["skew_spread"]
    rolling_mean = float(recent.mean())
    rolling_std = float(recent.std(ddof=1))

    if rolling_std == 0 or np.isnan(rolling_std):
        z_score = 0.0
    else:
        z_score = (current_spread - rolling_mean) / rolling_std

    is_alert = abs(z_score) >= SKEW_ZSCORE_THRESHOLD

    # 严重度分级
    abs_z = abs(z_score)
    if abs_z < SKEW_ZSCORE_THRESHOLD:
        alert_level = "normal"
    elif abs_z < SKEW_ZSCORE_THRESHOLD * 1.5:
        alert_level = "elevated"
    elif abs_z < SKEW_ZSCORE_THRESHOLD * 2.0:
        alert_level = "high"
    else:
        alert_level = "extreme"

    # 金融语义解释
    interpretation = _generate_interpretation(pair_name, z_score, abs_z)

    if is_alert:
        logger.warning(
            f"[跨标的预警] {pair_name}: spread={current_spread:.4f}, "
            f"Z={z_score:.2f}, 级别={alert_level} | {interpretation}"
        )

    return {
        "pair": pair_name,
        "current_spread": current_spread,
        "z_score": round(z_score, 4),
        "rolling_mean": round(rolling_mean, 6),
        "rolling_std": round(rolling_std, 6),
        "window_size": window,
        "actual_data_points": len(recent),
        "is_alert": is_alert,
        "alert_level": alert_level,
        "interpretation": interpretation,
    }


def _quick_zscore(series: pd.Series, current_value: float) -> float:
    """快速计算 Z-Score（数据不足时的回退方案）。"""
    if len(series) < 2:
        return np.nan
    mean = float(series.mean())
    std = float(series.std(ddof=1))
    if std == 0:
        return 0.0
    return (current_value - mean) / std


def _generate_interpretation(pair_name: str, z_score: float, abs_z: float) -> str:
    """
    根据跨标的对和 Z-Score 方向生成金融语义解释。

    主要覆盖:
        - QQQ-SPY: 科技股 vs 全市场（PRD 中明确定义的监控对）
        - 其他: 通用解释模板
    """
    direction = "显著高于" if z_score > 0 else "显著低于"

    if pair_name == "QQQ-SPY" and z_score > SKEW_ZSCORE_THRESHOLD:
        return (
            f"科技股下行保护需求{direction}全市场基准 (Z={z_score:.2f}σ)，"
            "资金集中对冲科技板块去杠杆风险"
        )
    elif pair_name == "QQQ-SPY" and z_score < -SKEW_ZSCORE_THRESHOLD:
        return (
            f"全市场保护需求异常高于科技股 (Z={z_score:.2f}σ)，"
            "宽基系统性压力大于科技板块"
        )
    elif abs_z > SKEW_ZSCORE_THRESHOLD * 2.0:
        return (
            f"{pair_name} 剪刀差{direction}历史均值 {abs_z:.1f}σ，"
            "极端偏离，需警惕结构性风险转移"
        )
    elif abs_z > SKEW_ZSCORE_THRESHOLD:
        return (
            f"{pair_name} 剪刀差{direction}历史均值 {abs_z:.1f}σ，"
            "存在统计显著性偏离"
        )
    else:
        return f"{pair_name} 剪刀差处于正常波动范围内 (Z={z_score:.2f})"


def check_all_cross_asset_alerts(
    cross_asset_results: list[dict[str, Any]],
    historical_df: pd.DataFrame,
) -> list[dict[str, Any]]:
    """
    对所有跨标的对进行统计检验，返回触发预警的对列表。

    Args:
        cross_asset_results: calculate_cross_asset_spreads() 的返回结果
        historical_df: 历史主数据帧（用于计算滚动统计量）

    Returns:
        预警列表（只包含触发预警的项），按 |Z-Score| 降序排列
    """
    alerts = []

    for cr in cross_asset_results:
        pair = cr.get("pair", [])
        spread = cr.get("spread")

        if spread is None:
            continue
        if len(pair) < 2:
            continue

        pair_name = f"{pair[0]}-{pair[1]}"
        result = compute_cross_asset_zscore(historical_df, spread, pair_name)

        if result.get("is_alert"):
            alerts.append(result)

    # 按严重度排序
    severity_order = {"extreme": 0, "high": 1, "elevated": 2, "normal": 3, "unknown": 4}
    alerts.sort(key=lambda x: severity_order.get(x.get("alert_level", "unknown"), 99))

    if alerts:
        logger.warning(
            f"跨标的统计检验: {len(alerts)} 个对触发统计显著性异常"
        )

    return alerts
