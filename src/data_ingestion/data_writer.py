"""
数据本地化存储模块 (data_writer.py)

功能：
- 将原始 JSON 响应按标识符和日期归档存储
- 支持 JSON 和 Parquet 两种格式
- 自动创建以日期为子目录的归档结构
- v1.2.1: 幂等去重 + source-priority + 原子写入
"""

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from loguru import logger

from config.settings import RAW_DATA_DIR, PROCESSED_DATA_DIR

# ── v1.2.1: 数据源优先级映射 ──
SOURCE_PRIORITY: dict[str, int] = {
    "polygon": 2,
    "yfinance": 1,
    "unknown": 0,
}


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
        snapshot_df: pd.DataFrame,
        filename: str = "daily_risk_snapshot.parquet",
    ) -> Path:
        """保存主数据帧快照（v1.2.1: 幂等去重 + source-priority + 原子写入）。

        策略:
            - 新结果与历史结果合并
            - (date, ticker) 主键去重，保留高优先级数据源
            - 按 date, ticker 排序后原子写入临时文件再 replace
            - 返回最终行数、去重数量，便于审计
        """
        if snapshot_df.empty:
            logger.warning("收到空 daily snapshot，跳过主快照写入")
            return self.processed_dir / filename

        required = {"date", "ticker"}
        missing = required - set(snapshot_df.columns)
        if missing:
            raise ValueError(f"主快照缺少主键列: {sorted(missing)}")

        file_path = self.processed_dir / filename
        incoming = snapshot_df.copy()
        incoming["date"] = pd.to_datetime(incoming["date"]).dt.normalize()
        incoming["ticker"] = incoming["ticker"].astype(str).str.upper()

        if file_path.exists():
            historical = pd.read_parquet(file_path)
            historical["date"] = pd.to_datetime(historical["date"]).dt.normalize()
            historical["ticker"] = historical["ticker"].astype(str).str.upper()
            combined = pd.concat([historical, incoming], ignore_index=True)
        else:
            combined = incoming

        before = len(combined)

        # ── v1.2.1: source-priority 去重 ──
        if "data_source" in combined.columns:
            combined["_source_priority"] = (
                combined.get("data_source", "unknown")
                .fillna("unknown")
                .map(SOURCE_PRIORITY)
                .fillna(0)
            )
            combined = (
                combined
                .sort_values(["date", "ticker", "_source_priority"])
                .drop_duplicates(subset=["date", "ticker"], keep="last")
                .drop(columns=["_source_priority"], errors="ignore")
            )
        else:
            combined = (
                combined
                .sort_values(["date", "ticker"])
                .drop_duplicates(subset=["date", "ticker"], keep="last")
            )

        combined = combined.reset_index(drop=True)
        removed = before - len(combined)

        # 原子写入：先写临时文件，再 os.replace
        temp_path = None
        try:
            temp_fd, temp_path_str = tempfile.mkstemp(
                suffix=".parquet", dir=str(self.processed_dir),
            )
            os.close(temp_fd)
            temp_path = Path(temp_path_str)
            combined.to_parquet(temp_path, index=False)
            os.replace(temp_path, file_path)
        finally:
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

        logger.info(
            f"主快照已写入: {file_path}; "
            f"rows={len(combined)}; deduplicated={removed}"
        )
        return file_path

    def load_master_snapshot(
        self,
        filename: str = "daily_risk_snapshot.parquet",
    ) -> pd.DataFrame:
        """
        加载主数据帧快照（v1.2: 含数据新鲜度校验）。

        校验逻辑:
            - 检查最新一行日期是否为上一个交易日
            - 若数据过期超过 3 个交易日，发出警告
        """
        file_path = self.processed_dir / filename
        if not file_path.exists():
            logger.warning(f"主数据帧文件不存在: {file_path}，返回空 DataFrame")
            return pd.DataFrame()

        df = pd.read_parquet(file_path)

        # ── v1.2: 数据新鲜度校验 ──
        if not df.empty and "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            latest_date = df["date"].max().date()
            today = date.today()
            days_since = (today - latest_date).days

            if days_since > 3:
                logger.warning(
                    f"数据新鲜度警告: 最新数据日期 {latest_date}，"
                    f"距今 {days_since} 天。Z-Score 窗口可能受污染。"
                )
            elif days_since > 0:
                logger.info(
                    f"数据新鲜度: 最新日期 {latest_date}，距今 {days_since} 天"
                )

        return df

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
