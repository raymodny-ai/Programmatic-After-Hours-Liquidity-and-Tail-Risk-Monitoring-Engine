"""
Skew 计算单元测试 (v1.2)

测试范围:
    1. PCHIP / CubicSpline / Linear 插值方法的正确性
    2. 边界情况：数据点不足、Delta 范围外、NaN 处理
    3. 回归验证：确保插值方法变更不破坏现有计算
"""

import numpy as np
import pandas as pd
import pytest

from src.calculation.delta_interpolator import DeltaIVInterpolator


class TestDeltaIVInterpolator:
    """25Δ IV 插值器单元测试。"""

    @pytest.fixture
    def sample_option_chain(self) -> pd.DataFrame:
        """构造一个模拟期权链，含 Call 和 Put 的 Delta/IV 数据。"""
        return pd.DataFrame({
            "contract_type": [
                "put", "put", "put", "put", "put", "put",
                "call", "call", "call", "call", "call", "call",
            ],
            "delta": [
                -0.10, -0.15, -0.20, -0.25, -0.30, -0.35,
                0.10, 0.15, 0.20, 0.25, 0.30, 0.35,
            ],
            "implied_volatility": [
                0.25, 0.24, 0.23, 0.22, 0.21, 0.20,
                0.18, 0.19, 0.20, 0.21, 0.22, 0.23,
            ],
            "strike": [400, 410, 420, 430, 440, 450, 460, 470, 480, 490, 500, 510],
        })

    @pytest.fixture
    def sparse_chain(self) -> pd.DataFrame:
        """模拟 DIA 稀疏期权链（只有 3 个 Put 数据点）。"""
        return pd.DataFrame({
            "contract_type": ["put", "put", "put", "call", "call", "call"],
            "delta": [-0.15, -0.25, -0.35, 0.15, 0.25, 0.35],
            "implied_volatility": [0.22, 0.20, 0.19, 0.17, 0.18, 0.19],
            "strike": [350, 360, 370, 380, 390, 400],
        })

    def test_pchip_basic(self, sample_option_chain):
        """PCHIP 插值应返回合理的 25Δ IV。"""
        interp = DeltaIVInterpolator(method="pchip")
        iv_put, iv_call = interp.interpolate(sample_option_chain)

        assert not np.isnan(iv_put), "PCHIP Put IV 不应为 NaN"
        assert not np.isnan(iv_call), "PCHIP Call IV 不应为 NaN"
        assert 0 < iv_put < 1.0, f"Put IV 应在 (0, 1) 范围内，实际={iv_put}"
        assert 0 < iv_call < 1.0, f"Call IV 应在 (0, 1) 范围内，实际={iv_call}"

    def test_cubic_spline_basic(self, sample_option_chain):
        """CubicSpline 插值应返回合理的 25Δ IV。"""
        interp = DeltaIVInterpolator(method="cubic_spline")
        iv_put, iv_call = interp.interpolate(sample_option_chain)

        assert not np.isnan(iv_put)
        assert not np.isnan(iv_call)
        assert 0 < iv_put < 1.0
        assert 0 < iv_call < 1.0

    def test_linear_basic(self, sample_option_chain):
        """Linear 插值应返回合理的 25Δ IV。"""
        interp = DeltaIVInterpolator(method="linear")
        iv_put, iv_call = interp.interpolate(sample_option_chain)

        assert not np.isnan(iv_put)
        assert not np.isnan(iv_call)
        assert 0 < iv_put < 1.0
        assert 0 < iv_call < 1.0

    def test_pchip_on_sparse_chain(self, sparse_chain):
        """PCHIP 在稀疏期权链上不应崩溃。"""
        interp = DeltaIVInterpolator(method="pchip")
        iv_put, iv_call = interp.interpolate(sparse_chain)

        assert not np.isnan(iv_put), "PCHIP 在稀疏链上不应返回 NaN"
        assert not np.isnan(iv_call)

    def test_cubic_spline_on_sparse_chain(self, sparse_chain):
        """CubicSpline 在稀疏链上也不应崩溃（但可能外推）。"""
        interp = DeltaIVInterpolator(method="cubic_spline")
        iv_put, iv_call = interp.interpolate(sparse_chain)

        assert not np.isnan(iv_put)
        assert not np.isnan(iv_call)

    def test_empty_dataframe(self):
        """空 DataFrame 应返回 NaN。"""
        interp = DeltaIVInterpolator()
        iv_put, iv_call = interp.interpolate(pd.DataFrame())

        assert np.isnan(iv_put)
        assert np.isnan(iv_call)

    def test_missing_delta_column(self):
        """缺少 delta 列时不应崩溃。"""
        interp = DeltaIVInterpolator()
        df = pd.DataFrame({
            "contract_type": ["put", "call"],
            "implied_volatility": [0.2, 0.2],
        })

        iv_put, iv_call = interp.interpolate(df)
        assert np.isnan(iv_put)
        assert np.isnan(iv_call)

    def test_single_data_point_returns_nan(self):
        """每个期权方向仅一个点时无法插值，必须返回 NaN。"""
        interp = DeltaIVInterpolator(method="pchip")
        df = pd.DataFrame({
            "contract_type": ["put", "call"],
            "delta": [-0.25, 0.25],
            "implied_volatility": [0.22, 0.21],
        })

        iv_put, iv_call = interp.interpolate(df)

        assert np.isnan(iv_put), f"单 Put 数据点应返回 NaN，实际={iv_put}"
        assert np.isnan(iv_call), f"单 Call 数据点应返回 NaN，实际={iv_call}"

    def test_target_delta_outside_observed_range_returns_nan(self):
        """目标 Delta 在样本区间外时，禁止外推（PCHIP extrapolate=False）。"""
        interp = DeltaIVInterpolator(method="pchip")
        # 构造 Delta 范围远离 0.25 的期权链
        df = pd.DataFrame({
            "contract_type": ["put", "put", "call", "call"],
            "delta": [-0.05, -0.10, 0.05, 0.10],
            "implied_volatility": [0.20, 0.21, 0.18, 0.19],
        })

        iv_put, iv_call = interp.interpolate(df)

        # 目标 0.25 不在 [0.05, 0.10] 范围内，extrapolate=False 应返回 NaN
        assert np.isnan(iv_put), f"Put 目标 Delta 超出范围应返回 NaN，实际={iv_put}"
        assert np.isnan(iv_call), f"Call 目标 Delta 超出范围应返回 NaN，实际={iv_call}"

    def test_duplicate_delta_values_are_deduplicated(self):
        """重复 Delta 值去重后不应导致 PCHIP 崩溃。"""
        interp = DeltaIVInterpolator(method="pchip")
        df = pd.DataFrame({
            "contract_type": ["put", "put", "put", "call", "call", "call"],
            "delta": [-0.20, -0.20, -0.30, 0.20, 0.20, 0.30],
            "implied_volatility": [0.22, 0.23, 0.20, 0.19, 0.20, 0.22],
        })

        iv_put, iv_call = interp.interpolate(df)

        # 去重后应有 2 个唯一 Put 和 2 个唯一 Call 点，应能插值
        assert not np.isnan(iv_put), f"去重后 Put 应可插值，实际={iv_put}"
        assert not np.isnan(iv_call), f"去重后 Call 应可插值，实际={iv_call}"

    def test_interpolate_with_confidence(self, sample_option_chain):
        """置信度报告应包含所有元数据字段。"""
        interp = DeltaIVInterpolator()
        result = interp.interpolate_with_confidence(sample_option_chain)

        assert "iv_put_25d" in result
        assert "iv_call_25d" in result
        assert "put_data_points" in result
        assert "call_data_points" in result
        assert "put_delta_range" in result
        assert "call_delta_range" in result
        assert "put_is_extrapolated" in result
        assert "call_is_extrapolated" in result

    def test_methods_produce_similar_results(self, sample_option_chain):
        """三种插值方法应在合理范围内产生相似结果。"""
        for method in ["pchip", "cubic_spline", "linear"]:
            interp = DeltaIVInterpolator(method=method)
            iv_put, iv_call = interp.interpolate(sample_option_chain)

            assert not np.isnan(iv_put), f"{method}: Put IV NaN"
            assert not np.isnan(iv_call), f"{method}: Call IV NaN"

            # 对于这个合成数据，Put IV 应在 ~0.22 附近
            assert abs(iv_put - 0.22) < 0.05, (
                f"{method}: Put IV={iv_put:.4f} 偏离预期太远"
            )
            # Call IV 应在 ~0.21 附近
            assert abs(iv_call - 0.21) < 0.05, (
                f"{method}: Call IV={iv_call:.4f} 偏离预期太远"
            )
