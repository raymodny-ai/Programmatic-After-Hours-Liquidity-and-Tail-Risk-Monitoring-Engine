"""
VXN 自动化告警引擎单元测试 (v1.2.1)

测试范围:
    1. VXNThresholds 从 YAML 配置加载
    2. calculate_vxn_alert 所有严重级别（normal → critical）
    3. 积分制评分正确性
    4. 边界情况：空数据、不足历史、缺失列
    5. confirm_alert 两日确认逻辑
    6. AlertStateManager 冷却/升级/解除
"""

import json as _json
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.calculation.vxn_alert_engine import (
    AlertStateManager,
    VXNThresholds,
    _percentile_of_last,
    calculate_vxn_alert,
    confirm_alert,
)


# ────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────


@pytest.fixture
def vxn_normal_history() -> pd.DataFrame:
    """构造正常 VXN 历史数据（252 天，均值≈22）。"""
    dates = pd.date_range("2024-01-01", periods=252, freq="B")
    np.random.seed(42)
    return pd.DataFrame({
        "date": dates,
        "close": np.random.normal(22, 2, 252).clip(14, 35),
    })


@pytest.fixture
def vix_normal_history() -> pd.DataFrame:
    """构造正常 VIX 历史数据（均值≈18）。"""
    dates = pd.date_range("2024-01-01", periods=252, freq="B")
    np.random.seed(7)
    return pd.DataFrame({
        "date": dates,
        "close": np.random.normal(18, 2, 252).clip(11, 30),
    })


@pytest.fixture
def vxn_spike_history() -> pd.DataFrame:
    """构造 VXN 尖峰数据（最后一日 +3σ）。"""
    dates = pd.date_range("2024-01-01", periods=252, freq="B")
    closes = [20.0] * 251 + [30.0]  # last day spike
    return pd.DataFrame({"date": dates, "close": closes})


@pytest.fixture
def vxn_critical_history() -> pd.DataFrame:
    """构造 critical 级 VXN 数据（VXN=50, 极端 Z-Score）。"""
    dates = pd.date_range("2024-01-01", periods=252, freq="B")
    closes = [20.0] * 251 + [50.0]
    return pd.DataFrame({"date": dates, "close": closes})


@pytest.fixture
def default_thresholds() -> VXNThresholds:
    return VXNThresholds.from_config()


# ────────────────────────────────────────────────────────────
# VXNThresholds
# ────────────────────────────────────────────────────────────


class TestVXNThresholds:

    def test_defaults_are_sensible(self, default_thresholds):
        """默认阈值应在合理范围内。"""
        assert default_thresholds.history_window == 252
        assert default_thresholds.watch_z == 1.0
        assert default_thresholds.elevated_z == 2.0
        assert default_thresholds.critical_z == 3.0
        assert 0 < default_thresholds.cooldown_hours < 168  # 1周以内

    def test_from_config_empty_dict(self):
        """空配置应返回默认值。"""
        t = VXNThresholds.from_config({})
        assert t.watch_z == 1.0
        assert t.elevated_z == 2.0

    def test_from_config_partial_override(self):
        """部分覆盖配置应保留其余默认值。"""
        t = VXNThresholds.from_config({
            "zscore": {"watch": 1.5, "critical": 3.5},
        })
        assert t.watch_z == 1.5
        assert t.critical_z == 3.5
        assert t.elevated_z == 2.0  # 未覆盖，保持默认
        assert t.history_window == 252

    def test_from_config_full_override(self):
        """完整自定义配置应全部生效。"""
        cfg = {
            "history_window": 120,
            "zscore": {"watch": 0.8, "elevated": 1.5, "high": 2.0, "critical": 2.5},
            "percentile": {"watch": 0.70, "elevated": 0.90, "critical": 0.98},
            "return_5d": {"watch": 0.10, "elevated": 0.20},
            "absolute_level": {"high": 30.0, "critical": 40.0},
            "relative_vxn_vix": {"zscore_threshold": 1.5},
            "qqq_skew": {"zscore_threshold": 1.8},
            "notification": {"cooldown_hours": 12, "elevated_confirmation_days": 1, "resolved_after_normal_days": 2},
        }
        t = VXNThresholds.from_config(cfg)
        assert t.history_window == 120
        assert t.watch_z == 0.8
        assert t.high_z == 2.0
        assert t.elevated_percentile == 0.90
        assert t.absolute_high == 30.0
        assert t.cooldown_hours == 12


# ────────────────────────────────────────────────────────────
# _percentile_of_last
# ────────────────────────────────────────────────────────────


class TestPercentileOfLast:

    def test_extreme_high(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 100.0])
        pct = _percentile_of_last(s)
        assert pct == 1.0

    def test_extreme_low(self):
        """末尾值为最低时，分位应为 0.2。"""
        s = pd.Series([5.0, 4.0, 3.0, 2.0, 0.1])
        pct = _percentile_of_last(s)
        assert pct == 0.2  # 末尾值 0.1 排最低

    def test_all_equal(self):
        """相等值时 rank(pct=True) 使用 average 方法，末尾值分位 ≈0.55。"""
        s = pd.Series([3.0] * 10)
        pct = _percentile_of_last(s)
        # rank(pct=True, method='average') 对相等值取平均秩
        assert 0.5 < pct < 0.6

    def test_empty_returns_nan(self):
        assert np.isnan(_percentile_of_last(pd.Series([], dtype=float)))


# ────────────────────────────────────────────────────────────
# calculate_vxn_alert — 严重级别
# ────────────────────────────────────────────────────────────


class TestCalculateVxnAlert:

    def test_normal_regime(self, vxn_normal_history, default_thresholds):
        """正常波动率范围应返回 normal。"""
        result = calculate_vxn_alert(vxn_normal_history, thresholds=default_thresholds)
        assert result["status"] == "ok"
        assert result["severity"] in ("normal", "watch")
        assert result["is_alert"] is False

    def test_elevated_from_spike(self, vxn_spike_history, default_thresholds):
        """VXN 尖峰应至少触发 elevated。"""
        result = calculate_vxn_alert(vxn_spike_history, thresholds=default_thresholds)
        assert result["status"] == "ok"
        assert result["severity"] in ("elevated", "high", "critical")
        assert result["vxn_z_score"] >= 2.0

    def test_critical_from_extreme(self, vxn_critical_history, default_thresholds):
        """VXN=50 应触发 critical。"""
        result = calculate_vxn_alert(
            vxn_critical_history, thresholds=default_thresholds,
        )
        assert result["severity"] == "critical"
        assert result["is_alert"] is True
        assert result["score"] >= 2  # Z + level

    def test_score_increases_with_dimensions(self, vxn_spike_history, vix_normal_history, default_thresholds):
        """多维度触发应增加积分。"""
        result_spike = calculate_vxn_alert(
            vxn_spike_history, thresholds=default_thresholds,
        )
        # 加入 VIX 相对压力 + QQQ skew
        result_full = calculate_vxn_alert(
            vxn_spike_history,
            vix_history=vix_normal_history,
            qqq_skew_z=2.5,
            thresholds=default_thresholds,
        )
        assert result_full["score"] >= result_spike["score"]

    def test_qqq_skew_resonance(self, vxn_spike_history, default_thresholds):
        """QQQ Skew Z=2.5 应贡献 2 分。"""
        without = calculate_vxn_alert(
            vxn_spike_history, thresholds=default_thresholds,
        )
        with_qqq = calculate_vxn_alert(
            vxn_spike_history,
            qqq_skew_z=2.5,
            thresholds=default_thresholds,
        )
        assert with_qqq["score"] >= without["score"] + 2

    def test_vxn_vix_relative_pressure(self, vxn_spike_history, vix_normal_history, default_thresholds):
        """VXN 尖峰 + VIX 正常 → 相对压力 Z 应显著。"""
        result = calculate_vxn_alert(
            vxn_spike_history,
            vix_history=vix_normal_history,
            thresholds=default_thresholds,
        )
        assert result["vxn_vix_relative_z"] is not None
        assert np.isfinite(result["vxn_vix_relative_z"])

    def test_all_dimensions_simultaneously(self, vxn_critical_history, vix_normal_history, default_thresholds):
        """所有维度同时触发应达到 critical + 高分。"""
        result = calculate_vxn_alert(
            vxn_critical_history,
            vix_history=vix_normal_history,
            qqq_skew_z=3.0,
            thresholds=default_thresholds,
        )
        assert result["severity"] == "critical"
        assert result["score"] >= 4
        assert len(result["reasons"]) >= 3

    def test_absolute_circuit_breaker(self, default_thresholds):
        """绝对点位熔断器：VXN >= 45 直接 critical。"""
        dates = pd.date_range("2024-01-01", periods=252, freq="B")
        closes = [20.0] * 251 + [46.0]
        df = pd.DataFrame({"date": dates, "close": closes})

        result = calculate_vxn_alert(df, thresholds=default_thresholds)
        assert result["severity"] == "critical"
        assert "VXN level" in "|".join(result["reasons"])

    def test_absolute_high_to_high(self, default_thresholds):
        """绝对点位熔断器：VXN >= 35 至少 high（如果无其他触发）。"""
        dates = pd.date_range("2024-01-01", periods=252, freq="B")
        closes = [18.0] * 251 + [36.0]
        df = pd.DataFrame({"date": dates, "close": closes})

        result = calculate_vxn_alert(df, thresholds=default_thresholds)
        # 有 Z 分数和绝对点位，积分至少 2
        assert result["score"] >= 2


# ────────────────────────────────────────────────────────────
# calculate_vxn_alert — 边界情况
# ────────────────────────────────────────────────────────────


class TestCalculateVxnAlertEdgeCases:

    def test_empty_dataframe(self, default_thresholds):
        """空 DataFrame 应返回 unavailable。"""
        result = calculate_vxn_alert(pd.DataFrame(), thresholds=default_thresholds)
        assert result["status"] == "unavailable"
        assert result["is_alert"] is False

    def test_missing_close_column(self, default_thresholds):
        """缺少 close 列应返回 unavailable。"""
        df = pd.DataFrame({"date": ["2024-01-01"], "value": [20.0]})
        result = calculate_vxn_alert(df, thresholds=default_thresholds)
        assert result["status"] == "unavailable"

    def test_insufficient_history(self, default_thresholds):
        """不足 20 条应返回 insufficient_history。"""
        dates = pd.date_range("2024-06-01", periods=10, freq="B")
        df = pd.DataFrame({"date": dates, "close": [20.0] * 10})
        result = calculate_vxn_alert(df, thresholds=default_thresholds)
        assert result["status"] == "insufficient_history"

    def test_with_nan_values(self, default_thresholds):
        """含 NaN 值的列应被自动清理。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        closes = [float("nan")] * 5 + [20.0] * 25
        df = pd.DataFrame({"date": dates, "close": closes})
        result = calculate_vxn_alert(df, thresholds=default_thresholds)
        assert result["status"] == "ok"

    def test_zero_standard_deviation(self, default_thresholds):
        """标准差为 0 时 Z-Score 应为 0。"""
        dates = pd.date_range("2024-01-01", periods=30, freq="B")
        df = pd.DataFrame({"date": dates, "close": [22.0] * 30})
        result = calculate_vxn_alert(df, thresholds=default_thresholds)
        assert result["vxn_z_score"] == 0.0

    def test_all_severities_reachable(self, default_thresholds):
        """所有 5 个严重级别都有返回路径。"""
        valid = {"normal", "watch", "elevated", "high", "critical"}

        # normal: 随机正常数据
        dates = pd.date_range("2024-01-01", periods=252, freq="B")
        np.random.seed(42)
        normal = pd.DataFrame({
            "date": dates,
            "close": np.random.normal(20, 2, 252).clip(14, 30),
        })
        r = calculate_vxn_alert(normal, thresholds=default_thresholds)
        assert r["severity"] in valid

        # critical: 极端值
        spike = pd.DataFrame({
            "date": dates,
            "close": [20.0] * 251 + [50.0],
        })
        r = calculate_vxn_alert(spike, thresholds=default_thresholds)
        assert r["severity"] == "critical"


# ────────────────────────────────────────────────────────────
# confirm_alert — 两日确认
# ────────────────────────────────────────────────────────────


class TestConfirmAlert:

    def test_critical_always_confirms(self):
        """critical 级别单日立即确认。"""
        df = pd.DataFrame({"severity": ["normal"]})
        assert confirm_alert(df, "critical") is True

    def test_high_with_prior_high_confirms(self):
        """high 级别在已有 high 历史时确认。"""
        df = pd.DataFrame({"severity": ["high"]})
        assert confirm_alert(df, "high") is True

    def test_high_with_normal_history_confirms(self):
        """high 级别即使昨日正常，当日也推送。"""
        df = pd.DataFrame({"severity": ["normal"]})
        assert confirm_alert(df, "high") is True

    def test_elevated_needs_two_confirmation(self):
        """elevated 需连续两日。"""
        # 昨日 elevated，今日 elevated → 确认（最后两行均为 elevated+）
        df = pd.DataFrame({"severity": ["elevated", "elevated"]})
        assert confirm_alert(df, "elevated") is True

        # 昨日 normal，今日 elevated → 不确认（仅 1 行 elevated）
        df2 = pd.DataFrame({"severity": ["normal", "normal"]})
        assert confirm_alert(df2, "elevated") is False

    def test_elevated_after_high_confirms(self):
        """昨日 high，今日 elevated 也应确认。"""
        df = pd.DataFrame({"severity": ["high", "high"]})
        assert confirm_alert(df, "elevated") is True

    def test_watch_never_confirms(self):
        """watch 级别不推送。"""
        df = pd.DataFrame({"severity": ["watch", "watch"]})
        assert confirm_alert(df, "watch") is False

    def test_normal_never_confirms(self):
        """normal 级别不推送。"""
        df = pd.DataFrame({"severity": ["normal", "normal"]})
        assert confirm_alert(df, "normal") is False

    def test_empty_history_with_high_confirms(self):
        """空历史时 high/critical 仍应推送。"""
        assert confirm_alert(pd.DataFrame(), "critical") is True
        assert confirm_alert(pd.DataFrame(), "high") is True

    def test_missing_severity_column(self):
        """缺少 severity 列时 high/critical 仍推送。"""
        df = pd.DataFrame({"date": ["2024-01-01"]})
        assert confirm_alert(df, "critical") is True
        assert confirm_alert(df, "high") is True


# ────────────────────────────────────────────────────────────
# AlertStateManager — 冷却/升级/解除
# ────────────────────────────────────────────────────────────


class TestAlertStateManager:

    @pytest.fixture
    def temp_state_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "vxn_alert_state.json"

    def test_first_alert_sends(self, temp_state_file):
        """首次告警应立即发送。"""
        mgr = AlertStateManager(state_file=temp_state_file, cooldown_hours=24, resolved_days=3)
        should_send, action = mgr.should_notify("elevated", "2026-07-10")
        assert should_send is True
        assert action == "upgrade"  # 从 normal 升级到 elevated

    def test_same_day_cooldown(self, temp_state_file):
        """同日同级别不重复推送。"""
        mgr = AlertStateManager(state_file=temp_state_file, cooldown_hours=24, resolved_days=3)
        mgr.should_notify("elevated", "2026-07-10")  # 第一次
        should_send, action = mgr.should_notify("elevated", "2026-07-10")  # 第二次同天
        assert should_send is False
        assert action == "cooldown"

    def test_upgrade_breaks_cooldown(self, temp_state_file):
        """升级即使在冷却期也应触发。"""
        mgr = AlertStateManager(state_file=temp_state_file, cooldown_hours=24, resolved_days=3)
        mgr.should_notify("elevated", "2026-07-10")
        # 升级到 high — 即使在同一天也立即推送（升级打破冷却期）
        should_send, action = mgr.should_notify("high", "2026-07-10")
        # 根据规范：严重等级升级应立即推送
        assert should_send is True
        assert action == "upgrade"

    def test_downgrade_to_watch_no_notify(self, temp_state_file):
        """降级到 watch 不推送。"""
        mgr = AlertStateManager(state_file=temp_state_file, cooldown_hours=24, resolved_days=3)
        mgr.should_notify("elevated", "2026-07-10")
        should_send, action = mgr.should_notify("watch", "2026-07-11")
        assert should_send is False
        assert action == "silent"

    def test_state_persisted(self, temp_state_file):
        """状态应正确持久化。"""
        mgr = AlertStateManager(state_file=temp_state_file, cooldown_hours=24, resolved_days=3)
        mgr.should_notify("elevated", "2026-07-10")

        state = mgr.get_last_state()
        assert state["last_severity"] == "elevated"
        assert state["last_notified_date"] == "2026-07-10"

    def test_get_last_state_empty_file(self, temp_state_file):
        """无文件时应返回默认状态。"""
        mgr = AlertStateManager(state_file=temp_state_file)
        state = mgr.get_last_state()
        assert state["last_severity"] == "normal"
        assert state["signal"] == "VXN_TECH_RISK"
