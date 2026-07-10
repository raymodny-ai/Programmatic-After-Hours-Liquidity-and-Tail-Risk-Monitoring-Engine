"""
FRED API 数据拉取客户端 (fred_client.py)

功能：
- 获取 M2 货币供应量（M2SL 系列）历史数据
- 获取其他 FRED 经济数据系列
- 内置数据格式化为 pandas DataFrame
"""

from datetime import date, datetime
from typing import Any, Optional

import pandas as pd
from fredapi import Fred
from loguru import logger

from config.settings import FRED_API_KEY


class FREDClient:
    """
    FRED (Federal Reserve Economic Data) API 客户端。

    主要接口:
        get_m2_supply() -> 获取 M2 货币供应量历史数据
        get_series(series_id) -> 获取任意 FRED 系列数据
    """

    def __init__(self, api_key: Optional[str] = None) -> None:
        self.api_key = api_key or FRED_API_KEY
        if not self.api_key:
            raise ValueError(
                "FRED API 密钥未设置。请在 .env 文件中设置 FRED_API_KEY，"
                "或通过构造函数参数传入。"
            )
        self._fred = Fred(api_key=self.api_key)

    def get_m2_supply(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        获取 M2 货币供应量历史数据（系列 ID: M2SL）。

        M2SL 是 FRED 中美联储公布的月度 M2 货币供应量，
        单位为十亿美元（Billions of Dollars）。

        Args:
            start_date: 起始日期 "YYYY-MM-DD"，默认从 2000-01-01 开始
            end_date: 结束日期 "YYYY-MM-DD"，默认到最新

        Returns:
            DataFrame，列: [date, m2_supply]
        """
        if start_date is None:
            start_date = "2000-01-01"

        logger.info(f"获取 FRED M2 货币供应量数据: {start_date} -> {end_date or '最新'}")

        try:
            series = self._fred.get_series(
                "M2SL",
                observation_start=start_date,
                observation_end=end_date,
            )

            df = series.reset_index()
            df.columns = ["date", "m2_supply"]
            df["date"] = pd.to_datetime(df["date"])

            logger.info(f"成功获取 M2 数据: {len(df)} 条记录")
            return df

        except Exception as e:
            logger.error(f"获取 M2 数据失败: {e}")
            raise

    def get_series(
        self,
        series_id: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> pd.Series:
        """
        获取指定 FRED 系列数据。

        Args:
            series_id: FRED 系列 ID，如 "M2SL", "GDP", "UNRATE"
            start_date: 起始日期
            end_date: 结束日期

        Returns:
            pandas Series，索引为日期
        """
        logger.info(f"获取 FRED 系列 {series_id}: {start_date} -> {end_date or '最新'}")

        try:
            series = self._fred.get_series(
                series_id,
                observation_start=start_date,
                observation_end=end_date,
            )
            return series
        except Exception as e:
            logger.error(f"获取 FRED 系列 {series_id} 失败: {e}")
            raise

    def get_latest_m2_value(self) -> float:
        """
        获取最新的 M2 货币供应量值（用于当前月度计算）。

        Returns:
            最新 M2 值（十亿美元）
        """
        df = self.get_m2_supply(start_date="2024-01-01")
        if df.empty:
            raise ValueError("未获取到 M2 数据")
        return float(df["m2_supply"].iloc[-1])
