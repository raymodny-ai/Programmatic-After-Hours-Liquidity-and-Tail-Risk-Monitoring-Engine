"""矩阵化 Skew 计算 (V1.3 阶段 2.5)。

特性：
- 单次调度输出多个标的的 25Δ Skew + Z-Score
- 复用 v1.2.1 的 ``src.calculation.skew_calculator.calculate_skew`` 计算逻辑
- 结果批量落库到 SQLite + Redis
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)


async def compute_matrix_skew(
    tickers: list[str],
    as_of: date,
    surface_fetcher,
    store_sink,
) -> dict[str, Any]:
    """对一组 ticker 并行计算 Skew 截面。

    Args:
        tickers: 标的池（默认 ["SPY", "QQQ", "IWM", "DIA"]）
        as_of: 计算日期
        surface_fetcher: async (ticker, as_of) -> pd.DataFrame
        store_sink:      sync (ticker, record) -> None

    Returns:
        {
            "as_of": ...,
            "computed": ["SPY", "QQQ", ...],
            "skipped":  [...],
            "results":  [...]
        }
    """
    results: list[dict[str, Any]] = []
    skipped: list[str] = []

    async def _one(t: str) -> dict[str, Any] | None:
        try:
            df = await surface_fetcher(t, as_of)
            if df is None or getattr(df, "empty", True):
                skipped.append(t)
                return None
            # 调用 v1.2.1 的 skew_calculator
            from src.calculation.skew_calculator import calculate_skew  # 局部导入

            record = calculate_skew(df, ticker=t)
            return {"ticker": t, **record}
        except Exception as e:
            logger.exception("Skew 计算失败: %s (%s)", t, e)
            skipped.append(t)
            return None

    # 并行计算
    outcomes = await asyncio.gather(*[_one(t) for t in tickers])
    for r in outcomes:
        if r is not None:
            results.append(r)
            store_sink(r)

    return {
        "as_of": as_of.isoformat(),
        "computed": [r["ticker"] for r in results],
        "skipped": skipped,
        "results": results,
        "count": len(results),
    }
