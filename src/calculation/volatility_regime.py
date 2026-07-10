"""
波动率状态分析模块 (volatility_regime.py) v1.2.1

功能：
- 对 VIX 或 VXN 单指数序列生成可审计的波动率状态信号
- 计算 VXN-VIX spread 的相对压力 Z-Score
- 不将 VXN 指数序列伪装为期货期限结构

核心信号:
    - VIX/VXN 水平 Z-Score: (当前值 - 滚动均值) / 滚动标准差
    - VIX/VXN 20日变化率: 短期动量
    - VXN-VIX spread: Nasdaq 科技板块 vs 全市场的波动率压力差异
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def compute_volatility_regime(
    index_df: pd.DataFrame,
    *,
    index_name: str,
    level_window: int = 252,
    momentum_window: int = 20,
    zscore_threshold: float = 2.0,
) -> dict[str, Any]:
    """对 VIX 或 VXN 单指数序列生成可审计的波动率状态信号。

    Args:
        index_df: 包含 date, close 列的 DataFrame
        index_name: 指数名称（"VIX" 或 "VXN"）
        level_window: 计算 Z-Score 的滚动窗口（交易日）
        momentum_window: 计算动量的窗口
        zscore_threshold: Z-Score 预警阈值

    Returns:
        {
            "index": str,
            "as_of_date": str,
            "status": "ok" | "unavailable" | "insufficient_history",
            "current_level": float,
            "rolling_mean": float,
            "rolling_std": float,
            "z_score": float,
            "change_20d": float | None,
            "is_alert": bool,
            "alert_level": "normal" | "elevated" | "critical",
        }
    """
    if index_df.empty or "close" not in index_df.columns:
        return {
            "index": index_name,
            "status": "unavailable",
            "reason": "empty_or_missing_close",
            "is_alert": False,
        }

    df = index_df.copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date")

    if len(df) < 20:
        return {
            "index": index_name,
            "status": "insufficient_history",
            "observations": len(df),
            "is_alert": False,
        }

    current = float(df["close"].iloc[-1])
    history = df["close"].tail(level_window)
    mean = float(history.mean())
    std = float(history.std(ddof=1))

    z_score = 0.0 if std == 0 or np.isnan(std) else (current - mean) / std

    if len(df) > momentum_window:
        prior = float(df["close"].iloc[-1 - momentum_window])
        change_20d = current / prior - 1.0 if prior > 0 else np.nan
    else:
        change_20d = np.nan

    alert_level = (
        "critical" if z_score >= 3.0
        else "elevated" if z_score >= zscore_threshold
        else "normal"
    )

    return {
        "index": index_name,
        "as_of_date": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "status": "ok",
        "current_level": round(current, 4),
        "rolling_mean": round(mean, 4),
        "rolling_std": round(std, 4),
        "z_score": round(float(z_score), 4),
        "change_20d": round(float(change_20d), 6)
        if pd.notna(change_20d)
        else None,
        "is_alert": bool(z_score >= zscore_threshold),
        "alert_level": alert_level,
    }


def compute_vxn_vix_spread(
    vxn_df: pd.DataFrame,
    vix_df: pd.DataFrame,
    *,
    window: int = 252,
    threshold: float = 2.0,
) -> dict[str, Any]:
    """计算 VXN - VIX 的相对压力 Z-Score。

    正值表示 Nasdaq 科技板块的波动率压力高于全市场。

    Args:
        vxn_df: VXN 指数 DataFrame (date, close)
        vix_df: VIX 指数 DataFrame (date, close)
        window: Z-Score 滚动窗口
        threshold: 预警阈值

    Returns:
        {
            "status": "ok" | "insufficient_history",
            "as_of_date": str,
            "spread": float,
            "z_score": float,
            "is_alert": bool,
            "interpretation": str,
        }
    """
    left = vxn_df[["date", "close"]].rename(columns={"close": "vxn"})
    right = vix_df[["date", "close"]].rename(columns={"close": "vix"})

    merged = (
        left.merge(right, on="date", how="inner")
        .dropna()
        .sort_values("date")
    )

    if len(merged) < 20:
        return {
            "status": "insufficient_history",
            "observations": len(merged),
            "is_alert": False,
        }

    merged["spread"] = merged["vxn"] - merged["vix"]
    series = merged["spread"].tail(window)

    current = float(series.iloc[-1])
    mean = float(series.mean())
    std = float(series.std(ddof=1))
    z_score = 0.0 if std == 0 else (current - mean) / std

    return {
        "status": "ok",
        "as_of_date": merged["date"].iloc[-1].strftime("%Y-%m-%d"),
        "spread": round(current, 4),
        "z_score": round(float(z_score), 4),
        "is_alert": bool(z_score >= threshold),
        "interpretation": (
            "Nasdaq implied-volatility pressure is materially above "
            "the broad-market volatility regime."
            if z_score >= threshold
            else "No relative Nasdaq volatility-pressure anomaly."
        ),
    }


def build_qqq_tail_risk_confirmation(
    qqq_skew_alert: dict | None,
    vxn_regime: dict | None,
    vxn_vix_signal: dict | None,
) -> dict[str, Any]:
    """QQQ 三因子联合确认预警（v1.2.1）。

    不建议仅凭 VXN 上升触发交易信号，而是把它作为 QQQ Skew 的确认条件。

    条件:
        - QQQ Skew Z-Score >= 2: QQQ 下行保护需求异常
        - VXN Z-Score >= 2: Nasdaq 波动率处于异常高位
        - VXN-VIX spread Z-Score >= 2: 科技板块压力强于宽基市场

    三者中任意两项成立 → 升级为 high/critical。

    Returns:
        {
            "signal": "QQQ_TAIL_RISK_CONFIRMATION",
            "components": {"qqq_skew": bool, "vxn_level": bool, "vxn_vix_relative": bool},
            "confirmation_score": int (0-3),
            "is_alert": bool,
            "severity": "normal" | "high" | "critical",
        }
    """
    flags = {
        "qqq_skew": bool(qqq_skew_alert and qqq_skew_alert.get("is_alert")),
        "vxn_level": bool(vxn_regime and vxn_regime.get("is_alert")),
        "vxn_vix_relative": bool(
            vxn_vix_signal and vxn_vix_signal.get("is_alert")
        ),
    }
    score = sum(flags.values())

    return {
        "signal": "QQQ_TAIL_RISK_CONFIRMATION",
        "components": flags,
        "confirmation_score": score,
        "is_alert": score >= 2,
        "severity": "critical" if score == 3 else "high" if score == 2 else "normal",
    }
