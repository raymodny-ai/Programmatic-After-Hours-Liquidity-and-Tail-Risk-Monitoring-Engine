"""
Skew 计算模块 (skew_calculator.py)

功能：
- 单标的 Skew 计算：Skew_spread = IV_Put_25Δ - IV_Call_25Δ
- 跨标的 Skew 剪刀差计算：如 QQQ Skew - SPY Skew
- 批量计算所有标的的 Skew 值

核心概念：
    25Δ Risk Reversal（风险逆转）是衡量波动率偏斜的标准指标。
    Put Skew 越大，说明市场对下行保护的需求越强，
    支付的"恐慌溢价"越高，反映尾部风险定价的上升。
"""

from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import CROSS_ASSET_PAIRS
from src.calculation.data_cleaner import clean_option_chain
from src.calculation.delta_interpolator import DeltaIVInterpolator


def calculate_single_skew(iv_put_25d: float, iv_call_25d: float) -> Optional[float]:
    """
    计算单标的 Skew 值。

    公式: Skew_spread = IV_Put_25Δ - IV_Call_25Δ

    解释:
        - 正值 > 0: Put IV 高于 Call IV，市场偏向下行保护（正常状态）
        - 负值 < 0: Call IV 高于 Put IV，市场偏向上行投机（罕见）
        - 值越大: 下行保护需求越强，尾部风险担忧越高

    Args:
        iv_put_25d: 25Δ Put 的隐含波动率
        iv_call_25d: 25Δ Call 的隐含波动率

    Returns:
        Skew 值，如果任一 IV 为 NaN 则返回 None
    """
    if np.isnan(iv_put_25d) or np.isnan(iv_call_25d):
        return None
    return round(iv_put_25d - iv_call_25d, 6)


def calculate_cross_asset_skew_spread(
    skew_results: dict[str, float],
    pair: list[str],
) -> Optional[float]:
    """
    计算跨标的 Skew 剪刀差。

    公式: Skew_TickerA - Skew_TickerB

    示例:
        Skew_QQQ - Skew_SPY:
        - 差值为正且走阔: 科技股相对于全市场面临更大的下行保护需求
        - 差值为负: 全市场风险情绪高于科技股（罕见）

    Args:
        skew_results: {ticker: skew_value} 字典
        pair: [ticker_a, ticker_b] 列表

    Returns:
        剪刀差值，如果任一标的无有效 Skew 则返回 None
    """
    skew_a = skew_results.get(pair[0])
    skew_b = skew_results.get(pair[1])

    if skew_a is None or skew_b is None:
        logger.warning(f"跨标的 Skew 计算失败: {pair[0]}={skew_a}, {pair[1]}={skew_b}")
        return None

    return round(skew_a - skew_b, 6)


def process_all_tickers(
    ticker_snapshots: dict[str, list[dict[str, Any]]],
    interpolator: Optional[DeltaIVInterpolator] = None,
) -> dict[str, dict[str, Any]]:
    """
    批量处理所有标的的期权链，计算各自的 Skew 值。

    完整流程:
        For each ticker:
            1. 清洗期权链数据
            2. Delta-IV 插值提取 25Δ IV
            3. 计算 Skew_spread

    Args:
        ticker_snapshots: {ticker: [snapshot_dicts]} 字典
        interpolator: DeltaIVInterpolator 实例（可选）

    Returns:
        {
            ticker: {
                "ticker": str,
                "skew_spread": float or None,
                "iv_put_25d": float or None,
                "iv_call_25d": float or None,
                "put_data_points": int,
                "call_data_points": int,
                "put_is_extrapolated": bool,
                "call_is_extrapolated": bool,
                "error": str or None,
            }
        }
    """
    if interpolator is None:
        interpolator = DeltaIVInterpolator()

    results: dict[str, dict[str, Any]] = {}

    for ticker, snapshots in ticker_snapshots.items():
        try:
            logger.info(f"[{ticker}] 开始处理 Skew 计算")

            # Step 1: 清洗数据
            df = clean_option_chain(snapshots, ticker)
            if df.empty:
                results[ticker] = {
                    "ticker": ticker,
                    "skew_spread": None,
                    "iv_put_25d": None,
                    "iv_call_25d": None,
                    "error": "清洗后数据为空",
                }
                continue

            # Step 2: Delta 插值
            interp_result = interpolator.interpolate_with_confidence(df)
            iv_put_25d = interp_result["iv_put_25d"]
            iv_call_25d = interp_result["iv_call_25d"]

            # Step 3: 计算 Skew
            skew = None
            if iv_put_25d is not None and iv_call_25d is not None:
                skew = calculate_single_skew(iv_put_25d, iv_call_25d)

            results[ticker] = {
                "ticker": ticker,
                "skew_spread": skew,
                "iv_put_25d": iv_put_25d,
                "iv_call_25d": iv_call_25d,
                "put_data_points": interp_result["put_data_points"],
                "call_data_points": interp_result["call_data_points"],
                "put_is_extrapolated": interp_result["put_is_extrapolated"],
                "call_is_extrapolated": interp_result["call_is_extrapolated"],
                "error": None,
            }

            logger.info(
                f"[{ticker}] Skew 计算完成: skew={skew}, "
                f"Put 25Δ IV={iv_put_25d}, Call 25Δ IV={iv_call_25d}"
            )

        except Exception as e:
            logger.error(f"[{ticker}] Skew 计算异常: {e}")
            results[ticker] = {
                "ticker": ticker,
                "skew_spread": None,
                "iv_put_25d": None,
                "iv_call_25d": None,
                "error": str(e),
            }

    return results


def calculate_cross_asset_spreads(
    skew_results: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    计算预定义的所有跨标的 Skew 剪刀差。

    Args:
        skew_results: process_all_tickers() 的返回结果

    Returns:
        [
            {
                "pair": ["QQQ", "SPY"],
                "description": "科技股 vs 全市场",
                "spread": float or None,
            },
            ...
        ]
    """
    # 构建 {ticker: skew_value} 映射
    skew_map = {
        ticker: result.get("skew_spread")
        for ticker, result in skew_results.items()
    }

    cross_results = []
    for pair_config in CROSS_ASSET_PAIRS:
        pair = pair_config["pair"]
        spread = calculate_cross_asset_skew_spread(skew_map, pair)

        result = {
            "pair": pair,
            "description": pair_config.get("description", f"{pair[0]} vs {pair[1]}"),
            "spread": spread,
        }
        cross_results.append(result)

        if spread is not None:
            logger.info(
                f"跨标的 Skew 剪刀差 {pair[0]}-{pair[1]}: {spread:.4f}"
            )

    return cross_results
