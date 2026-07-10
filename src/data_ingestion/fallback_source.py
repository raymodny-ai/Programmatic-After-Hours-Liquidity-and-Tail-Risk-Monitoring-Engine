"""
备用数据源模块 (fallback_source.py) v1.2

功能：
- 当 Polygon.io API 限速/超时/故障时，自动降级到 yfinance
- 提供双源对冲：确保每日数据管道的韧性
- 在 DataWriter 的 Parquet 快照中记录 data_source 字段用于溯源审计

数据源优先级:
    1. Polygon.io (主源) — 提供完整 Greeks + IV + 快照
    2. yfinance (备用源) — 提供基础期权链数据（无 Greeks）

使用方式:
    from src.data_ingestion.fallback_source import fetch_options_fallback
    df = await fetch_options_fallback("SPY")
"""

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
import yfinance as yf
from loguru import logger


async def fetch_options_fallback(
    ticker: str,
    target_dte: int = 30,
    dte_tolerance: int = 5,
) -> Optional[list[dict[str, Any]]]:
    """
    使用 yfinance 作为备用源获取期权链数据。

    将 yfinance 返回的 DataFrame 转换为与 Polygon.io 快照兼容的格式，
    以便下游清洗与插值管道无需改动。

    Args:
        ticker: 标的符号 (如 SPY, QQQ)
        target_dte: 目标到期天数
        dte_tolerance: 容差范围（±天）

    Returns:
        Polygon.io 兼容的快照列表，失败时返回 None
    """
    logger.info(f"[fallback] 使用 yfinance 备用源获取 {ticker} 期权链...")

    try:
        stock = yf.Ticker(ticker)

        # 获取所有可用到期日
        expirations = stock.options
        if not expirations:
            logger.warning(f"[fallback] {ticker} 无可用到期日")
            return None

        # 选择最接近 target_dte 的到期日
        today = date.today()
        best_expiry = None
        best_diff = float("inf")

        for exp_str in expirations:
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - today).days
            diff = abs(dte - target_dte)
            if diff <= dte_tolerance and diff < best_diff:
                best_diff = diff
                best_expiry = exp_str

        if best_expiry is None:
            logger.warning(
                f"[fallback] {ticker} 在 DTE [{target_dte - dte_tolerance}, "
                f"{target_dte + dte_tolerance}] 范围内无到期日"
            )
            return None

        # 拉取期权链
        opt_chain = stock.option_chain(best_expiry)
        calls_df = opt_chain.calls.copy()
        puts_df = opt_chain.puts.copy()

        # 转换为 Polygon.io 兼容格式的快照列表
        snapshots = []

        for _, row in calls_df.iterrows():
            snap = _row_to_snapshot(ticker, "call", row, best_expiry)
            if snap:
                snapshots.append(snap)

        for _, row in puts_df.iterrows():
            snap = _row_to_snapshot(ticker, "put", row, best_expiry)
            if snap:
                snapshots.append(snap)

        if not snapshots:
            logger.warning(f"[fallback] {ticker} 期权链转换后无有效快照")
            return None

        logger.info(
            f"[fallback] {ticker}: 获取 {len(snapshots)} 条快照 "
            f"(到期日={best_expiry}, DTE={best_diff:.0f})"
        )
        return snapshots

    except Exception as e:
        logger.error(f"[fallback] {ticker} 备用源获取失败: {type(e).__name__}: {e}")
        return None


def _row_to_snapshot(
    ticker: str,
    contract_type: str,
    row: pd.Series,
    expiration_str: str,
) -> Optional[dict[str, Any]]:
    """
    将 yfinance 期权行转换为 Polygon.io 快照兼容格式。

    Args:
        ticker: 标的符号
        contract_type: "call" 或 "put"
        row: yfinance 期权行数据
        expiration_str: 到期日字符串

    Returns:
        Polygon.io 兼容快照字典；若关键字段缺失则返回 None
    """
    strike = row.get("strike")
    if strike is None or pd.isna(strike):
        return None

    iv = row.get("impliedVolatility")
    if iv is None or pd.isna(iv):
        return None

    # 构造 OCC 格式的 ticker
    exp_clean = expiration_str.replace("-", "")
    strike_str = f"{int(strike * 1000):08d}"
    occ = f"O:{ticker}{exp_clean}{'C' if contract_type == 'call' else 'P'}{strike_str}"

    bid = row.get("bid", 0)
    ask = row.get("ask", 0)
    last_price = row.get("lastPrice", 0)
    volume = row.get("volume", 0) or 0
    open_interest = row.get("openInterest", 0) or 0

    # yfinance 不提供 Greeks，设为 NaN（下游会通过缺失值过滤）
    return {
        "ticker": occ,
        "details": {
            "contract_type": contract_type,
            "strike_price": float(strike),
            "expiration_date": expiration_str,
            "shares_per_contract": 100,
            "open_interest": int(open_interest),
        },
        "greeks": {
            "delta": row.get("delta") if "delta" in row.index and not pd.isna(row.get("delta")) else None,
            "gamma": None,
            "theta": None,
            "vega": None,
        },
        "implied_volatility": float(iv),
        "day": {
            "open": float(bid),
            "high": float(ask),
            "low": float(bid),
            "close": float(last_price),
            "volume": int(volume),
        },
        "last_quote": {
            "bid": float(bid) if bid else None,
            "ask": float(ask) if ask else None,
            "bid_size": 0,
            "ask_size": 0,
        },
        "_data_source": "yfinance",  # v1.2: 溯源标记
    }
