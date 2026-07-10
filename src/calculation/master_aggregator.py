"""
主数据帧汇总模块 (master_aggregator.py)

功能：
- 将所有标的的 Skew 计算结果汇总到统一 DataFrame
- 整合跨标的 Skew 剪刀差
- 输出标准化的每日风险快照 Parquet 文件

输出结构:
    date, ticker, skew_spread, iv_put_25d, iv_call_25d,
    z_score, alert_flag, alert_severity,
    vix_term_structure_status, macro_leverage_flag
"""

from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from src.calculation.risk_signals import check_all_ticker_alerts
from src.calculation.skew_calculator import calculate_cross_asset_spreads


def build_daily_risk_snapshot(
    skew_results: dict[str, dict[str, Any]],
    cross_asset_results: list[dict[str, Any]],
    historical_df: pd.DataFrame,
    term_structure_status: Optional[dict[str, Any]] = None,
    macro_leverage_flag: Optional[bool] = None,
    as_of_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    构建单日风险快照 DataFrame。

    Args:
        skew_results: process_all_tickers() 的返回结果
        cross_asset_results: calculate_cross_asset_spreads() 的返回结果
        historical_df: 历史 Skew 数据 DataFrame（用于 Z-Score 计算）
        term_structure_status: VIX 期限结构状态
        macro_leverage_flag: 宏观杠杆是否触发预警
        as_of_date: 快照日期（默认今天）

    Returns:
        标准化的每日风险快照 DataFrame
    """
    if as_of_date is None:
        as_of_date = date.today()

    # 提取当前 Skew 值
    current_skews = {
        ticker: result.get("skew_spread")
        for ticker, result in skew_results.items()
    }

    # 检查预警
    alerts = check_all_ticker_alerts(current_skews, historical_df)
    alert_map = {a["ticker"]: a for a in alerts}

    # 构建记录
    records = []
    for ticker, result in skew_results.items():
        # ── v1.2.1: 跳过 status="skipped" 的记录 ──
        if result.get("status") == "skipped":
            logger.info(f"[{ticker}] 已跳过，不纳入主快照: {result.get('skip_reason')}")
            continue

        alert_info = alert_map.get(ticker, {})

        record = {
            "date": as_of_date,
            "ticker": ticker,
            "skew_spread": result.get("skew_spread"),
            "iv_put_25d": result.get("iv_put_25d"),
            "iv_call_25d": result.get("iv_call_25d"),
            "z_score": alert_info.get("z_score", np.nan),
            "alert_flag": alert_info.get("is_alert", False),
            "alert_severity": alert_info.get("severity", "normal"),
            "alert_direction": alert_info.get("alert_direction"),
            "put_data_points": result.get("put_data_points", 0),
            "call_data_points": result.get("call_data_points", 0),
            "data_source": result.get("data_source", "unknown"),  # v1.2.1
            "error": result.get("error"),
        }
        records.append(record)

    # 添加跨标的剪刀差记录
    for cr in cross_asset_results:
        pair_name = f"{cr['pair'][0]}-{cr['pair'][1]}"
        record = {
            "date": as_of_date,
            "ticker": f"CROSS:{pair_name}",
            "skew_spread": cr.get("spread"),
            "iv_put_25d": None,
            "iv_call_25d": None,
            "z_score": np.nan,
            "alert_flag": False,
            "alert_severity": "normal",
            "alert_direction": None,
            "put_data_points": 0,
            "call_data_points": 0,
            "description": cr.get("description", ""),
            "error": None,
        }
        records.append(record)

    # 构建 DataFrame
    df = pd.DataFrame(records)

    # 可选：附加期限结构和宏观标志
    if term_structure_status:
        df["vix_term_structure_status"] = term_structure_status.get("status", "")
        df["vix_is_inverted"] = term_structure_status.get("is_inverted", False)

    if macro_leverage_flag is not None:
        df["macro_leverage_alert"] = macro_leverage_flag

    logger.info(f"每日风险快照构建完成: {len(df)} 条记录 (日期={as_of_date})")
    return df


def aggregate_results(
    skew_results: dict[str, dict[str, Any]],
    historical_df: pd.DataFrame,
    cross_asset_results: Optional[list[dict[str, Any]]] = None,
    term_structure_status: Optional[dict[str, Any]] = None,
    macro_leverage_result: Optional[dict[str, Any]] = None,
    volatility_regime: Optional[dict[str, Any]] = None,  # v1.2.1
    as_of_date: Optional[date] = None,
) -> dict[str, Any]:
    """
    汇总所有计算结果到一个结构化的字典中。

    这是一个完整流程的聚合函数，适合作为 main.py 的中间步骤。

    Returns:
        {
            "date": str,
            "ticker_results": dict,          # 每个标的的详细结果
            "cross_asset_spreads": list,     # 跨标的剪刀差
            "alerts": list,                  # 预警列表
            "term_structure": dict or None,  # 期限结构分析
            "macro_leverage": dict or None,  # 宏观杠杆分析
            "daily_snapshot_df": DataFrame,  # 标准化快照
        }
    """
    if as_of_date is None:
        as_of_date = date.today()

    if cross_asset_results is None:
        cross_asset_results = calculate_cross_asset_spreads(skew_results)

    # 构建每日快照
    snapshot_df = build_daily_risk_snapshot(
        skew_results=skew_results,
        cross_asset_results=cross_asset_results,
        historical_df=historical_df,
        term_structure_status=term_structure_status,
        macro_leverage_flag=(
            macro_leverage_result.get("is_alert", False)
            if macro_leverage_result else None
        ),
        as_of_date=as_of_date,
    )

    # 检查预警
    current_skews = {
        t: r.get("skew_spread") for t, r in skew_results.items()
    }
    alerts = check_all_ticker_alerts(current_skews, historical_df)

    result = {
        "date": as_of_date.isoformat(),
        "ticker_results": skew_results,
        "cross_asset_spreads": cross_asset_results,
        "alerts": alerts,
        "term_structure": term_structure_status,
        "macro_leverage": macro_leverage_result,
        "volatility_regime": volatility_regime,  # v1.2.1
        "daily_snapshot_df": snapshot_df,
    }

    # 汇总日志
    alert_count = len([a for a in alerts if a.get("is_alert")])
    logger.info(
        f"结果汇总完成: {len(skew_results)} 个标的, "
        f"{alert_count} 个预警, "
        f"{len(cross_asset_results)} 个跨标的剪刀差"
    )

    return result
