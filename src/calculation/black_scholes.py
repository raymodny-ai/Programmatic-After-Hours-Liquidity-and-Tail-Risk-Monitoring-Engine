"""
Black-Scholes-Merton 欧式期权定价辅助模块 (black_scholes.py) v1.2.1

功能：
- 基于 BSM 模型回推 ETF 期权的 Delta（用于 yfinance 备用源）
- yfinance 提供的期权链数据有 IV、无 Greeks，通过 BSM 近似计算 Delta
- 仅供风险监控参考，不得等同于 Polygon.io 的原生 Greeks

核心公式:
    Δ_call = e^{-qT} N(d1)
    Δ_put  = -e^{-qT} N(-d1)

    d1 = (ln(S/K) + (r - q + σ²/2)T) / (σ√T)

    其中:
        S: ETF 现货价格
        K: 行权价
        T: 剩余到期年化时间
        r: 无风险利率
        q: 股息率
        σ: 隐含波动率 (yfinance 提供)
"""

from __future__ import annotations

import math
from typing import Literal

import numpy as np
from scipy.stats import norm


def bsm_delta(
    *,
    spot: float,
    strike: float,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatility: float,
    option_type: Literal["call", "put"],
) -> float:
    """使用 Black-Scholes-Merton 模型回推 ETF 期权 Delta。

    Args:
        spot: ETF 现货价格
        strike: 行权价
        time_to_expiry: 剩余到期年化时间（年）
        rate: 无风险利率（如 0.045 表示 4.5%）
        dividend_yield: 连续股息率（如 0.012 表示 1.2%）
        volatility: yfinance 提供的隐含波动率（如 0.22 表示 22%）
        option_type: "call" 或 "put"

    Returns:
        估计的 Delta 值（call 为正值，put 为负值）
        若输入无效则返回 NaN
    """
    values = [spot, strike, time_to_expiry, volatility]
    if any(not np.isfinite(x) or x <= 0 for x in values):
        return float("nan")

    sqrt_t = math.sqrt(time_to_expiry)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * volatility ** 2) * time_to_expiry
    ) / (volatility * sqrt_t)

    discount_q = math.exp(-dividend_yield * time_to_expiry)

    if option_type == "call":
        return float(discount_q * norm.cdf(d1))

    if option_type == "put":
        return float(-discount_q * norm.cdf(-d1))

    raise ValueError(f"未知期权类型: {option_type}")


def bsm_delta_batch(
    *,
    spot: float,
    strikes: np.ndarray,
    time_to_expiry: float,
    rate: float,
    dividend_yield: float,
    volatilities: np.ndarray,
    option_types: np.ndarray,
) -> np.ndarray:
    """批量计算 BSM Delta（向量化加速）。

    Args:
        spot: ETF 现货价格
        strikes: 行权价数组
        time_to_expiry: 剩余到期年化时间
        rate: 无风险利率
        dividend_yield: 股息率
        volatilities: IV 数组（与 strikes 长度相同）
        option_types: "call"/"put" 数组（与 strikes 长度相同）

    Returns:
        Delta 数组
    """
    if any(x <= 0 or not np.isfinite(x) for x in [spot, time_to_expiry]):
        return np.full_like(strikes, np.nan, dtype=float)

    valid = (
        np.isfinite(strikes)
        & (strikes > 0)
        & np.isfinite(volatilities)
        & (volatilities > 0)
    )

    result = np.full_like(strikes, np.nan, dtype=float)

    if not valid.any():
        return result

    s = spot
    t = time_to_expiry
    sqrt_t = math.sqrt(t)
    discount_q = math.exp(-dividend_yield * t)

    k_valid = strikes[valid]
    v_valid = volatilities[valid]

    d1 = (
        np.log(s / k_valid)
        + (rate - dividend_yield + 0.5 * v_valid ** 2) * t
    ) / (v_valid * sqrt_t)

    is_call = option_types[valid] == "call"
    deltas = np.where(
        is_call,
        discount_q * norm.cdf(d1),
        -discount_q * norm.cdf(-d1),
    )
    result[valid] = deltas

    return result
