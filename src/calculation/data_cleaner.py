"""
数据清洗模块 (data_cleaner.py)

功能：
- 从 Polygon.io 快照 JSON 解析为结构化 pandas DataFrame
- 剔除期权链中 Bid/Ask 价差异常的脏数据
- DTE 过滤器：筛选距离到期日约 30 天的期权链
- 缺失值处理：标记或剔除 Delta/IV 缺失的行
"""

from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import (
    MAX_BID_ASK_SPREAD_PCT,
    TARGET_DTE,
    DTE_TOLERANCE,
    MIN_VOLUME,
)


def parse_snapshots_to_dataframe(
    snapshots: list[dict[str, Any]],
    ticker: str,
    as_of_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    将 Polygon.io 期权快照 JSON 解析为标准化 DataFrame。

    Polygon 快照 JSON 结构:
    {
        "ticker": "O:SPY240119C00450000",
        "details": {
            "contract_type": "call" | "put",
            "strike_price": 450.0,
            "expiration_date": "2024-01-19",
            "shares_per_contract": 100,
        },
        "greeks": {"delta": 0.65, "gamma": 0.01, "theta": -0.05, "vega": 0.12},
        "implied_volatility": 0.18,
        "day": {"open": 5.0, "high": 5.5, "low": 4.5, "close": 5.2, "volume": 12345},
    }

    Returns:
        DataFrame，列:
        - ticker, underlying, contract_type, strike, expiration_date
        - delta, gamma, theta, vega, implied_volatility
        - bid, ask, last_price, volume, open_interest (如果有)
        - dte (距离到期天数)
        - bid_ask_spread_pct (价差比例)
    """
    if not snapshots:
        logger.warning(f"[{ticker}] 快照列表为空")
        return pd.DataFrame()

    records = []
    for snap in snapshots:
        try:
            details = snap.get("details", {})
            greeks = snap.get("greeks", {})
            day_data = snap.get("day", {})

            # 提取合约类型和行权价
            contract_type = details.get("contract_type", "").lower()
            strike = details.get("strike_price")
            expiration_str = details.get("expiration_date")

            if not contract_type or strike is None or not expiration_str:
                continue

            expiration_date = datetime.strptime(expiration_str, "%Y-%m-%d").date()
            today = as_of_date or date.today()
            dte = (expiration_date - today).days

            # 计算 Bid/Ask 价差
            bid = day_data.get("open", np.nan)  # Polygon snapshot day.open 可能是 bid
            ask = day_data.get("high", np.nan)  # 近似处理，实际 bid/ask 需通过 quotes 端点
            last_price = day_data.get("close", np.nan)
            mid_price = (bid + ask) / 2 if bid and ask else last_price
            bid_ask_spread_pct = (
                abs(ask - bid) / mid_price if mid_price and mid_price > 0 else np.nan
            )

            records.append({
                "snapshot_ticker": snap.get("ticker", ""),
                "underlying": ticker,
                "contract_type": contract_type,
                "strike": strike,
                "expiration_date": expiration_date,
                "dte": dte,
                "delta": greeks.get("delta", np.nan) if greeks else np.nan,
                "gamma": greeks.get("gamma", np.nan) if greeks else np.nan,
                "theta": greeks.get("theta", np.nan) if greeks else np.nan,
                "vega": greeks.get("vega", np.nan) if greeks else np.nan,
                "implied_volatility": snap.get("implied_volatility", np.nan),
                "bid": bid,
                "ask": ask,
                "last_price": last_price,
                "volume": day_data.get("volume", 0),
                "bid_ask_spread_pct": bid_ask_spread_pct,
            })
        except Exception as e:
            logger.debug(f"解析快照记录失败: {e}")
            continue

    df = pd.DataFrame(records)

    if df.empty:
        logger.warning(f"[{ticker}] 解析后无有效记录")
        return df

    # Delta 取绝对值（Put 在 Polygon API 中可能为负值）
    df["delta_abs"] = df["delta"].abs()

    logger.info(f"[{ticker}] 解析完成: {len(df)} 条记录 (call={len(df[df.contract_type=='call'])}, put={len(df[df.contract_type=='put'])})")
    return df


def filter_bid_ask_outliers(
    df: pd.DataFrame,
    max_spread_pct: float = MAX_BID_ASK_SPREAD_PCT,
) -> pd.DataFrame:
    """
    剔除 Bid/Ask 价差异常的脏数据。

    筛选逻辑:
        (Ask - Bid) / Mid > max_spread_pct 的记录视为无流动性，予以剔除。

    同时也会剔除 bid_ask_spread_pct 为 NaN 的记录
    （即无法计算价差的记录）。

    Args:
        df: 原始期权数据 DataFrame
        max_spread_pct: 最大价差比例阈值（相对于中间价），默认 0.5 (50%)

    Returns:
        清洗后的 DataFrame
    """
    before = len(df)

    # 剔除无法计算价差的记录
    df = df.dropna(subset=["bid_ask_spread_pct"])

    # 剔除价差异常的记录
    df = df[df["bid_ask_spread_pct"] <= max_spread_pct]

    removed = before - len(df)
    if removed > 0:
        logger.info(f"Bid/Ask 过滤器: 剔除了 {removed} 条异常记录 ({removed / before * 100:.1f}%)")

    return df


def filter_by_dte(
    df: pd.DataFrame,
    target_dte: int = TARGET_DTE,
    tolerance: int = DTE_TOLERANCE,
) -> pd.DataFrame:
    """
    筛选距离到期日约 target_dte 天的期权链。

    如果存在多个到期日，优先选择 DTE 最接近目标的那个。

    Args:
        df: 期权数据 DataFrame（须包含 dte 列）
        target_dte: 目标到期天数
        tolerance: 容差范围（±天），在此范围内的到期日都视为候选

    Returns:
        筛选后的 DataFrame，仅包含最接近目标 DTE 的到期日的期权
    """
    if "dte" not in df.columns:
        logger.warning("DataFrame 缺少 dte 列，跳过 DTE 过滤")
        return df

    # 找出所有在容差范围内的 DTE
    dt_min = target_dte - tolerance
    dt_max = target_dte + tolerance

    candidate_df = df[(df["dte"] >= dt_min) & (df["dte"] <= dt_max)]

    if candidate_df.empty:
        # 如果容差范围内没有数据，取 DTE 最接近的
        logger.warning(
            f"DTE 容差 [{dt_min}, {dt_max}] 内无数据，"
            f"选择最接近目标 DTE={target_dte} 的到期日"
        )
        df["dte_diff"] = (df["dte"] - target_dte).abs()
        nearest_dte = df["dte_diff"].min()
        candidate_df = df[df["dte_diff"] == nearest_dte].drop(columns=["dte_diff"])

    selected_dte = candidate_df["dte"].iloc[0] if not candidate_df.empty else None
    logger.info(f"DTE 过滤器: 目标={target_dte}, 实际选择={selected_dte}, 保留 {len(candidate_df)} 条记录")
    return candidate_df


def filter_missing_values(df: pd.DataFrame) -> pd.DataFrame:
    """
    处理缺失值：
    - 标记 Delta/IV 缺失的行
    - 默认剔除 IV 缺失的记录（无法进行 Skew 计算）

    Returns:
        清洗后的 DataFrame
    """
    before = len(df)

    # 记录缺失统计
    missing_delta = df["delta"].isna().sum()
    missing_iv = df["implied_volatility"].isna().sum()
    missing_volume = (df["volume"] == 0).sum()

    if missing_delta > 0 or missing_iv > 0:
        logger.info(
            f"缺失值统计: delta={missing_delta}, iv={missing_iv}, zero_volume={missing_volume}"
        )

    # 剔除 IV 缺失的记录（无法进行 Skew 计算）
    df = df.dropna(subset=["implied_volatility"])

    # 剔除 IV 为 0 或负值的记录
    df = df[df["implied_volatility"] > 0]

    # 可选：按最低成交量过滤
    df = df[df["volume"] >= MIN_VOLUME]

    removed = before - len(df)
    if removed > 0:
        logger.info(f"缺失值过滤器: 剔除了 {removed} 条记录 ({removed / before * 100:.1f}%)")

    return df


def clean_option_chain(
    snapshots: list[dict[str, Any]],
    ticker: str,
    as_of_date: Optional[date] = None,
) -> pd.DataFrame:
    """
    完整的期权链数据清洗流水线。

    依次执行:
        1. JSON 解析 -> DataFrame
        2. Bid/Ask 价差异常过滤
        3. DTE 过滤（选择约30天到期的期权）
        4. 缺失值处理

    Args:
        snapshots: Polygon.io 期权快照列表
        ticker: 标的符号
        as_of_date: 参考日期

    Returns:
        清洗后的期权链 DataFrame，可直接用于插值计算
    """
    # Step 1: 解析
    df = parse_snapshots_to_dataframe(snapshots, ticker, as_of_date)
    if df.empty:
        return df

    # Step 2: 过滤 Bid/Ask 异常
    df = filter_bid_ask_outliers(df)

    # Step 3: 过滤 DTE
    df = filter_by_dte(df)

    # Step 4: 缺失值处理
    df = filter_missing_values(df)

    logger.info(f"[{ticker}] 数据清洗完成: {len(df)} 条有效期权记录")
    return df
