"""
波动率状态分析单元测试 (v1.2.1)

测试范围:
    1. VXN 高于 2σ 时触发预警
    2. VXN/VIX 无共同日期时返回 insufficient_history
    3. 空数据、缺失列等边界情况
"""

import numpy as np
import pandas as pd
import pytest

from src.calculation.volatility_regime import (
    compute_volatility_regime,
    compute_vxn_vix_spread,
    build_qqq_tail_risk_confirmation,
)


class TestVolatilityRegime:
    """波动率状态信号单元测试。"""

    @pytest.fixture
    def normal_vix_df(self) -> pd.DataFrame:
        """构造正常的 VIX 历史数据（100 个交易日）。"""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        np.random.seed(42)
        return pd.DataFrame({
            "date": dates,
            "close": np.random.normal(18, 2, 100).clip(10, 40),
        })

    def test_normal_regime_no_alert(self, normal_vix_df):
        """正常波动率范围内不应触发预警。"""
        result = compute_volatility_regime(
            normal_vix_df,
            index_name="VIX",
            zscore_threshold=2.0,
        )

        assert result["status"] == "ok"
        assert result["index"] == "VIX"
        assert "current_level" in result
        # 随机数据不太可能触发极端预警
        assert result["alert_level"] in ("normal", "elevated")

    def test_vxn_above_2sigma_triggers_alert(self):
        """VXN 当前值高于 2σ 时应触发预警。"""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        # 前99天均值≈20，std≈2；最后一天设 26（3σ）
        closes = [20.0] * 99 + [26.0]
        df = pd.DataFrame({"date": dates, "close": closes})

        result = compute_volatility_regime(
            df,
            index_name="VXN",
            zscore_threshold=2.0,
        )

        assert result["status"] == "ok"
        assert result["is_alert"] is True
        assert result["z_score"] >= 2.0

    def test_insufficient_history(self):
        """历史数据不足 20 条时应返回 insufficient_history。"""
        dates = pd.date_range("2024-06-01", periods=5, freq="B")
        df = pd.DataFrame({"date": dates, "close": [20.0] * 5})

        result = compute_volatility_regime(df, index_name="VIX")
        assert result["status"] == "insufficient_history"
        assert result["is_alert"] is False

    def test_empty_dataframe(self):
        """空 DataFrame 应返回 unavailable。"""
        result = compute_volatility_regime(pd.DataFrame(), index_name="VIX")
        assert result["status"] == "unavailable"
        assert result["is_alert"] is False

    def test_missing_close_column(self):
        """缺少 close 列应返回 unavailable。"""
        df = pd.DataFrame({"date": ["2024-01-01"], "value": [20.0]})
        result = compute_volatility_regime(df, index_name="VIX")
        assert result["status"] == "unavailable"

    def test_zero_std_returns_zscore_zero(self):
        """标准差为 0 时 Z-Score 应为 0。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        df = pd.DataFrame({"date": dates, "close": [25.0] * 30})

        result = compute_volatility_regime(df, index_name="VIX")
        assert result["z_score"] == 0.0
        assert result["is_alert"] is False

    def test_change_20d_calculation(self):
        """20 日变化率应正确计算。"""
        dates = pd.date_range("2024-01-01", periods=50, freq="B")
        closes = [20.0] * 29 + [22.0] * 21  # 第30天起从20跳到22
        df = pd.DataFrame({"date": dates, "close": closes})

        result = compute_volatility_regime(df, index_name="VIX", momentum_window=20)
        # 20天前是22, 当前是22, change≈0
        assert result["change_20d"] is not None
        assert abs(result["change_20d"]) < 0.001


class TestVXNVIXSpread:
    """VXN-VIX Spread 测试。"""

    def test_no_common_dates_returns_insufficient(self):
        """VXN 和 VIX 无共同日期时应返回 insufficient_history。"""
        vxn = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=10, freq="B"),
            "close": [25.0] * 10,
        })
        vix = pd.DataFrame({
            "date": pd.date_range("2024-03-01", periods=10, freq="B"),
            "close": [18.0] * 10,
        })

        result = compute_vxn_vix_spread(vxn, vix)
        assert result["status"] == "insufficient_history"
        assert result["is_alert"] is False

    def test_normal_spread_no_alert(self):
        """正常 Spread 不应触发预警。"""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        vxn = pd.DataFrame({"date": dates, "close": [22.0] * 100})
        vix = pd.DataFrame({"date": dates, "close": [18.0] * 100})

        result = compute_vxn_vix_spread(vxn, vix)

        assert result["status"] == "ok"
        # spread 恒定为 4，标准差为 0，z_score=0
        assert result["z_score"] == 0.0
        assert result["is_alert"] is False

    def test_wide_spread_triggers_alert(self):
        """Spread 异常扩大时应触发预警。"""
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        # 前99天 spread≈4，最后一天 spread=6
        vxn_closes = [22.0] * 99 + [24.0]
        vix_closes = [18.0] * 99 + [18.0]
        vxn = pd.DataFrame({"date": dates, "close": vxn_closes})
        vix = pd.DataFrame({"date": dates, "close": vix_closes})

        result = compute_vxn_vix_spread(vxn, vix)

        # spread 从4跳到6，应有预警
        assert result["status"] == "ok"
        assert result["z_score"] >= 2.0
        assert result["is_alert"] is True


class TestQQQTailRiskConfirmation:
    """QQQ 三因子确认预警测试。"""

    def test_all_three_triggered(self):
        """三因子全部触发应返回 critical。"""
        result = build_qqq_tail_risk_confirmation(
            qqq_skew_alert={"ticker": "QQQ", "is_alert": True},
            vxn_regime={"is_alert": True},
            vxn_vix_signal={"is_alert": True},
        )

        assert result["confirmation_score"] == 3
        assert result["severity"] == "critical"
        assert result["is_alert"] is True

    def test_none_triggered(self):
        """无触发应返回 normal。"""
        result = build_qqq_tail_risk_confirmation(
            qqq_skew_alert={"ticker": "QQQ", "is_alert": False},
            vxn_regime={"is_alert": False},
            vxn_vix_signal={"is_alert": False},
        )

        assert result["confirmation_score"] == 0
        assert result["severity"] == "normal"
        assert result["is_alert"] is False

    def test_none_inputs(self):
        """None 输入应正常处理。"""
        result = build_qqq_tail_risk_confirmation(
            qqq_skew_alert=None,
            vxn_regime=None,
            vxn_vix_signal=None,
        )

        assert result["confirmation_score"] == 0
        assert result["is_alert"] is False

    def test_two_of_three_triggered(self):
        """两因子触发应返回 high。"""
        result = build_qqq_tail_risk_confirmation(
            qqq_skew_alert={"ticker": "QQQ", "is_alert": True},
            vxn_regime={"is_alert": True},
            vxn_vix_signal={"is_alert": False},
        )

        assert result["confirmation_score"] == 2
        assert result["severity"] == "high"
        assert result["is_alert"] is True
