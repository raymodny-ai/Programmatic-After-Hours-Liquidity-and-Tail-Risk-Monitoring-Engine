"""
VIX / VXN 期货期限结构数据获取客户端 (vix_client.py) v1.2

功能：
- 从 Cboe 公开 CSV 获取 VIX / VXN 期货结算价历史数据
- 提取近月与次月 VIX 期货价差
- 判断波动率期限结构是否发生倒挂（Inversion）
- v1.2: 新增 VXN（纳斯达克波动率指数）独立接入

数据来源:
    Cboe VIX Futures Historical Data (CSV)
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv

说明:
    VIX Central 和 Cboe 均提供公开的 VIX 期货历史数据。
    当前实现基于 Cboe 公开 CSV，无需 API 密钥。
    VXN 是纳斯达克100波动率指数，用于 QQQ 相关的波动率分析。
"""

from datetime import date, datetime, timedelta
from typing import Optional

import httpx
import pandas as pd
from loguru import logger

from config.settings import CBOE_VIX_FUTURES_URL


class VIXClient:
    """
    VIX / VXN 期货数据客户端。

    主要接口:
        fetch_vix_history()   -> 获取 VIX 期货历史结算价 DataFrame
        fetch_vxn_history()   -> 获取 VXN 期货历史结算价 DataFrame (v1.2)
        get_term_structure()  -> 获取近月/次月期货价差
        check_inversion()     -> 检测期限结构是否倒挂
    """

    # v1.2: VXN 数据 URL
    VXN_FUTURES_URL: str = (
        "https://cdn.cboe.com/api/global/us_indices/daily_prices/VXN_History.csv"
    )

    def __init__(self, futures_csv_url: Optional[str] = None) -> None:
        self.futures_csv_url = futures_csv_url or CBOE_VIX_FUTURES_URL

    async def fetch_vix_history(self) -> pd.DataFrame:
        """
        从 Cboe 公开 CSV 获取 VIX 期货历史数据。

        Cboe CSV 格式说明:
            - 包含多列: Date, F1 (近月结算价), F2 (次月结算价), ... F9
            - F1 是最近到期的 VIX 期货, F2 是次近月, 依此类推

        Returns:
            DataFrame，列: [date, f1_price, f2_price, ...]
        """
        logger.info(f"从 Cboe 拉取 VIX 期货历史数据: {self.futures_csv_url}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.futures_csv_url)
            response.raise_for_status()

            # Cboe CSV 可能需要跳过一些标题行
            from io import StringIO

            lines = response.text.splitlines()
            # 查找表头行（通常以 "Date" 开头）
            header_idx = 0
            for i, line in enumerate(lines):
                if line.strip().startswith("Date"):
                    header_idx = i
                    break

            csv_content = "\n".join(lines[header_idx:])
            df = pd.read_csv(StringIO(csv_content))

            # 标准化列名
            df.columns = [c.strip().lower() for c in df.columns]
            date_col = df.columns[0]
            df = df.rename(columns={date_col: "date"})
            df["date"] = pd.to_datetime(df["date"])

            logger.info(f"成功获取 VIX 期货数据: {len(df)} 条记录, 日期范围 "
                        f"{df['date'].min().date()} -> {df['date'].max().date()}")
            return df

    def get_term_structure(
        self,
        df: pd.DataFrame,
        as_of_date: Optional[date] = None,
    ) -> dict:
        """
        计算 VIX 期限结构（近月 vs 次月价差）。

        Args:
            df: fetch_vix_history() 返回的 DataFrame
            as_of_date: 目标日期，默认取最新一日

        Returns:
            {
                "date": str,
                "front_month": float,      # 近月结算价
                "second_month": float,      # 次月结算价
                "spread": float,            # 次月 - 近月 价差
                "is_inverted": bool,        # 是否倒挂（近月 > 次月）
                "contango_pct": float,      # 升水/贴水百分比
            }
        """
        if as_of_date is not None:
            as_of_datetime = pd.Timestamp(as_of_date)
            row = df[df["date"] == as_of_datetime]
            if row.empty:
                closest = df.iloc[(df["date"] - as_of_datetime).abs().argsort()[:1]]
                row = closest
        else:
            row = df.iloc[-1:]

        if row.empty:
            raise ValueError("未找到目标日期的 VIX 期货数据")

        front = float(row.iloc[0].get("f1", 0))
        second = float(row.iloc[0].get("f2", 0))

        spread = second - front
        is_inverted = front > second
        contango_pct = (spread / front * 100) if front > 0 else 0.0

        return {
            "date": str(row.iloc[0]["date"].date()),
            "front_month": front,
            "second_month": second,
            "spread": round(spread, 2),
            "is_inverted": is_inverted,
            "contango_pct": round(contango_pct, 2),
        }

    def check_inversion(
        self,
        df: pd.DataFrame,
        lookback_days: int = 252,
    ) -> pd.DataFrame:
        """
        检测历史期限结构倒挂事件。

        Args:
            df: fetch_vix_history() 返回的 DataFrame
            lookback_days: 回溯天数

        Returns:
            DataFrame，增加 is_inverted 列
        """
        result = df.copy()
        result["is_inverted"] = result["f1"] > result["f2"]
        result["spread"] = result["f2"] - result["f1"]
        return result.tail(lookback_days)

    # ── v1.2: VXN 独立接入 ──

    async def fetch_vxn_history(self) -> pd.DataFrame:
        """
        从 Cboe 公开 CSV 获取 VXN（纳斯达克波动率指数）期货历史数据。

        VXN 是纳斯达克100指数的波动率指数，等价于 QQQ 的 "VIX"。
        对 QQQ 的期限结构分析应使用 VXN 而非 VIX。

        Returns:
            DataFrame，结构同 fetch_vix_history()
        """
        logger.info(f"[VXN] 从 Cboe 拉取 VXN 期货历史数据: {self.VXN_FUTURES_URL}")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(self.VXN_FUTURES_URL)
            response.raise_for_status()

            from io import StringIO

            lines = response.text.splitlines()
            header_idx = 0
            for i, line in enumerate(lines):
                if line.strip().startswith("Date"):
                    header_idx = i
                    break

            csv_content = "\n".join(lines[header_idx:])
            df = pd.read_csv(StringIO(csv_content))

            df.columns = [c.strip().lower() for c in df.columns]
            date_col = df.columns[0]
            df = df.rename(columns={date_col: "date"})
            df["date"] = pd.to_datetime(df["date"])

            logger.info(
                f"[VXN] 成功获取 VXN 期货数据: {len(df)} 条记录, "
                f"日期范围 {df['date'].min().date()} -> {df['date'].max().date()}"
            )
            return df

    def get_vxn_term_structure(
        self,
        df: pd.DataFrame,
        as_of_date: Optional[date] = None,
    ) -> dict:
        """
        计算 VXN 期限结构（同 get_term_structure，但用于 VXN 数据）。

        用法与 get_term_structure() 完全相同。
        """
        return self.get_term_structure(df, as_of_date=as_of_date)
