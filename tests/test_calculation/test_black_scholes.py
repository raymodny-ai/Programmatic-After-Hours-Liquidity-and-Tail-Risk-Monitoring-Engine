"""
Black-Scholes-Merton Delta 计算单元测试 (v1.2.1)

测试范围:
    1. ATM Call Delta 应接近 0.5（考虑股息折现）
    2. Put-Call Delta 关系验证
    3. 边界情况：零波动率、负时间、无效参数
"""

import math

import numpy as np
import pytest

from src.calculation.black_scholes import bsm_delta, bsm_delta_batch


class TestBSMDelta:
    """BSM Delta 回推单元测试。"""

    def test_atm_call_delta_near_half(self):
        """ATM Call Delta 应接近 0.5（考虑股息折现）。"""
        delta = bsm_delta(
            spot=100.0,
            strike=100.0,
            time_to_expiry=0.08,  # ~30 天
            rate=0.045,
            dividend_yield=0.012,
            volatility=0.22,
            option_type="call",
        )

        # 股息折现后 ATM Call Delta 略低于 0.5
        assert 0.45 < delta < 0.55, f"ATM Call Delta={delta:.4f}，应接近 0.5"
        assert np.isfinite(delta)

    def test_deep_itm_call_delta_near_one(self):
        """深度实值 Call Delta 应接近 1.0。"""
        delta = bsm_delta(
            spot=150.0,
            strike=50.0,
            time_to_expiry=0.25,
            rate=0.05,
            dividend_yield=0.0,
            volatility=0.20,
            option_type="call",
        )
        assert delta > 0.9, f"深度 ITM Call Delta={delta:.4f}，应接近 1.0"

    def test_deep_otm_call_delta_near_zero(self):
        """深度虚值 Call Delta 应接近 0。"""
        delta = bsm_delta(
            spot=100.0,
            strike=200.0,
            time_to_expiry=0.08,
            rate=0.05,
            dividend_yield=0.0,
            volatility=0.20,
            option_type="call",
        )
        assert delta < 0.1, f"深度 OTM Call Delta={delta:.4f}，应接近 0"

    def test_put_call_delta_relation(self):
        """BSM 中 Call Delta - Put Delta ≈ e^{-qT}。"""
        common = dict(
            spot=100.0,
            strike=100.0,
            time_to_expiry=0.08,
            rate=0.045,
            dividend_yield=0.012,
            volatility=0.22,
        )

        call_delta = bsm_delta(**common, option_type="call")
        put_delta = bsm_delta(**common, option_type="put")

        # Put Delta 应为负值
        assert put_delta < 0

        # Call Delta - Put Delta ≈ e^{-qT}
        expected_diff = math.exp(-0.012 * 0.08)
        actual_diff = call_delta - put_delta
        assert abs(actual_diff - expected_diff) < 1e-6, (
            f"Δ_call - Δ_put={actual_diff:.6f}, 预期={expected_diff:.6f}"
        )

    def test_zero_spot_returns_nan(self):
        """spot=0 时应返回 NaN。"""
        delta = bsm_delta(
            spot=0.0,
            strike=100.0,
            time_to_expiry=0.08,
            rate=0.05,
            dividend_yield=0.0,
            volatility=0.20,
            option_type="call",
        )
        assert np.isnan(delta)

    def test_negative_time_returns_nan(self):
        """负数时间到期货应返回 NaN。"""
        delta = bsm_delta(
            spot=100.0,
            strike=100.0,
            time_to_expiry=-0.01,
            rate=0.05,
            dividend_yield=0.0,
            volatility=0.20,
            option_type="call",
        )
        assert np.isnan(delta)

    def test_zero_volatility_returns_nan(self):
        """零波动率应返回 NaN（除数为零）。"""
        delta = bsm_delta(
            spot=100.0,
            strike=100.0,
            time_to_expiry=0.08,
            rate=0.05,
            dividend_yield=0.0,
            volatility=0.0,
            option_type="call",
        )
        assert np.isnan(delta)

    def test_invalid_option_type_raises(self):
        """无效期权类型应抛出 ValueError。"""
        with pytest.raises(ValueError, match="未知期权类型"):
            bsm_delta(
                spot=100.0,
                strike=100.0,
                time_to_expiry=0.08,
                rate=0.05,
                dividend_yield=0.0,
                volatility=0.20,
                option_type="straddle",  # type: ignore[arg-type]
            )


class TestBSMDeltaBatch:
    """BSM Delta 批量计算测试。"""

    def test_batch_vs_single_consistency(self):
        """批量计算结果应与单次计算一致。"""
        strikes = np.array([90.0, 100.0, 110.0])
        ivs = np.array([0.22, 0.20, 0.24])
        types = np.array(["put", "call", "call"])

        batch_deltas = bsm_delta_batch(
            spot=100.0,
            strikes=strikes,
            time_to_expiry=0.08,
            rate=0.045,
            dividend_yield=0.012,
            volatilities=ivs,
            option_types=types,
        )

        for i in range(3):
            single = bsm_delta(
                spot=100.0,
                strike=float(strikes[i]),
                time_to_expiry=0.08,
                rate=0.045,
                dividend_yield=0.012,
                volatility=float(ivs[i]),
                option_type=str(types[i]),  # type: ignore[arg-type]
            )
            assert abs(batch_deltas[i] - single) < 1e-10, (
                f"Batch[{i}]={batch_deltas[i]:.10f}, Single={single:.10f}"
            )

    def test_batch_with_invalid_elements(self):
        """无效元素应返回 NaN，但不影响有效元素。"""
        strikes = np.array([90.0, -10.0, 110.0])
        ivs = np.array([0.22, 0.20, 0.24])
        types = np.array(["put", "call", "call"])

        batch_deltas = bsm_delta_batch(
            spot=100.0,
            strikes=strikes,
            time_to_expiry=0.08,
            rate=0.045,
            dividend_yield=0.012,
            volatilities=ivs,
            option_types=types,
        )

        assert np.isnan(batch_deltas[1]), "负 strike 应返回 NaN"
        assert not np.isnan(batch_deltas[0]), "有效元素不应为 NaN"
        assert not np.isnan(batch_deltas[2]), "有效元素不应为 NaN"
