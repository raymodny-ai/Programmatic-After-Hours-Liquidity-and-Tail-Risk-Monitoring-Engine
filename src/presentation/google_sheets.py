"""
Google Sheets 风险看板推送模块 (google_sheets.py)

功能：
- 使用 gspread 连接目标 Google Sheets
- 按列写入 SPY/QQQ/IWM/DIA 的 Skew 历史走势数据
- 支持追加模式（append）以保留历史记录
- 自动格式化日期和数值列

前提条件:
    1. 在 Google Cloud Console 中启用 Google Sheets API 和 Google Drive API
    2. 创建 Service Account 并下载 JSON 凭证文件
    3. 将 Service Account 的 email 添加为 Google Sheets 的编辑者
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials
from loguru import logger

from config.settings import (
    GOOGLE_SERVICE_ACCOUNT_FILE,
    GOOGLE_SPREADSHEET_ID,
    PROJECT_ROOT,
)


class GoogleSheetsClient:
    """
    Google Sheets 客户端。

    主要接口:
        connect() -> 建立连接
        update_dashboard() -> 更新风险看板
        append_row() -> 追加单行数据
        read_sheet() -> 读取工作表数据
    """

    # Google Sheets API 权限范围
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]

    def __init__(
        self,
        credentials_file: Optional[str] = None,
        spreadsheet_id: Optional[str] = None,
    ) -> None:
        self.credentials_file = credentials_file or GOOGLE_SERVICE_ACCOUNT_FILE
        self.spreadsheet_id = spreadsheet_id or GOOGLE_SPREADSHEET_ID

        # 解析凭证路径（支持相对路径和绝对路径）
        cred_path = Path(self.credentials_file)
        if not cred_path.is_absolute():
            cred_path = PROJECT_ROOT / cred_path
        self.credentials_path = cred_path

        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None

    def connect(self) -> gspread.Spreadsheet:
        """
        建立 Google Sheets 连接。

        Returns:
            gspread Spreadsheet 对象

        Raises:
            FileNotFoundError: 凭证文件不存在
            ValueError: Spreadsheet ID 未设置
        """
        if not self.spreadsheet_id:
            raise ValueError("GOOGLE_SPREADSHEET_ID 未设置，请在 .env 文件中配置")

        if not self.credentials_path.exists():
            raise FileNotFoundError(
                f"Google Service Account 凭证文件不存在: {self.credentials_path}\n"
                f"请在 Google Cloud Console 中创建 Service Account 并下载 JSON 凭证文件。"
            )

        credentials = Credentials.from_service_account_file(
            str(self.credentials_path),
            scopes=self.SCOPES,
        )

        self._client = gspread.authorize(credentials)
        self._spreadsheet = self._client.open_by_key(self.spreadsheet_id)

        logger.info(f"已连接到 Google Sheets: {self._spreadsheet.title}")
        return self._spreadsheet

    def get_or_create_worksheet(
        self, sheet_name: str, rows: int = 500, cols: int = 20
    ) -> gspread.Worksheet:
        """
        获取或创建工作表。

        Args:
            sheet_name: 工作表名称
            rows: 初始行数
            cols: 初始列数

        Returns:
            gspread Worksheet 对象
        """
        if self._spreadsheet is None:
            self.connect()

        try:
            worksheet = self._spreadsheet.worksheet(sheet_name)
            logger.debug(f"使用现有工作表: {sheet_name}")
        except gspread.exceptions.WorksheetNotFound:
            worksheet = self._spreadsheet.add_worksheet(
                title=sheet_name, rows=str(rows), cols=str(cols)
            )
            logger.info(f"创建新工作表: {sheet_name}")

        return worksheet

    def update_dashboard(
        self,
        snapshot_df: pd.DataFrame,
        sheet_name: str = "Daily Risk Dashboard",
    ) -> None:
        """
        更新 Google Sheets 风险看板。

        将每日风险快照 DataFrame 写入指定的工作表。
        如果工作表中已有历史数据，则在最后一行之后追加。

        列布局:
            A: date        B: ticker     C: skew_spread     D: iv_put_25d
            E: iv_call_25d F: z_score    G: alert_flag      H: alert_severity
            ...

        Args:
            snapshot_df: 每日风险快照 DataFrame（来自 master_aggregator）
            sheet_name: 目标工作表名称
        """
        if self._spreadsheet is None:
            self.connect()

        worksheet = self.get_or_create_worksheet(sheet_name)

        # 检查工作表是否已有内容
        existing = worksheet.get_all_values()
        is_empty = len(existing) <= 1  # 无数据或仅有表头

        # 准备数据
        header = list(snapshot_df.columns)
        values = snapshot_df.fillna("").astype(str).values.tolist()

        if is_empty:
            # 首次写入：写入表头和所有数据
            worksheet.update(range_name="A1", values=[header])
            if values:
                worksheet.update(range_name="A2", values=values)
            logger.info(f"首次写入工作表 '{sheet_name}': {len(values)} 行")
        else:
            # 追加模式：从已有数据末尾追加
            next_row = len(existing) + 1
            # 只追加 header 不在已有表头中时的表头（通常已有）
            for i, row in enumerate(values):
                range_name = f"A{next_row + i}"
                worksheet.update(range_name=range_name, values=[row])
            logger.info(
                f"追加 {len(values)} 行到工作表 '{sheet_name}' "
                f"(总行数: {next_row + len(values) - 1})"
            )

    def update_ticker_history(
        self,
        ticker: str,
        date_str: str,
        skew_spread: Optional[float],
        iv_put: Optional[float],
        iv_call: Optional[float],
        z_score: Optional[float],
        alert_flag: bool,
        sheet_name: str = "Skew History",
    ) -> None:
        """
        向指定标的的历史记录工作表追加一行数据。

        每个标的使用独立的工作表，按日期累积 Skew 历史。

        Args:
            ticker: 标的符号
            date_str: 日期字符串
            skew_spread: Skew 值
            iv_put: Put 25Δ IV
            iv_call: Call 25Δ IV
            z_score: Z-Score
            alert_flag: 是否触发预警
            sheet_name: 工作表名称
        """
        if self._spreadsheet is None:
            self.connect()

        worksheet = self.get_or_create_worksheet(f"{ticker}_{sheet_name}")

        # 如果工作表为空，先写表头
        existing = worksheet.get_all_values()
        if not existing:
            header = ["Date", "Skew_Spread", "IV_Put_25D", "IV_Call_25D",
                      "Z_Score", "Alert"]
            worksheet.update(range_name="A1", values=[header])

        # 追加数据行
        next_row = len(existing) + 1 if existing else 2
        row_data = [
            date_str,
            round(skew_spread, 6) if skew_spread is not None else "",
            round(iv_put, 6) if iv_put is not None else "",
            round(iv_call, 6) if iv_call is not None else "",
            round(z_score, 4) if z_score is not None else "",
            "YES" if alert_flag else "NO",
        ]
        worksheet.update(range_name=f"A{next_row}", values=[row_data])

    def read_historical_data(
        self, sheet_name: str = "Daily Risk Dashboard"
    ) -> pd.DataFrame:
        """
        从 Google Sheets 读取历史风险数据。

        Args:
            sheet_name: 工作表名称

        Returns:
            历史数据 DataFrame
        """
        if self._spreadsheet is None:
            self.connect()

        worksheet = self.get_or_create_worksheet(sheet_name)
        data = worksheet.get_all_values()

        if len(data) <= 1:
            return pd.DataFrame()

        df = pd.DataFrame(data[1:], columns=data[0])
        return df
