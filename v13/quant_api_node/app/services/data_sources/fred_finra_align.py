"""FRED M2 + FINRA Margin Debt 自动对齐 (V1.3 阶段 2.3)。

冲突场景：
- FRED M2  发布频率：周更（周四），可能延迟 1~2 周
- FINRA Margin Debt 发布频率：月更（次月 25 日前后）

策略：
1. 各自按发布日期升序排列
2. 对齐为月度时间轴（Month-End）
3. Forward-Fill 空白月
4. 比对同期增量，计算 YoY / MoM
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


def align_macro_series(
    m2_series: list[dict[str, Any]],
    margin_series: list[dict[str, Any]],
) -> dict[str, Any]:
    """对齐两份宏观时间序列到月度。

    Args:
        m2_series: [{"date": "2026-06-01", "value": 22000.0}, ...] 周度
        margin_series: [{"date": "2026-05-25", "value": 850.0}, ...] 月度

    Returns:
        {
            "months": ["2026-04", "2026-05", "2026-06", ...],
            "m2_by_month": {month: value},
            "margin_by_month": {month: value},
            "ratio_by_month": {month: ratio},     // margin / m2 * 100
            "as_of": "2026-06"
        }
    """
    m2_monthly = _resample_to_month_end(m2_series)
    margin_monthly = _resample_to_month_end(margin_series)

    all_months = sorted(set(m2_monthly) | set(margin_monthly))
    if not all_months:
        return {"months": [], "m2_by_month": {}, "margin_by_month": {}, "ratio_by_month": {}, "as_of": None}

    # Forward-fill: 自最新向过去回填
    m2_filled = _forward_fill(m2_monthly, all_months)
    margin_filled = _forward_fill(margin_monthly, all_months)

    ratio: dict[str, float | None] = {}
    for m in all_months:
        m2_v = m2_filled.get(m)
        margin_v = margin_filled.get(m)
        if m2_v and margin_v and m2_v > 0:
            ratio[m] = round(margin_v / m2_v * 100, 4)
        else:
            ratio[m] = None

    return {
        "months": all_months,
        "m2_by_month": m2_filled,
        "margin_by_month": margin_filled,
        "ratio_by_month": ratio,
        "as_of": all_months[-1],
    }


def compute_leverage_metrics(
    series: dict[str, Any],
    window_months: int = 36,
) -> dict[str, Any]:
    """由对齐后的月度序列计算杠杆指标。

    返回::

        {
            "as_of": "2026-06",
            "ratio": 3.8,           # 当下比例
            "ratio_yoy": 0.10,      # YoY 变化
            "ratio_3m_momentum": 0.05,
            "momentum_reversal": False   # 3 月环比 vs 前 3 月环比的符号反转
        }
    """
    months = series.get("months") or []
    ratio = series.get("ratio_by_month") or {}
    if not months:
        return {"as_of": None}

    as_of = months[-1]
    cur = ratio.get(as_of)
    if cur is None:
        return {"as_of": as_of}

    # YoY（12 个月前）
    yoy_idx = max(0, len(months) - 13)
    yoy_val = ratio.get(months[yoy_idx])
    ratio_yoy = round(cur - yoy_val, 4) if yoy_val is not None else None

    # 3 月环比动量
    prev_3m_idx = max(0, len(months) - 4)
    prev_3m_idx_2 = max(0, len(months) - 7)
    if len(months) >= 4:
        now_3m = cur - (ratio.get(months[-4]) or cur)
    else:
        now_3m = 0
    if len(months) >= 7:
        prev_3m = (ratio.get(months[-4]) or cur) - (ratio.get(months[-7]) or cur)
    else:
        prev_3m = 0

    # 动量反转：两个 3 月环比的符号相反且绝对值都 > 0
    momentum_reversal = (now_3m * prev_3m) < 0 and abs(now_3m) > 0.005 and abs(prev_3m) > 0.005

    return {
        "as_of": as_of,
        "ratio": cur,
        "ratio_yoy": ratio_yoy,
        "ratio_3m_momentum": round(now_3m, 4),
        "prev_3m_momentum": round(prev_3m, 4),
        "momentum_reversal": momentum_reversal,
    }


def _resample_to_month_end(series: list[dict[str, Any]]) -> dict[str, float | None]:
    """weekly/daily → month-end: 取每月最后一条。"""
    bucket: dict[str, float | None] = {}
    sorted_series = sorted(series, key=lambda x: x["date"])
    for entry in sorted_series:
        d = entry["date"][:7]  # 'YYYY-MM'
        v = entry.get("value")
        if v is not None and not (isinstance(v, float) and math.isnan(v)):
            bucket[d] = v  # 覆盖为最后一条
    return bucket


def _forward_fill(source: dict[str, float | None], full_months: list[str]) -> dict[str, float | None]:
    """对全月份列表做 forward fill。"""
    result: dict[str, float | None] = {}
    last_value = None
    for m in full_months:
        v = source.get(m)
        if v is not None:
            last_value = v
        result[m] = last_value
    return result
