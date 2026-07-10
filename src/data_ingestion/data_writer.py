"""
数据本地化存储模块 (data_writer.py)

功能：
- 将原始 JSON 响应按标识符和日期归档存储
- 支持 JSON 和 Parquet 两种格式
- 自动创建以日期为子目录的归档结构
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from loguru import logger

from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR


class DataWriter:
    """
    数据本地化写入器。

    存储格式:
        data/raw/{ticker}/{ticker}_{date}.json       # 原始 JSON
        data/raw/{ticker}/{ticker}_{date}.parquet    # 解析后 DataFrame
        data/processed/daily_risk_snapshot.parquet   # 汇总后的每日风险快照
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.raw_dir = base_dir or RAW_DATA_DIR
        self.processed_dir = PROCESSED_DATA_DIR
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)

    def _ticker_dir(self, ticker: str) -> Path:
        """获取标的专属目录路径。"""
        ticker_dir = self.raw_dir / ticker
        ticker_dir.mkdir(parents=True, exist_ok=True)
        return ticker_dir

    def _filename(self, ticker: str, target_date: date, extension: str) -> str:
        """生成标准化文件名: {ticker}_{YYYY-MM-DD}.{ext}"""
        return f"{ticker}_{target_date.isoformat()}.{extension}"

    # ------------------------------------------------------------------
    # JSON 写入
    # ------------------------------------------------------------------

    def save_raw_json(
        self,
        ticker: str,
        target_date: date,
        data: Any,
    ) -> Path:
        """
        将原始 API 响应保存为 JSON 文件。

        Args:
            ticker: 标的符号 (如 SPY)
            target_date: 数据日期
            data: 原始响应数据（dict 或 list）

        Returns:
            保存的文件路径
        """
        file_path = self._ticker_dir(ticker) / self._filename(ticker, target_date, "json")

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, default=str)

        logger.info(f"原始 JSON 已保存: {file_path}")
        return file_path

    def load_raw_json(self, ticker: str, target_date: date) -> Any:
        """
        加载之前保存的原始 JSON 文件。

        Args:
            ticker: 标的符号
            target_date: 数据日期

        Returns:
            解析后的 JSON 数据
        """
        file_path = self._ticker_dir(ticker) / self._filename(ticker, target_date, "json")

        if not file_path.exists():
            raise FileNotFoundError(f"原始数据文件不存在: {file_path}")

        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)

    # ------------------------------------------------------------------
    # Parquet 写入
    # ------------------------------------------------------------------

    def save_dataframe(
        self,
        ticker: str,
        target_date: date,
        df: pd.DataFrame,
    ) -> Path:
        """
        将 DataFrame 保存为 Parquet 文件（列式压缩存储）。

        Args:
            ticker: 标的符号
            target_date: 数据日期
            df: pandas DataFrame

        Returns:
            保存的文件路径
        """
        file_path = self._ticker_dir(ticker) / self._filename(ticker, target_date, "parquet")
        df.to_parquet(file_path, index=False)
        logger.info(f"DataFrame 已保存: {file_path} ({len(df)} 行)")
        return file_path

    def load_dataframe(self, ticker: str, target_date: date) -> pd.DataFrame:
        """
        加载之前保存的 Parquet 文件为 DataFrame。
        """
        file_path = self._ticker_dir(ticker) / self._filename(ticker, target_date, "parquet")

        if not file_path.exists():
            raise FileNotFoundError(f"Parquet 文件不存在: {file_path}")

        return pd.read_parquet(file_path)

    # ------------------------------------------------------------------
    # 主数据帧快照
    # ------------------------------------------------------------------

    def save_master_snapshot(
        self,
        df: pd.DataFrame,
        filename: str = "daily_risk_snapshot.parquet",
    ) -> Path:
        """
        保存主数据帧快照（每日风险汇总表）。

        如果文件已存在，则追加新数据。
        """
        file_path = self.processed_dir / filename

        if file_path.exists():
            existing = pd.read_parquet(file_path)
            # 按 date + ticker 去重，新数据覆盖旧数据
            combined = pd.concat([existing, df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
            combined.to_parquet(file_path, index=False)
        else:
            df.to_parquet(file_path, index=False)

        logger.info(f"主数据帧已更新: {file_path} ({len(df)} 行新增)")
        return file_path

    def load_master_snapshot(
        self,
        filename: str = "daily_risk_snapshot.parquet",
    ) -> pd.DataFrame:
        """
        加载主数据帧快照。
        """
        file_path = self.processed_dir / filename
        if not file_path.exists():
            logger.warning(f"主数据帧文件不存在: {file_path}，返回空 DataFrame")
            return pd.DataFrame()
        return pd.read_parquet(file_path)

    def list_available_dates(self, ticker: str) -> list[date]:
        """
        列出某标的所有已保存数据的日期。

        Args:
            ticker: 标的符号

        Returns:
            已保存日期列表（升序排列）
        """
        ticker_dir = self._ticker_dir(ticker)
        dates: set[date] = set()

        for f in ticker_dir.glob(f"{ticker}_*.json"):
            # 从文件名提取日期: SPY_2024-01-15.json -> 2024-01-15
            try:
                name = f.stem  # SPY_2024-01-15
                date_str = name[len(ticker) + 1:]  # 2024-01-15
                dates.add(date.fromisoformat(date_str))
            except (ValueError, IndexError):
                continue

        return sorted(dates)
