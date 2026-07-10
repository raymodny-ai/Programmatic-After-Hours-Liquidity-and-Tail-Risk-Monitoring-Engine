"""
固定 Delta 隐含波动率插值模块 (delta_interpolator.py)

功能：
- 基于 scipy 样条插值，从离散期权链中精准定位 25Δ Put 和 25Δ Call 的 IV
- 支持 Cubic Spline（三次样条）和 Linear 插值方法
- 对 Call 和 Put 分别插值（因为 IV Skew 在 Call/Put 之间是不连续的）

核心算法：
    1. 将期权链按 contract_type 分为 call 和 put 两组
    2. 对于每组，在 Delta (x) - IV (y) 平面上做样条插值
    3. 在插值曲线上查询目标 Delta 对应的 IV 值
    4. 对插值结果施加边界约束，防止外推产生不合理值

背景知识：
    期权链中很少存在精确 25Δ 的期权合约。
    通过插值可以在相邻 Delta 的期权之间平滑估计 25Δ 的 IV，
    这是业界计算 Risk Reversal (25Δ Skew) 的标准方法。
"""

from typing import Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger
from scipy.interpolate import CubicSpline, interp1d

from config.settings import (
    TARGET_DELTA_CALL,
    TARGET_DELTA_PUT,
    INTERPOLATION_METHOD,
)


class DeltaIVInterpolator:
    """
    Delta-IV 插值器。

    从期权链 DataFrame 中提取特定 Delta 对应的隐含波动率。

    用法:
        interp = DeltaIVInterpolator()
        iv_put_25d, iv_call_25d = interp.interpolate(df)
    """

    def __init__(
        self,
        target_delta_put: float = TARGET_DELTA_PUT,
        target_delta_call: float = TARGET_DELTA_CALL,
        method: str = INTERPOLATION_METHOD,
    ) -> None:
        """
        Args:
            target_delta_put: 目标 Put Delta（正数，如 0.25 表示 25Δ）
            target_delta_call: 目标 Call Delta（正数，如 0.25 表示 25Δ）
            method: 插值方法 ("cubic_spline" 或 "linear")
        """
        self.target_delta_put = target_delta_put
        self.target_delta_call = target_delta_call
        self.method = method

    def _interpolate_iv(
        self,
        deltas: np.ndarray,
        ivs: np.ndarray,
        target_delta: float,
    ) -> float:
        """
        在 (delta, iv) 平面上对目标 delta 插值得到 IV。

        Args:
            deltas: Delta 值数组（已按 Delta 排序）
            ivs: 对应的 IV 值数组
            target_delta: 目标 Delta 值

        Returns:
            插值得到的 IV 值。如果数据不足则返回 np.nan。
        """
        if len(deltas) < 2:
            logger.debug(f"数据点不足 ({len(deltas)} 个)，无法插值")
            return np.nan

        # 确保 Delta 是严格递增的，去除重复值
        unique_indices = np.unique(deltas, return_index=True)[1]
        if len(unique_indices) < 2:
            return np.nan

        x = deltas[unique_indices]
        y = ivs[unique_indices]

        try:
            if self.method == "cubic_spline":
                interpolator = CubicSpline(x, y, extrapolate=False)
            else:
                interpolator = interp1d(
                    x, y, kind="linear", bounds_error=False, fill_value=np.nan
                )

            result = float(interpolator(target_delta))

            # 边界约束：插值结果不应超出数据范围太多
            if np.isnan(result):
                # 如果目标在数据范围外且无法外推，尝试线性外推
                if target_delta < x[0] or target_delta > x[-1]:
                    logger.debug(
                        f"目标 Delta {target_delta:.3f} 超出数据范围 "
                        f"[{x[0]:.3f}, {x[-1]:.3f}]，使用最近邻值"
                    )
                    result = float(np.interp(target_delta, x, y))

            return result

        except Exception as e:
            logger.error(f"插值失败: {e}, deltas={x}, target={target_delta}")
            return np.nan

    def interpolate(
        self,
        df: pd.DataFrame,
    ) -> Tuple[float, float]:
        """
        从期权链 DataFrame 中提取 25Δ Put 和 25Δ Call 的 IV。

        流程:
            1. 筛选 Put 期权，按 Delta 绝对值排序
            2. 在 Put Delta-IV 曲线上插值 25Δ Put IV
            3. 筛选 Call 期权，按 Delta 排序
            4. 在 Call Delta-IV 曲线上插值 25Δ Call IV

        注意：
            Polygon.io API 中 Put 的 Delta 为负值（如 -0.25），
            Call 的 Delta 为正值（如 0.25）。
            本方法内部使用绝对值处理 Put Delta。

        Args:
            df: 清洗后的期权链 DataFrame，必须包含:
                - contract_type: "call" 或 "put"
                - delta: Delta 值
                - implied_volatility: 隐含波动率

        Returns:
            (iv_put_25d, iv_call_25d)
            如果任一无法插值，对应位置返回 np.nan
        """
        if df.empty:
            logger.warning("DataFrame 为空，无法插值")
            return np.nan, np.nan

        # ------------------------------------------------------------------
        # Put 侧插值
        # ------------------------------------------------------------------
        puts = df[df["contract_type"] == "put"].copy()

        if puts.empty:
            logger.warning("未找到 Put 期权数据")
            iv_put = np.nan
        else:
            # Put Delta 为负值，取绝对值用于插值
            puts["delta_pos"] = puts["delta"].abs()
            puts = puts.dropna(subset=["delta_pos", "implied_volatility"])
            puts = puts.sort_values("delta_pos")

            deltas = puts["delta_pos"].values
            ivs = puts["implied_volatility"].values

            iv_put = self._interpolate_iv(deltas, ivs, self.target_delta_put)
            logger.debug(
                f"Put 25Δ IV: {iv_put:.4f} "
                f"(数据范围: Δ=[{deltas.min():.3f}, {deltas.max():.3f}], "
                f"数据点: {len(deltas)})"
            )

        # ------------------------------------------------------------------
        # Call 侧插值
        # ------------------------------------------------------------------
        calls = df[df["contract_type"] == "call"].copy()

        if calls.empty:
            logger.warning("未找到 Call 期权数据")
            iv_call = np.nan
        else:
            calls = calls.dropna(subset=["delta", "implied_volatility"])
            calls = calls.sort_values("delta")

            deltas = calls["delta"].values
            ivs = calls["implied_volatility"].values

            iv_call = self._interpolate_iv(deltas, ivs, self.target_delta_call)
            logger.debug(
                f"Call 25Δ IV: {iv_call:.4f} "
                f"(数据范围: Δ=[{deltas.min():.3f}, {deltas.max():.3f}], "
                f"数据点: {len(deltas)})"
            )

        return iv_put, iv_call

    def interpolate_with_confidence(
        self,
        df: pd.DataFrame,
    ) -> dict:
        """
        插值并附带置信度信息。

        Returns:
            {
                "iv_put_25d": float,       # Put 25Δ IV
                "iv_call_25d": float,      # Call 25Δ IV
                "put_data_points": int,    # Put 数据点数量
                "call_data_points": int,   # Call 数据点数量
                "put_delta_range": tuple,  # Put Delta 范围 (min, max)
                "call_delta_range": tuple, # Call Delta 范围 (min, max)
                "put_is_extrapolated": bool,  # Put 是否外推
                "call_is_extrapolated": bool, # Call 是否外推
            }
        """
        iv_put, iv_call = self.interpolate(df)

        # Put 元数据
        puts = df[df["contract_type"] == "put"]
        put_deltas = puts["delta"].abs().dropna()
        put_range = (
            (float(put_deltas.min()), float(put_deltas.max()))
            if not put_deltas.empty else (np.nan, np.nan)
        )
        put_extrapolated = (
            not put_deltas.empty
            and (
                self.target_delta_put < put_deltas.min()
                or self.target_delta_put > put_deltas.max()
            )
        )

        # Call 元数据
        calls = df[df["contract_type"] == "call"]
        call_deltas = calls["delta"].dropna()
        call_range = (
            (float(call_deltas.min()), float(call_deltas.max()))
            if not call_deltas.empty else (np.nan, np.nan)
        )
        call_extrapolated = (
            not call_deltas.empty
            and (
                self.target_delta_call < call_deltas.min()
                or self.target_delta_call > call_deltas.max()
            )
        )

        return {
            "iv_put_25d": round(iv_put, 6) if not np.isnan(iv_put) else None,
            "iv_call_25d": round(iv_call, 6) if not np.isnan(iv_call) else None,
            "put_data_points": len(puts),
            "call_data_points": len(calls),
            "put_delta_range": put_range,
            "call_delta_range": call_range,
            "put_is_extrapolated": put_extrapolated,
            "call_is_extrapolated": call_extrapolated,
        }
