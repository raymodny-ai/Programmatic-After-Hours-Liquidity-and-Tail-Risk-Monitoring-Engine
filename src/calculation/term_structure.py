"""
波动率期限结构计算模块 (term_structure.py)

功能：
- 计算 VIX/VXN 近月与次月期货价差
- 检测波动率期限结构是否发生倒挂（Inversion）
- 生成倒挂预警信号

核心概念：
    正常市场状态下，远月 VIX 期货价格高于近月（Contango/升水），
    因为不确定性随期限增加。当近月价格高于远月时（Backwardation/贴水），
    说明市场对短期风险极度恐慌，这是重要的尾部风险信号。
"""

from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger


def calculate_term_structure_spread(
    front_month_price: float,
    second_month_price: float,
) -> dict[str, Any]:
    """
    计算期货期限结构价差。

    Args:
        front_month_price: 近月期货结算价
        second_month_price: 次月期货结算价

    Returns:
        {
            "spread": float,         # 次月 - 近月 价差
            "is_inverted": bool,     # 是否倒挂
            "contango_pct": float,   # 升贴水百分比
            "status": str,           # "contango" | "flat" | "backwardation"
        }
    """
    spread = second_month_price - front_month_price
    is_inverted = front_month_price > second_month_price

    if front_month_price > 0:
        contango_pct = (spread / front_month_price) * 100
    else:
        contango_pct = 0.0

    # 状态分类
    if is_inverted:
        status = "backwardation"  # 倒挂 - 短期恐慌
    elif contango_pct < 1.0:
        status = "flat"           # 平坦 - 中性
    else:
        status = "contango"       # 升水 - 正常

    return {
        "spread": round(spread, 2),
        "is_inverted": is_inverted,
        "contango_pct": round(contango_pct, 2),
        "status": status,
    }


def analyze_term_structure_history(
    df: pd.DataFrame,
    lookback_days: int = 252,
) -> dict[str, Any]:
    """
    分析 VIX 期货期限结构历史数据，识别倒挂事件。

    Args:
        df: VIX 期货历史 DataFrame（来自 vix_client.fetch_vix_history()）
            须包含列: date, f1 (近月), f2 (次月)
        lookback_days: 回溯分析的天数

    Returns:
        {
            "total_days": int,             # 总交易日数
            "inversion_days": int,         # 倒挂天数
            "inversion_pct": float,        # 倒挂占比
            "current_status": dict,        # 当前状态
            "recent_inversions": list,     # 最近倒挂事件列表
        }
    """
    if df.empty or "f1" not in df.columns or "f2" not in df.columns:
        return {"error": "VIX 期货数据不完整", "total_days": 0}

    recent = df.tail(lookback_days).copy()
    recent["is_inverted"] = recent["f1"] > recent["f2"]
    recent["spread"] = recent["f2"] - recent["f1"]

    total_days = len(recent)
    inversion_days = recent["is_inverted"].sum()
    inversion_pct = (inversion_days / total_days * 100) if total_days > 0 else 0.0

    # 当前最新状态
    latest = recent.iloc[-1]
    current_status = calculate_term_structure_spread(
        float(latest["f1"]), float(latest["f2"])
    )

    # 识别倒挂事件段
    inversion_events = []
    in_event = False
    event_start = None

    for idx, row in recent.iterrows():
        if row["is_inverted"] and not in_event:
            event_start = row["date"]
            in_event = True
        elif not row["is_inverted"] and in_event:
            inversion_events.append({
                "start": str(event_start.date()),
                "end": str(row["date"].date()),
            })
            in_event = False

    if in_event:
        inversion_events.append({
            "start": str(event_start.date()),
            "end": str(latest["date"].date()),
        })

    logger.info(
        f"VIX 期限结构分析: 回溯 {total_days} 天, "
        f"倒挂 {inversion_days} 天 ({inversion_pct:.1f}%), "
        f"当前状态: {current_status['status']}"
    )

    return {
        "total_days": total_days,
        "inversion_days": int(inversion_days),
        "inversion_pct": round(inversion_pct, 2),
        "current_status": current_status,
        "recent_inversions": inversion_events[-5:],  # 最近5次倒挂事件
    }


def generate_term_structure_alert(
    current_status: dict[str, Any],
) -> dict[str, Any]:
    """
    根据当前期限结构状态生成预警信号。

    Args:
        current_status: calculate_term_structure_spread() 的返回

    Returns:
        {
            "alert_level": str,      # "normal" | "warning" | "critical"
            "alert_message": str,
            "recommended_action": str,
        }
    """
    status = current_status["status"]
    is_inverted = current_status["is_inverted"]
    spread = current_status["spread"]

    if status == "contango":
        return {
            "alert_level": "normal",
            "alert_message": f"VIX 期限结构正常 (升水 {current_status['contango_pct']:.1f}%)",
            "recommended_action": "维持常规监控",
        }
    elif status == "flat":
        return {
            "alert_level": "warning",
            "alert_message": f"VIX 期限结构平坦 (升水仅 {current_status['contango_pct']:.1f}%)，密切关注",
            "recommended_action": "提高监控频率，检查其他风险指标",
        }
    else:  # backwardation
        severity = "严重" if abs(spread) > 3 else "轻度"
        return {
            "alert_level": "critical",
            "alert_message": f"VIX 期限结构{severity}倒挂 (贴水 {abs(spread):.1f} 点)！短期恐慌情绪极强",
            "recommended_action": "发出尾部风险预警，建议评估组合风险敞口",
        }
