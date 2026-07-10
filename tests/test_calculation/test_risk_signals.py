"""
Z-Score 预警边界测试 (v1.2)

测试范围:
    1. 历史数据不足 10 条时的降级路径
    2. 标准差为零时的处理
    3. NaN 输入的容错
    4. 正常预警触发逻辑
"""

import numpy as np
import pandas as pd
import pytest

from src.calculation.risk_signals import (
    _classify_severity,
    check_all_ticker_alerts,
    compute_rolling_zscore,
    format_alert_summary,
)


class TestRollingZScore:
    """滚动 Z-Score 计算单元测试。"""

    @pytest.fixture
    def long_history(self) -> pd.DataFrame:
        """构造 100 天的历史数据。"""
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=100, freq="B")
        return pd.DataFrame({
            "date": dates,
            "ticker": "SPY",
            "skew_spread": np.random.normal(0.05, 0.01, 100),
        })

    @pytest.fixture
    def short_history(self) -> pd.DataFrame:
        """构造 5 天的历史数据（不足 10 条）。"""
        dates = pd.date_range("2024-06-01", periods=5, freq="B")
        return pd.DataFrame({
            "date": dates,
            "ticker": "SPY",
            "skew_spread": [0.05, 0.052, 0.048, 0.051, 0.053],
        })

    def test_zscore_with_sufficient_data(self, long_history):
        """有足够历史数据时应正常计算 Z-Score。"""
        result = compute_rolling_zscore(long_history, 0.07, window=90)

        assert not np.isnan(result["z_score"])
        assert not np.isnan(result["rolling_mean"])
        assert not np.isnan(result["rolling_std"])
        assert result["actual_data_points"] >= 10

    def test_zscore_below_10_points(self, short_history):
        """历史数据 2-10 条时应仍可估算 Z-Score（带警告）。"""
        result = compute_rolling_zscore(short_history, 0.06, window=90)

        # ≥2 个数据点时，仍可计算 Z-Score（仅 < 2 才返回 NaN）
        assert not np.isnan(result["z_score"])
        assert result["is_alert"] == True  # numpy bool → 用 == 比较
        # z_score≈4.8 超过阈值，方向应为 "above"
        assert result["alert_direction"] == "above"

    def test_zscore_with_zero_std(self):
        """标准差为零时 Z-Score 应为 0。"""
        dates = pd.date_range("2024-01-01", periods=20, freq="B")
        df = pd.DataFrame({
            "date": dates,
            "skew_spread": [0.05] * 20,  # 所有值相同
        })

        result = compute_rolling_zscore(df, 0.05, window=20)

        assert result["z_score"] == 0.0
        assert result["is_alert"] == False  # numpy bool → 用 == 比较

    def test_zscore_empty_history(self):
        """空历史数据应返回安全的默认值。"""
        result = compute_rolling_zscore(pd.DataFrame(), 0.05)

        assert np.isnan(result["z_score"])
        assert result["is_alert"] == False  # numpy bool → 用 == 比较

    def test_zscore_triggers_alert(self, long_history):
        """显著偏离均值时应触发预警。"""
        mean_val = long_history["skew_spread"].mean()
        std_val = long_history["skew_spread"].std()
        # 构造一个 >3σ 的极值
        extreme_value = mean_val + 3.5 * std_val

        result = compute_rolling_zscore(long_history, extreme_value, window=90)

        assert result["is_alert"] == True  # numpy bool → 用 == 比较
        assert result["alert_direction"] == "above"
        assert abs(result["z_score"]) >= 2.0

    def test_zscore_no_alert_normal(self, long_history):
        """正常值不应触发预警。"""
        mean_val = long_history["skew_spread"].mean()
        # 接近均值的值
        result = compute_rolling_zscore(long_history, mean_val + 0.001, window=90)

        assert result["is_alert"] == False  # numpy bool → 用 == 比较
        assert abs(result["z_score"]) < 2.0


class TestSeverityClassification:
    """预警严重程度分级测试。"""

    def test_normal_severity(self):
        assert _classify_severity(1.5, 2.0) == "normal"

    def test_elevated_severity(self):
        assert _classify_severity(2.5, 2.0) == "elevated"

    def test_high_severity(self):
        assert _classify_severity(3.5, 2.0) == "high"

    def test_extreme_severity(self):
        assert _classify_severity(5.0, 2.0) == "extreme"

    def test_nan_severity(self):
        assert _classify_severity(float("nan"), 2.0) == "unknown"

    def test_negative_zscore_severity(self):
        """负 Z-Score 也应按绝对值分级。"""
        assert _classify_severity(-3.0, 2.0) == "high"


class TestAlertCheck:
    """全标的预警检查测试。"""

    def test_check_all_tickers_empty_history(self):
        """空历史数据不应触发任何预警。"""
        alerts = check_all_ticker_alerts(
            {"SPY": 0.06, "QQQ": 0.07},
            pd.DataFrame(),
        )

        assert len(alerts) == 0

    def test_check_all_tickers_with_history(self):
        """有历史数据时应正常检查。"""
        dates = pd.date_range("2023-01-01", periods=100, freq="B")
        np.random.seed(42)
        df = pd.DataFrame({
            "date": dates.tolist() * 2,
            "ticker": ["SPY"] * 100 + ["QQQ"] * 100,
            "skew_spread": list(np.random.normal(0.05, 0.01, 100))
            + list(np.random.normal(0.04, 0.008, 100)),
        })

        alerts = check_all_ticker_alerts(
            {"SPY": 0.06, "QQQ": 0.045},
            df,
            window=90,
            threshold=2.0,
        )

        # 正常情况下不应触发（值接近均值）
        assert all(a["z_score"] is not None for a in alerts)


class TestFormatAlertSummary:
    """预警摘要格式化测试。"""

    def test_empty_alerts(self):
        result = format_alert_summary([])
        assert "未触发预警" in result

    def test_with_alerts(self):
        alerts = [{
            "ticker": "SPY",
            "skew_value": 0.08,
            "z_score": 3.2,
            "rolling_mean": 0.05,
            "rolling_std": 0.01,
            "severity": "extreme",
        }]
        result = format_alert_summary(alerts)
        assert "SPY" in result
        assert "EXTREME" in result.upper()
