"""
备用数据源模块 (fallback_source.py) v1.2.1

功能：
- 当 Polygon.io API 限速/超时/故障时，自动降级到 yfinance
- v1.2.1: 使用 BSM 模型回推 Delta，解决无 Greeks 导致插值输出 NaN 的问题
- 在 DataWriter 的 Parquet 快照中记录 data_source 字段用于溯源审计

数据源优先级:
    1. Polygon.io (主源) — 提供完整 Greeks + IV + 快照
    2. yfinance (备用源) — 提供基础期权链数据 + BSM 估计 Delta

使用方式:
    from src.data_ingestion.fallback_source import fetch_options_fallback
    df = await fetch_options_fallback("SPY")
"""

from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf
from loguru import logger

from src.calculation.black_scholes import bsm_delta

# ── v1.2.1: BSM 模型参数 ──
DEFAULT_RISK_FREE_RATE: float = 0.045  # 4.5% 无风险利率
DEFAULT_DIVIDEND_YIELD: dict[str, float] = {
    "SPY": 0.012,
    "QQQ": 0.006,
    "IWM": 0.014,
    "DIA": 0.016,
}


async def fetch_options_fallback(
    ticker: str,
    target_dte: int = 30,
    dte_tolerance: int = 5,
) -> Optional[list[dict[str, Any]]]:
    """使用 yfinance 作为备用源获取期权链数据（v1.2.1: BSM Delta）。

    将 yfinance 返回的 DataFrame 转换为与 Polygon.io 快照兼容的格式，
    并通过 BSM 模型回推 Delta，以便下游清洗与插值管道无需改动。

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

        # ── v1.2.1: 获取 market context（spot + 利率 + 股息率）──
        market_context = _get_market_context(stock, ticker)
        logger.debug(
            f"[fallback] {ticker} market_context: spot={market_context['spot']:.2f}, "
            f"rate={market_context['risk_free_rate']}, div={market_context['dividend_yield']}"
        )

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
            snap = _row_to_snapshot(
                ticker, "call", row, best_expiry,
                market_context=market_context,
            )
            if snap:
                snapshots.append(snap)

        for _, row in puts_df.iterrows():
            snap = _row_to_snapshot(
                ticker, "put", row, best_expiry,
                market_context=market_context,
            )
            if snap:
                snapshots.append(snap)

        if not snapshots:
            logger.warning(f"[fallback] {ticker} 期权链转换后无有效快照")
            return None

        logger.info(
            f"[fallback] {ticker}: 获取 {len(snapshots)} 条快照 "
            f"(到期日={best_expiry}, DTE={best_diff:.0f}, "
            f"source=yfinance+BSM)"
        )
        return snapshots

    except Exception as e:
        logger.error(f"[fallback] {ticker} 备用源获取失败: {type(e).__name__}: {e}")
        return None


def _get_market_context(stock: yf.Ticker, ticker: str) -> dict[str, float]:
    """获取 yfinance 标的的 market context（v1.2.1）。

    每个 ticker 只调用一次，避免在逐行期权循环中重复请求。

    Returns:
        {"spot": float, "risk_free_rate": float, "dividend_yield": float}
    """
    fast_info = stock.fast_info

    spot = (
        fast_info.get("last_price")
        or fast_info.get("regular_market_previous_close")
    )

    if spot is None or not np.isfinite(spot) or spot <= 0:
        history = stock.history(period="5d", auto_adjust=False)
        if not history.empty:
            spot = float(history["Close"].dropna().iloc[-1])
        else:
            raise ValueError(f"{ticker} 无法获取 spot 价格")

    return {
        "spot": float(spot),
        "risk_free_rate": DEFAULT_RISK_FREE_RATE,
        "dividend_yield": DEFAULT_DIVIDEND_YIELD.get(ticker.upper(), 0.0),
    }


def _row_to_snapshot(
    ticker: str,
    contract_type: str,
    row: pd.Series,
    expiration_str: str,
    market_context: Optional[dict[str, float]] = None,
) -> Optional[dict[str, Any]]:
    """将 yfinance 期权行转换为 Polygon.io 快照兼容格式（v1.2.1: BSM Delta）。

    Args:
        ticker: 标的符号
        contract_type: "call" 或 "put"
        row: yfinance 期权行数据
        expiration_str: 到期日字符串
        market_context: BSM 模型参数 {"spot", "risk_free_rate", "dividend_yield"}

    Returns:
        Polygon.io 兼容快照字典；若关键字段缺失或 BSM Delta 无效则返回 None
    """
    strike = row.get("strike")
    if strike is None or pd.isna(strike):
        return None

    iv = row.get("impliedVolatility")
    if iv is None or pd.isna(iv):
        return None

    # ── v1.2.1: BSM Delta 回推 ──
    estimated_delta = None
    greeks_source = "vendor"  # 默认：Polygon 原生 Greeks
    if market_context is not None:
        try:
            expiry = datetime.strptime(expiration_str, "%Y-%m-%d").date()
            dte = max((expiry - date.today()).days, 0)
            time_to_expiry = max(dte / 365.25, 1.0 / 365.25)

            estimated_delta = bsm_delta(
                spot=market_context["spot"],
                strike=float(strike),
                time_to_expiry=time_to_expiry,
                rate=market_context["risk_free_rate"],
                dividend_yield=market_context["dividend_yield"],
                volatility=float(iv),
                option_type=contract_type,  # type: ignore[arg-type]
            )

            if not np.isfinite(estimated_delta):
                logger.debug(
                    f"[fallback] {ticker} {contract_type} K={strike}: "
                    f"BSM Delta 计算失败，跳过该行"
                )
                return None

            greeks_source = "bsm_estimated"
        except Exception as e:
            logger.debug(f"[fallback] BSM Delta 计算异常: {e}")
            return None

    # 构造 OCC 格式的 ticker
    exp_clean = expiration_str.replace("-", "")
    strike_str = f"{int(float(strike) * 1000):08d}"
    occ = f"O:{ticker}{exp_clean}{'C' if contract_type == 'call' else 'P'}{strike_str}"

    bid = row.get("bid", 0)
    ask = row.get("ask", 0)
    last_price = row.get("lastPrice", 0)
    volume = row.get("volume", 0) or 0
    open_interest = row.get("openInterest", 0) or 0

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
            "delta": float(estimated_delta) if estimated_delta is not None else None,
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
        "_data_source": "yfinance",
        "_greeks_source": greeks_source,  # v1.2.1
        "_signal_quality": "fallback_estimated",  # v1.2.1: 综合质量标记
        "_spot_used": market_context["spot"] if market_context else None,
        "_risk_free_rate_used": (
            market_context["risk_free_rate"] if market_context else None
        ),
        "_dividend_yield_used": (
            market_context["dividend_yield"] if market_context else None
        ),
    }
