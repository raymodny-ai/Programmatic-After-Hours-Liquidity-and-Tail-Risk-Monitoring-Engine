"""
VXN 自动化告警引擎 (vxn_alert_engine.py) v1.2.1

功能：
- 基于多维度信号的分层状态机，决定 VXN 风险等级
- 积分制评分 + 两日确认 + 冷却/升级机制
- 不产生任何交易建议，仅做风险状态识别

核心告警维度：
    1. 绝对压力: VXN 当前水平
    2. 历史异常: VXN Z-Score（252 日滚动）
    3. 加速上升: VXN 5d / 20d 收益率
    4. 相对压力: VXN-VIX spread Z-Score
    5. QQQ Skew 共振: QQQ 25Δ Skew Z-Score

状态机层级: normal → watch → elevated → high → critical
"""

from __future__ import annotations

import json as _json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from config.settings import PROCESSED_DATA_DIR, VXN_ALERT_CONFIG


# ──────────────────────────────────────────────────────────────────────────────
# 可配置阈值
# ──────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class VXNThresholds:
    """VXN 告警阈值容器（不可变）。

    所有参数均可通过 config/risk_thresholds.yaml 覆盖。
    """

    history_window: int = 252
    percentile_window: int = 252
    momentum_short_days: int = 5
    momentum_long_days: int = 20

    watch_z: float = 1.0
    elevated_z: float = 2.0
    high_z: float = 2.5
    critical_z: float = 3.0

    watch_percentile: float = 0.80
    elevated_percentile: float = 0.95
    critical_percentile: float = 0.99

    watch_return_5d: float = 0.15
    elevated_return_5d: float = 0.25

    absolute_high: float = 35.0
    absolute_critical: float = 45.0

    relative_vxn_vix_z: float = 2.0
    qqq_skew_z: float = 2.0

    # 通知策略
    cooldown_hours: int = 24
    elevated_confirmation_days: int = 2
    resolved_after_normal_days: int = 3

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None = None) -> VXNThresholds:
        """从 YAML 配置字典创建阈值实例。"""
        if cfg is None:
            cfg = VXN_ALERT_CONFIG or {}

        zs = cfg.get("zscore", {})
        pct = cfg.get("percentile", {})
        r5d = cfg.get("return_5d", {})
        abs_lvl = cfg.get("absolute_level", {})
        rel = cfg.get("relative_vxn_vix", {})
        qqq = cfg.get("qqq_skew", {})
        notif = cfg.get("notification", {})

        return cls(
            history_window=int(cfg.get("history_window", 252)),
            percentile_window=int(cfg.get("percentile_window", 252)),
            momentum_short_days=int(cfg.get("momentum_short_days", 5)),
            momentum_long_days=int(cfg.get("momentum_long_days", 20)),
            watch_z=float(zs.get("watch", 1.0)),
            elevated_z=float(zs.get("elevated", 2.0)),
            high_z=float(zs.get("high", 2.5)),
            critical_z=float(zs.get("critical", 3.0)),
            watch_percentile=float(pct.get("watch", 0.80)),
            elevated_percentile=float(pct.get("elevated", 0.95)),
            critical_percentile=float(pct.get("critical", 0.99)),
            watch_return_5d=float(r5d.get("watch", 0.15)),
            elevated_return_5d=float(r5d.get("elevated", 0.25)),
            absolute_high=float(abs_lvl.get("high", 35.0)),
            absolute_critical=float(abs_lvl.get("critical", 45.0)),
            relative_vxn_vix_z=float(rel.get("zscore_threshold", 2.0)),
            qqq_skew_z=float(qqq.get("zscore_threshold", 2.0)),
            cooldown_hours=int(notif.get("cooldown_hours", 24)),
            elevated_confirmation_days=int(
                notif.get("elevated_confirmation_days", 2)
            ),
            resolved_after_normal_days=int(
                notif.get("resolved_after_normal_days", 3)
            ),
        )


# ──────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────────────────────────────────────

def _percentile_of_last(series: pd.Series) -> float:
    """计算序列末尾值在序列中的分位数（0-1）。"""
    clean = series.dropna()
    if clean.empty:
        return np.nan
    return float(clean.rank(pct=True).iloc[-1])


# ──────────────────────────────────────────────────────────────────────────────
# 核心告警计算
# ──────────────────────────────────────────────────────────────────────────────

def calculate_vxn_alert(
    vxn_history: pd.DataFrame,
    vix_history: pd.DataFrame | None = None,
    qqq_skew_z: float | None = None,
    thresholds: VXNThresholds | None = None,
) -> dict[str, Any]:
    """计算 VXN 多维度风险状态（积分制分层状态机）。

    不产生任何交易建议。

    Args:
        vxn_history: VXN 指数历史数据 (date, close)
        vix_history: VIX 指数历史数据 (date, close)，用于相对压力
        qqq_skew_z: QQQ 25Δ Skew Z-Score，用于多维共振确认
        thresholds: 阈值配置，None 时从 YAML 加载

    Returns:
        {
            "status": "ok" | "unavailable" | "insufficient_history",
            "as_of_date": str,
            "vxn_level": float,
            "vxn_z_score": float,
            "vxn_percentile": float,
            "vxn_return_5d": float,
            "vxn_return_20d": float | None,
            "vxn_vix_relative_z": float | None,
            "qqq_skew_z": float | None,
            "score": int,
            "severity": "normal" | "watch" | "elevated" | "high" | "critical",
            "is_alert": bool,
            "reasons": list[str],
        }
    """
    if thresholds is None:
        thresholds = VXNThresholds.from_config()

    # ── 输入校验 ──
    required_cols = {"date", "close"}
    if vxn_history.empty or not required_cols.issubset(vxn_history.columns):
        return {
            "status": "unavailable",
            "is_alert": False,
            "reason": "missing_vxn_history",
        }

    vxn = vxn_history.copy()
    vxn["date"] = pd.to_datetime(vxn["date"], errors="coerce")
    vxn["close"] = pd.to_numeric(vxn["close"], errors="coerce")
    vxn = vxn.dropna(subset=["date", "close"]).sort_values("date")

    min_required = max(20, thresholds.momentum_short_days + 1)
    if len(vxn) < min_required:
        return {
            "status": "insufficient_history",
            "is_alert": False,
            "observations": len(vxn),
        }

    # ── 维度 1: 绝对压力 (VXN level) ──
    level_window = vxn["close"].tail(thresholds.history_window)
    current = float(level_window.iloc[-1])

    # ── 维度 2: 历史异常 (VXN Z-Score) ──
    mean = float(level_window.mean())
    std = float(level_window.std(ddof=1))
    z_score = 0.0 if std == 0 or np.isnan(std) else (current - mean) / std

    # ── 维度 2b: 滚动分位数 ──
    percentile_series = vxn["close"].tail(thresholds.percentile_window)
    percentile = _percentile_of_last(percentile_series)

    # ── 维度 3: 加速上升 (5d / 20d return) ──
    ret_5d = (
        current / float(vxn["close"].iloc[-1 - thresholds.momentum_short_days]) - 1.0
    )
    if len(vxn) > thresholds.momentum_long_days:
        ret_20d = (
            current / float(vxn["close"].iloc[-1 - thresholds.momentum_long_days]) - 1.0
        )
    else:
        ret_20d = np.nan

    # ── 维度 4: 相对压力 (VXN-VIX spread Z-Score) ──
    relative_z = np.nan
    if vix_history is not None and not vix_history.empty:
        vix = vix_history[["date", "close"]].copy()
        vix["date"] = pd.to_datetime(vix["date"], errors="coerce")
        vix["close"] = pd.to_numeric(vix["close"], errors="coerce")

        merged = (
            vxn[["date", "close"]]
            .rename(columns={"close": "vxn"})
            .merge(
                vix.rename(columns={"close": "vix"}),
                on="date",
                how="inner",
            )
            .dropna()
            .sort_values("date")
        )

        if len(merged) >= 20:
            relative = (merged["vxn"] - merged["vix"]).tail(
                thresholds.history_window
            )
            rel_mean = float(relative.mean())
            rel_std = float(relative.std(ddof=1))
            if rel_std > 1e-10 and not np.isnan(rel_std):
                relative_z = float((relative.iloc[-1] - rel_mean) / rel_std)
            else:
                relative_z = 0.0

    # ══════════════════════════════════════════════════════════════════════
    # 积分制评分（每个维度独立计分）
    # ══════════════════════════════════════════════════════════════════════

    score = 0
    reasons: list[str] = []

    # VXN Z-Score
    if z_score >= thresholds.watch_z:
        score += 1
        reasons.append(f"VXN Z={z_score:.2f}")

    # 滚动分位数
    if not np.isnan(percentile) and percentile >= thresholds.watch_percentile:
        score += 1
        reasons.append(f"VXN percentile={percentile:.1%}")

    # 短期动量
    if ret_5d >= thresholds.watch_return_5d:
        score += 1
        reasons.append(f"VXN 5d={ret_5d:.1%}")

    # 绝对点位熔断器
    if current >= thresholds.absolute_high:
        score += 1
        reasons.append(f"VXN level={current:.2f}")

    # VXN-VIX 相对压力（权重 2）
    if np.isfinite(relative_z) and relative_z >= thresholds.relative_vxn_vix_z:
        score += 2
        reasons.append(f"VXN-VIX Z={relative_z:.2f}")

    # QQQ Skew 共振（权重 2）
    if qqq_skew_z is not None and np.isfinite(qqq_skew_z):
        if qqq_skew_z >= thresholds.qqq_skew_z:
            score += 2
            reasons.append(f"QQQ skew Z={qqq_skew_z:.2f}")

    # ══════════════════════════════════════════════════════════════════════
    # 分层判定（由高到低）
    # ══════════════════════════════════════════════════════════════════════

    # critical: 最高级别 — 多维共振或极端统计
    if (
        z_score >= thresholds.critical_z
        or percentile >= thresholds.critical_percentile
        or current >= thresholds.absolute_critical
        or (
            z_score >= thresholds.elevated_z
            and np.isfinite(relative_z)
            and relative_z >= thresholds.relative_vxn_vix_z
            and qqq_skew_z is not None
            and qqq_skew_z >= thresholds.qqq_skew_z
        )
    ):
        severity = "critical"
    # high: 高积分 or Z ≥ 2.5 且 5d 涨幅 ≥ 25%
    elif (
        score >= 4
        or (
            z_score >= thresholds.high_z
            and ret_5d >= thresholds.elevated_return_5d
        )
    ):
        severity = "high"
    # elevated: 中等积分 or Z ≥ 2.0 or 分位 ≥ 95% or 5d ≥ 25%
    elif (
        score >= 2
        or z_score >= thresholds.elevated_z
        or percentile >= thresholds.elevated_percentile
        or ret_5d >= thresholds.elevated_return_5d
    ):
        severity = "elevated"
    # watch: 低积分 — 仅关注，不推送
    elif score >= 1:
        severity = "watch"
    else:
        severity = "normal"

    return {
        "status": "ok",
        "as_of_date": vxn["date"].iloc[-1].strftime("%Y-%m-%d"),
        "vxn_level": round(current, 4),
        "vxn_z_score": round(float(z_score), 4),
        "vxn_percentile": round(percentile, 4)
        if not np.isnan(percentile)
        else None,
        "vxn_return_5d": round(float(ret_5d), 6),
        "vxn_return_20d": round(float(ret_20d), 6)
        if np.isfinite(ret_20d)
        else None,
        "vxn_vix_relative_z": round(float(relative_z), 4)
        if np.isfinite(relative_z)
        else None,
        "qqq_skew_z": qqq_skew_z,
        "score": score,
        "severity": severity,
        "is_alert": severity in {"elevated", "high", "critical"},
        "reasons": reasons,
    }


# ──────────────────────────────────────────────────────────────────────────────
# 两日确认与状态机
# ──────────────────────────────────────────────────────────────────────────────

def confirm_alert(
    daily_signals: pd.DataFrame,
    severity: str,
) -> bool:
    """两日确认逻辑：防止单日噪声触发反复告警。

    Args:
        daily_signals: 按时间排序的每日 severity 记录 DataFrame（需含 'severity' 列）
        severity: 当日判定等级

    Returns:
        True 表示应发出外部通知
    """
    # critical: 单日立即触发
    if severity == "critical":
        return True

    # high: 当日触发推送
    if severity == "high":
        return True

    # normal/watch: 不推送
    if severity in {"normal", "watch"}:
        return False

    # elevated: 需两日确认
    if severity == "elevated":
        if "severity" not in daily_signals.columns or daily_signals.empty:
            return False

        last_two = daily_signals["severity"].tail(2).tolist()
        # 昨日 elevated/high/critical 且今日 elevated → 确认
        return sum(
            level in {"elevated", "high", "critical"} for level in last_two
        ) >= 2

    return False


# ──────────────────────────────────────────────────────────────────────────────
# 告警状态持久化管理
# ──────────────────────────────────────────────────────────────────────────────

_ALERT_STATE_FILE = PROCESSED_DATA_DIR / "vxn_alert_state.json"


class AlertStateManager:
    """VXN 告警状态管理器：冷却、升级、解除通知。

    持久化文件: data/processed/vxn_alert_state.json

    推送规则:
        - 同严重等级：cooldown_hours 内仅推送一次
        - 严重等级升级：即使在冷却期也立即推送
        - 连续 resolved_after_normal_days 日低于 watch：发送解除通知
        - 数据不可用：记录数据质量告警，不显示"正常"
    """

    def __init__(
        self,
        state_file: Path | None = None,
        cooldown_hours: int | None = None,
        resolved_days: int | None = None,
    ) -> None:
        self.state_file = state_file or _ALERT_STATE_FILE
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        thresholds = VXNThresholds.from_config()
        self.cooldown_hours = cooldown_hours or thresholds.cooldown_hours
        self.resolved_days = resolved_days or thresholds.resolved_after_normal_days

    def _load(self) -> dict[str, Any]:
        if self.state_file.exists():
            try:
                return _json.loads(self.state_file.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("告警状态文件损坏，重置")
        return {
            "signal": "VXN_TECH_RISK",
            "last_severity": "normal",
            "last_notified_at": None,
            "last_notified_date": None,
            "last_reasons": [],
            "severity_history": [],
        }

    def _save(self, state: dict[str, Any]) -> None:
        tmp_path = self.state_file.with_suffix(".tmp")
        tmp_path.write_text(
            _json.dumps(state, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        tmp_path.replace(self.state_file)

    def should_notify(
        self,
        severity: str,
        as_of_date: str,
        reasons: list[str] | None = None,
    ) -> tuple[bool, str]:
        """判断当前应执行的动作。

        Args:
            severity: 当日判定等级
            as_of_date: 日期字符串 "YYYY-MM-DD"
            reasons: 本次告警的触发原因列表（用于持久化复盘）

        Returns:
            (should_send: bool, action: str)
            action 取值: "send" | "upgrade" | "resolved" | "cooldown" | "silent"
        """
        state = self._load()

        prev_severity = state.get("last_severity", "normal")
        last_notified = state.get("last_notified_at")
        last_notified_date = state.get("last_notified_date")

        now = datetime.now()

        # ── 升级判定（立即推送）──
        _rank = {"normal": 0, "watch": 1, "elevated": 2, "high": 3, "critical": 4}
        is_upgrade = _rank.get(severity, 0) > _rank.get(prev_severity, 0)

        if is_upgrade and severity in {"elevated", "high", "critical"}:
            state["last_severity"] = severity
            state["last_notified_at"] = now.isoformat()
            state["last_notified_date"] = as_of_date
            state["last_reasons"] = reasons or []
            self._save(state)
            return True, "upgrade"

        # ── 冷却检查 ──
        if last_notified and last_notified_date == as_of_date:
            # 同日已推送，冷却
            return False, "cooldown"

        if last_notified:
            try:
                last_dt = datetime.fromisoformat(last_notified)
                if (now - last_dt) < timedelta(hours=self.cooldown_hours):
                    if severity == prev_severity:
                        return False, "cooldown"
            except (ValueError, TypeError):
                pass

        # ── 风险解除 ──
        history: list[dict] = state.get("severity_history", [])
        # 追加当日记录
        history.append({"date": as_of_date, "severity": severity})
        # 只保留最近 N+5 天
        history = history[-(self.resolved_days + 5):]
        state["severity_history"] = history

        if severity in {"normal", "watch"}:
            recent = [
                h["severity"]
                for h in history[-self.resolved_days:]
                if h.get("severity") in {"normal", "watch"}
            ]
            if len(recent) >= self.resolved_days and prev_severity not in {
                "normal",
                "watch",
            }:
                state["last_severity"] = severity
                state["last_notified_at"] = now.isoformat()
                state["last_notified_date"] = as_of_date
                state["last_reasons"] = reasons or []
                self._save(state)
                return True, "resolved"

        # ── 正常推送 ──
        if severity in {"elevated", "high", "critical"}:
            state["last_severity"] = severity
            state["last_notified_at"] = now.isoformat()
            state["last_notified_date"] = as_of_date
            state["last_reasons"] = reasons or []
            self._save(state)
            return True, "send"

        # silent
        state["last_severity"] = severity
        state["last_reasons"] = reasons or []
        self._save(state)
        return False, "silent"

    def get_last_state(self) -> dict[str, Any]:
        """获取上次持久化的告警状态。"""
        return self._load()
