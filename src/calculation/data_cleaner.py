"""
数据清洗模块 (data_cleaner.py) v1.2

功能：
- 从 Polygon.io 快照 JSON 解析为结构化 pandas DataFrame
- 剔除期权链中 Bid/Ask 价差异常的脏数据（VWMP 加权中点）
- DTE 严格过滤器：[25, 35] 天窗口，无外推回退
- open_interest > 0 流动性过滤
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

            # ── v1.2: 使用 last_quote 中的真实 Bid/Ask ──
            last_quote = snap.get("last_quote", {}) or {}
            bid = last_quote.get("bid") or snap.get("day", {}).get("low")
            ask = last_quote.get("ask") or snap.get("day", {}).get("high")
            last_price = day_data.get("close", np.nan)

            # VWMP: 成交量加权中点（v1.2）
            bid_size = last_quote.get("bid_size", 0) or 0
            ask_size = last_quote.get("ask_size", 0) or 0
            total_size = bid_size + ask_size
            if total_size > 0 and bid is not None and ask is not None:
                mid_price = (bid * ask_size + ask * bid_size) / total_size
            elif bid is not None and ask is not None:
                mid_price = (bid + ask) / 2
            else:
                mid_price = last_price

            bid_ask_spread_pct = (
                abs(ask - bid) / mid_price if mid_price and mid_price > 0 and bid and ask else np.nan
            )

            # ── v1.2: 提取 open_interest ──
            open_interest = details.get("open_interest") or day_data.get("open_interest")

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
                "open_interest": open_interest,
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
    严格筛选距离到期日在 [target_dte - tolerance, target_dte + tolerance] 天的期权链。

    v1.2 变更：
        - 严格 DTE 窗口 [25, 35]，不再回退到窗口外最近到期日
        - 若窗口内有多个到期日，选择 DTE 最接近目标的那个

    Args:
        df: 期权数据 DataFrame（须包含 dte 列）
        target_dte: 目标到期天数
        tolerance: 容差范围（±天）

    Returns:
        筛选后的 DataFrame；若无匹配到期日则返回空 DataFrame
    """
    if "dte" not in df.columns:
        logger.warning("DataFrame 缺少 dte 列，跳过 DTE 过滤")
        return df

    dt_min = target_dte - tolerance
    dt_max = target_dte + tolerance

    candidate_df = df[(df["dte"] >= dt_min) & (df["dte"] <= dt_max)]

    if candidate_df.empty:
        # v1.2: 严格窗口，无数据则返回空
        logger.warning(
            f"DTE 严格窗口 [{dt_min}, {dt_max}] 内无数据，"
            f"跳过该标的（不再回退到最近到期日）"
        )
        return pd.DataFrame()

    # 若存在多个到期日，选择 DTE 最接近目标的
    unique_dtes = candidate_df["dte"].unique()
    if len(unique_dtes) > 1:
        closest_dte = min(unique_dtes, key=lambda d: abs(d - target_dte))
        candidate_df = candidate_df[candidate_df["dte"] == closest_dte]
        logger.info(
            f"DTE 过滤器: [{dt_min}, {dt_max}] 内有 {len(unique_dtes)} 个到期日，"
            f"选择最接近的 DTE={closest_dte}"
        )

    selected_dte = candidate_df["dte"].iloc[0]
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

    # ── v1.2: 过滤零未平仓合约（open_interest > 0）──
    if "open_interest" in df.columns:
        oi_before = len(df)
        df = df[df["open_interest"].isna() | (df["open_interest"] > 0)]
        oi_removed = oi_before - len(df)
        if oi_removed > 0:
            logger.info(f"open_interest 过滤器: 剔除了 {oi_removed} 条零 OI 记录")

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
