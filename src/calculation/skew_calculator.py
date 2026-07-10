"""
Skew 计算模块 (skew_calculator.py)

功能：
- 单标的 Skew 计算：Skew_spread = IV_Put_25Δ - IV_Call_25Δ
- 跨标的 Skew 剪刀差计算：如 QQQ Skew - SPY Skew
- 批量计算所有标的的 Skew 值
- v1.2.1: DTE空链保护 + skipped_result + 数据源追踪

核心概念：
    25Δ Risk Reversal（风险逆转）是衡量波动率偏斜的标准指标。
    Put Skew 越大，说明市场对下行保护的需求越强，
    支付的"恐慌溢价"越高，反映尾部风险定价的上升。
"""

from datetime import date
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import CROSS_ASSET_PAIRS
from src.calculation.data_cleaner import clean_option_chain, filter_by_dte, filter_missing_values
from src.calculation.delta_interpolator import DeltaIVInterpolator


# ── v1.2.1: 跳过结果辅助函数 ──
def skipped_result(
    ticker: str,
    as_of_date: date,
    reason: str,
    data_source: str = "unknown",
) -> dict[str, Any]:
    """构造标准化的"跳过"结果，防止 NaN 数据进入主快照。"""
    return {
        "ticker": ticker,
        "as_of_date": as_of_date.isoformat(),
        "status": "skipped",
        "skip_reason": reason,
        "data_source": data_source,
        "skew_spread": None,
        "iv_put_25d": None,
        "iv_call_25d": None,
        "put_data_points": 0,
        "call_data_points": 0,
        "put_is_extrapolated": False,
        "call_is_extrapolated": False,
        "error": reason,
    }


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
    """批量处理所有标的的期权链，计算各自的 Skew 值（v1.2.1: DTE空链保护）。

    完整流程:
        For each ticker:
            1. 清洗期权链数据
            2. DTE 严格过滤检查（空链则跳过）
            3. Put/Call 数据点充足性检查（<2 则跳过）
            4. Delta-IV 插值提取 25Δ IV
            5. 插值结果有效性检查（NaN 则跳过）
            6. 计算 Skew_spread
    """
    if interpolator is None:
        interpolator = DeltaIVInterpolator()

    results: dict[str, dict[str, Any]] = {}
    as_of_date = date.today()

    for ticker, snapshots in ticker_snapshots.items():
        try:
            logger.info(f"[{ticker}] 开始处理 Skew 计算")

            # 提取数据源标记
            data_source = _extract_data_source(snapshots)

            # Step 1: 清洗数据
            df = clean_option_chain(snapshots, ticker)
            if df.empty:
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason="清洗后数据为空",
                    data_source=data_source,
                )
                logger.warning(f"[{ticker}] 跳过: 清洗后数据为空")
                continue

            # ── v1.2.1: DTE 空链保护 ──
            dte_df = filter_by_dte(df)
            if dte_df.empty:
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason="no_expiry_in_dte_window",
                    data_source=data_source,
                )
                logger.warning(f"[{ticker}] 跳过: DTE [25,35] 窗口内无到期日")
                continue

            dte_df = filter_missing_values(dte_df)
            if dte_df.empty:
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason="all_records_filtered_by_quality",
                    data_source=data_source,
                )
                logger.warning(f"[{ticker}] 跳过: 质量过滤后无剩余记录")
                continue

            # ── v1.2.1: Put/Call 数据点充足性检查 ──
            put_count = int((dte_df["contract_type"] == "put").sum())
            call_count = int((dte_df["contract_type"] == "call").sum())

            if put_count < 2 or call_count < 2:
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason=f"insufficient_option_points: puts={put_count}, calls={call_count}",
                    data_source=data_source,
                )
                logger.warning(
                    f"[{ticker}] 跳过: 数据点不足 (Put={put_count}, Call={call_count})"
                )
                continue

            # Step 2: Delta 插值
            interp_result = interpolator.interpolate_with_confidence(dte_df)
            iv_put_25d = interp_result["iv_put_25d"]
            iv_call_25d = interp_result["iv_call_25d"]

            # ── v1.2.1: 插值有效性检查 ──
            if iv_put_25d is None or iv_call_25d is None:
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason="delta_interpolation_returned_none",
                    data_source=data_source,
                )
                logger.warning(f"[{ticker}] 跳过: 插值返回 None (Put={iv_put_25d}, Call={iv_call_25d})")
                continue

            if not np.isfinite(iv_put_25d) or not np.isfinite(iv_call_25d):
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason="delta_interpolation_returned_nan_or_inf",
                    data_source=data_source,
                )
                logger.warning(
                    f"[{ticker}] 跳过: 插值返回 NaN/Inf (Put={iv_put_25d}, Call={iv_call_25d})"
                )
                continue

            # Step 3: 计算 Skew
            skew = calculate_single_skew(iv_put_25d, iv_call_25d)

            if skew is None or not np.isfinite(skew):
                results[ticker] = skipped_result(
                    ticker, as_of_date,
                    reason="skew_calculation_failed",
                    data_source=data_source,
                )
                logger.warning(f"[{ticker}] 跳过: Skew 计算失败")
                continue

            results[ticker] = {
                "ticker": ticker,
                "as_of_date": as_of_date.isoformat(),
                "status": "ok",
                "skew_spread": skew,
                "iv_put_25d": iv_put_25d,
                "iv_call_25d": iv_call_25d,
                "put_data_points": interp_result["put_data_points"],
                "call_data_points": interp_result["call_data_points"],
                "put_is_extrapolated": interp_result["put_is_extrapolated"],
                "call_is_extrapolated": interp_result["call_is_extrapolated"],
                "data_source": data_source,
                "error": None,
            }

            logger.info(
                f"[{ticker}] Skew 计算完成: skew={skew}, "
                f"Put 25Δ IV={iv_put_25d}, Call 25Δ IV={iv_call_25d}"
                f"{' [yfinance+BSM]' if data_source == 'yfinance' else ''}"
            )

        except Exception as e:
            logger.error(f"[{ticker}] Skew 计算异常: {e}")
            results[ticker] = skipped_result(
                ticker, as_of_date,
                reason=f"exception: {type(e).__name__}",
                data_source="unknown",
            )

    # ── v1.2.1: 汇总跳过日志 ──
    skipped = {
        t: r for t, r in results.items()
        if r.get("status") == "skipped"
    }
    for ticker, result in skipped.items():
        logger.warning(
            f"[{ticker}] 本日未纳入风险快照: {result['skip_reason']}"
        )

    return results


def _extract_data_source(snapshots: list[dict[str, Any]]) -> str:
    """从快照列表中提取数据源标记（v1.2.1）。"""
    if not snapshots:
        return "unknown"
    # 取第一个快照的 _data_source 字段
    ds = snapshots[0].get("_data_source", "")
    if ds:
        return str(ds)
    # 如果 Polyon 快照中有 greeks.delta 非 None，判定为 polygon
    for snap in snapshots[:5]:
        greeks = snap.get("greeks", {}) or {}
        if greeks.get("delta") is not None:
            return "polygon"
    return "unknown"


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
