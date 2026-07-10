"""
宏观流动性与杠杆压力测试模块 (macro_leverage.py)

功能：
- 计算杠杆极限占比: Ratio = Margin_Debt / M2_Supply
- 动量反转计算 (MoM/YoY): 保证金债务总额的同比与环比变化
- 阈值判断: Ratio > 6.0% 且环比连续两个月萎缩或同比转负

数据来源:
    - Margin Debt: FINRA 保证金债务数据（需手工上传 CSV 或网页抓取）
    - M2 Supply: FRED API (M2SL 系列)

FINRA 保证金债务数据格式（CSV）:
    date, margin_debt
    2024-01-31, 700.5  (单位: 十亿美元)
    2024-02-29, 715.2
    ...
"""

from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import MACRO_LEVERAGE_RATIO_THRESHOLD


def load_margin_debt_csv(file_path: str | Path) -> pd.DataFrame:
    """
    加载 FINRA 保证金债务 CSV 文件。

    期望的 CSV 格式:
        date,margin_debt
        2024-01-31,700.5
        ...

    Args:
        file_path: CSV 文件路径

    Returns:
        DataFrame，列: [date, margin_debt]
    """
    df = pd.read_csv(file_path, parse_dates=["date"])
    df = df.sort_values("date")
    logger.info(f"加载保证金债务数据: {len(df)} 条记录, "
                f"日期范围: {df['date'].min().date()} -> {df['date'].max().date()}")
    return df


def calculate_leverage_ratio(
    margin_debt: float,
    m2_supply: float,
) -> float:
    """
    计算杠杆极限占比。

    公式:
        Ratio = Margin_Debt / M2_Supply

    解读:
        - 该比率反映市场投机杠杆相对于基础货币供应的比例
        - 历史极值约 6-7%，超过 6% 往往对应市场顶部
        - 比率从高位回落通常伴随市场调整

    Args:
        margin_debt: 保证金债务总额（十亿美元）
        m2_supply: M2 货币供应量（十亿美元）

    Returns:
        杠杆占比（小数形式，如 0.065 表示 6.5%）
    """
    if m2_supply <= 0:
        raise ValueError(f"M2 供应量必须为正值，当前值: {m2_supply}")
    return margin_debt / m2_supply


def calculate_momentum(
    df: pd.DataFrame,
    value_col: str = "margin_debt",
    date_col: str = "date",
) -> pd.DataFrame:
    """
    计算保证金债务的动量指标。

    计算指标:
        - MoM (环比): 本月值 / 上月值 - 1
        - YoY (同比): 本月值 / 去年同期值 - 1
        - MoM_change (环比变化量): 本月值 - 上月值
        - YoY_change (同比变化量): 本月值 - 去年同期值

    Args:
        df: 保证金债务 DataFrame（已按日期排序）
        value_col: 数值列名
        date_col: 日期列名

    Returns:
        增加 momentum 相关列的 DataFrame
    """
    result = df.copy()
    result = result.sort_values(date_col)

    # MoM: 月度环比
    result["mom_pct"] = result[value_col].pct_change(periods=1) * 100
    result["mom_change"] = result[value_col].diff(periods=1)

    # YoY: 年度同比（12个月）
    result["yoy_pct"] = result[value_col].pct_change(periods=12) * 100
    result["yoy_change"] = result[value_col].diff(periods=12)

    logger.debug(f"动量计算完成: {len(result)} 条记录")
    return result


def run_leverage_analysis(
    margin_debt_df: pd.DataFrame,
    m2_df: pd.DataFrame,
) -> dict[str, Any]:
    """
    运行完整的宏观流动性分析。

    流程:
        1. 对齐保证金债务与 M2 数据（按月）
        2. 计算杠杆占比 Ratio = Margin_Debt / M2
        3. 计算 MoM 和 YoY 动量
        4. 检查是否触发预警条件

    预警触发条件:
        1. 杠杆占比 Ratio > 6.0%
        2. 且同时满足以下任一:
           a. 连续两个月环比萎缩（MoM_change < 0 持续两月）
           b. 同比转负（YoY_pct < 0）

    Args:
        margin_debt_df: 保证金债务 DataFrame
        m2_df: M2 货币供应 DataFrame

    Returns:
        {
            "current_ratio": float,         # 当前杠杆占比
            "ratio_pct": float,             # 杠杆占比百分比
            "current_margin_debt": float,   # 当前保证金债务
            "current_m2": float,            # 当前 M2 供应量
            "mom_pct": float or None,       # 环比变化率 (%)
            "yoy_pct": float or None,       # 同比变化率 (%)
            "is_alert": bool,               # 是否触发预警
            "alert_reasons": list[str],      # 预警原因列表
            "historical_ratios": pd.DataFrame,  # 历史杠杆占比
            "max_historical_ratio": float,  # 历史最高杠杆占比
        }
    """
    # 按月对齐数据
    margin_debt_df = margin_debt_df.copy()
    m2_df = m2_df.copy()

    margin_debt_df["year_month"] = margin_debt_df["date"].dt.to_period("M")
    m2_df["year_month"] = m2_df["date"].dt.to_period("M")

    # 合并
    merged = pd.merge(
        margin_debt_df, m2_df,
        on="year_month",
        suffixes=("_margin", "_m2"),
    )
    merged = merged.sort_values("date_margin")

    if merged.empty:
        return {"error": "保证金债务与 M2 数据合并后为空，检查日期对齐"}

    # 计算杠杆占比
    merged["leverage_ratio"] = merged.apply(
        lambda row: calculate_leverage_ratio(row["margin_debt"], row["m2_supply"]),
        axis=1,
    )

    # 计算动量
    merged = calculate_momentum(merged, value_col="margin_debt", date_col="date_margin")
    merged = calculate_momentum(merged, value_col="leverage_ratio", date_col="date_margin")

    # 取最新一行
    latest = merged.iloc[-1]
    current_ratio = float(latest["leverage_ratio"])
    current_margin_debt = float(latest["margin_debt"])
    current_m2 = float(latest["m2_supply"])
    mom_pct = float(latest["mom_pct"]) if not np.isnan(latest["mom_pct"]) else None
    yoy_pct = float(latest["yoy_pct"]) if not np.isnan(latest["yoy_pct"]) else None
    max_historical_ratio = float(merged["leverage_ratio"].max())

    # 检查预警条件
    alert_reasons = []
    is_alert = False

    # 条件 1: Ratio > 6.0%
    if current_ratio > MACRO_LEVERAGE_RATIO_THRESHOLD / 100:
        alert_reasons.append(
            f"杠杆占比 ({current_ratio * 100:.2f}%) 超过阈值 "
            f"({MACRO_LEVERAGE_RATIO_THRESHOLD}%)"
        )
        is_alert = True
    else:
        alert_reasons.append(
            f"杠杆占比 ({current_ratio * 100:.2f}%) 未超过阈值"
        )

    # 条件 2a: 连续两个月环比萎缩
    if len(merged) >= 3:
        recent_mom_changes = merged["mom_change"].tail(2).values
        if all(c < 0 for c in recent_mom_changes if not np.isnan(c)):
            alert_reasons.append(
                f"连续两个月保证金债务环比萎缩"
            )
            is_alert = True

    # 条件 2b: 同比转负
    if yoy_pct is not None and yoy_pct < 0:
        alert_reasons.append(
            f"保证金债务同比转负 ({yoy_pct:.2f}%)"
        )
        is_alert = True

    result = {
        "current_ratio": round(current_ratio, 6),
        "ratio_pct": round(current_ratio * 100, 2),
        "current_margin_debt": current_margin_debt,
        "current_m2": current_m2,
        "mom_pct": round(mom_pct, 2) if mom_pct is not None else None,
        "yoy_pct": round(yoy_pct, 2) if yoy_pct is not None else None,
        "is_alert": is_alert,
        "alert_reasons": alert_reasons,
        "historical_ratios": merged[["date_margin", "leverage_ratio", "margin_debt", "m2_supply"]].copy(),
        "max_historical_ratio": round(max_historical_ratio * 100, 2),
    }

    logger.info(
        f"宏观杠杆分析完成: Ratio={current_ratio * 100:.2f}%, "
        f"预警={'是' if is_alert else '否'}"
    )

    return result
