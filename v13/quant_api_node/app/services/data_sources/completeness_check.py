"""期权链数据完备性校验 (V1.3 阶段 2.2)。

核心需求：
1. 深度虚值 (deep OTM) 覆盖率：OTM 期权覆盖到 ±30% moneyness
2. 远月合约覆盖：至少覆盖到 90 DTE 之后的 3 个到期日
3. 缺口检测：单点 IV 是否与相邻 strike 平滑
4. 完整性审计分数（0~1）：< 0.7 时降级为 fallback_estimated
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


# ── 阈值（可由 V1.3 用户配置覆盖） ────────────────────────────────────

_DEFAULT = {
    "deep_otm_min_pct": 0.70,        # spot × 0.70 为最深 put
    "deep_otm_max_pct": 1.30,        # spot × 1.30 为最深 call
    "min_dte_count": 3,              # 至少 3 个 > 90 DTE 到期日
    "min_iv_strikes_per_expiry": 10, # 每个到期至少 10 个 strike 有 IV
    "max_iv_jump_pct": 0.30,         # 相邻 strike 间 IV 突变 30% 视为缺口
    "min_completeness": 0.70,        # 综合分 < 此值则降级
}


def validate_options_surface(
    surface: dict[str, Any],
    spot: float,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """验证 3D 表面数据完备性。

    Args:
        surface: ThetaData 返回的 3D 表面 dict
                {strikes, expirations, iv, oi, volume, spot}
        spot: 当前标的价格
        config: 可选阈值覆盖

    Returns:
        {
            "completeness": float (0~1),
            "otm_coverage": {min_moneyness, max_moneyness, ratio},
            "data_quality": "primary" | "fallback" | "unavailable",
            "issues": [...],   # 描述缺口的中文列表
            "rank": ...        # 综合评级
        }
    """
    cfg = {**_DEFAULT, **(config or {})}
    strikes = surface.get("strikes") or []
    expirations = surface.get("expirations") or []
    iv = surface.get("iv") or []
    dte_list = surface.get("dte_list") or []

    issues: list[str] = []

    # ── 1. 深度 OTM 覆盖率 ──────────────────────────────────────────
    otm_min = spot * cfg["deep_otm_min_pct"]
    otm_max = spot * cfg["deep_otm_max_pct"]
    deep_puts = [s for s in strikes if s <= otm_min]
    deep_calls = [s for s in strikes if s >= otm_max]
    otm_min_actual = min(strikes) if strikes else None
    otm_max_actual = max(strikes) if strikes else None

    otm_ratio = 0.0
    if otm_min_actual is not None and otm_max_actual is not None and spot > 0:
        # 0 = 仅 ATM, 1 = 覆盖到 ±30%
        width_actual = (otm_max_actual - otm_min_actual) / spot
        width_target = cfg["deep_otm_max_pct"] - cfg["deep_otm_min_pct"]
        otm_ratio = min(1.0, width_actual / width_target) if width_target > 0 else 1.0

    if otm_ratio < 1.0:
        missing = "depth_OTM"
        if not deep_puts:
            missing = "深度虚值 Put（≤ spot×0.70）"
        elif not deep_calls:
            missing = "深度虚值 Call（≥ spot×1.30）"
        else:
            missing = f"深度虚值边界（当前覆盖 {otm_ratio:.0%}）"
        issues.append(f"OTM 不完整：{missing}")

    # ── 2. 远月合约覆盖 ──────────────────────────────────────────────
    far_count = sum(1 for d in dte_list if d >= 90)
    if far_count < cfg["min_dte_count"]:
        issues.append(f"远月合约不足：仅 {far_count} 个 ≥90DTE 到期日（最少需 {cfg['min_dte_count']}）")

    # ── 3. 缺口检测（相邻 strike IV 突变） ──────────────────────────
    n_gaps = 0
    n_total_points = 0
    if isinstance(iv, list) and iv and isinstance(iv[0], list):
        # iv 是 [expirations][strikes] 二维数组
        for row in iv:
            prev_iv = None
            for v in row:
                if v is None or (isinstance(v, float) and math.isnan(v)):
                    continue
                n_total_points += 1
                if prev_iv is not None and prev_iv > 0:
                    jump = abs(v - prev_iv) / prev_iv
                    if jump > cfg["max_iv_jump_pct"]:
                        n_gaps += 1
                prev_iv = v
        gap_ratio = n_gaps / n_total_points if n_total_points else 0
        if gap_ratio > 0.05:
            issues.append(f"相邻 strike IV 缺口率 {gap_ratio:.1%} (突变 > 30% 的对数={n_gaps})")

    # ── 4. 综合分数 ──────────────────────────────────────────────────
    score_otm = otm_ratio
    score_far = min(1.0, far_count / cfg["min_dte_count"]) if cfg["min_dte_count"] else 1.0
    score_iv_complete = 1.0 - (n_gaps / n_total_points if n_total_points else 1.0)

    completeness = (score_otm * 0.4 + score_far * 0.3 + score_iv_complete * 0.3)
    quality = (
        "primary"
        if completeness >= cfg["min_completeness"]
        else "fallback"
        if completeness >= 0.4
        else "unavailable"
    )

    return {
        "completeness": round(completeness, 4),
        "otm_coverage": {
            "min_moneyness": round(otm_min_actual / spot, 4) if spot and otm_min_actual else None,
            "max_moneyness": round(otm_max_actual / spot, 4) if spot and otm_max_actual else None,
            "ratio": round(otm_ratio, 4),
        },
        "data_quality": quality,
        "issues": issues,
        "rank": 1 if quality == "primary" else 2 if quality == "fallback" else 3,
    }
