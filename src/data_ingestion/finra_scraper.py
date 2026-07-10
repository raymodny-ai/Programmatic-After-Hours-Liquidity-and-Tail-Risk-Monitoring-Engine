"""
FINRA 保证金债务自动爬取模块 (finra_scraper.py)

数据来源: https://www.finra.org/investors/research-and-tools/margin-statistics

功能:
- 自动爬取 FINRA 官网保证金债务统计数据（XLSX 格式）
- 每月缓存避免重复下载
- 标准化 DataFrame 输出，兼容 macro_leverage 模块
- 解析失败时提供清晰的错误提示和手动下载指引

FINRA 数据表格包含:
    Month/Year | Debit Balances in Customers' Securities Margin Accounts
               | Free Credit Balances - Cash Accounts
               | Free Credit Balances - Securities Margin Accounts
"""

import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd
from loguru import logger

# FINRA 保证金统计页面 URL
FINRA_MARGIN_URL = "https://www.finra.org/investors/research-and-tools/margin-statistics"

# 匹配 XLSX/XLS 下载链接的正则
FINRA_FILE_PATTERN = re.compile(
    r'href="(https?://(?:www\.)?finra\.org/[^"]*margin[^"]*\.xlsx?)"',
    re.IGNORECASE,
)

# 备用直接下载 URL（FINRA 有时会变更，此处使用已知路径格式）
FALLBACK_URLS = [
    "https://www.finra.org/sites/default/files/margin-statistics.xlsx",
]


class FINRAScraper:
    """
    FINRA 保证金债务统计数据自动采集器。

    使用示例:
        scraper = FINRAScraper()
        df = await scraper.fetch_margin_debt()
        # DataFrame: [date, debit_balance]
    """

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or Path("data/raw/finra")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    async def fetch_margin_debt(self) -> pd.DataFrame:
        """
        自动获取最新 FINRA 保证金债务数据。

        流程:
            1. 检查当月缓存（避免重复下载）
            2. 尝试从 FINRA 页面解析最新文件链接
            3. 下载并解析 XLSX 文件
            4. 标准化为 [date, debit_balance] DataFrame
            5. 缓存结果到本地

        Returns:
            DataFrame，列:
                - date: 月末日期
                - debit_balance: 保证金债务余额（百万美元）

        Raises:
            RuntimeError: 所有获取方式均失败时抛出
        """
        # Step 1: 检查本月缓存
        cache_file = self.cache_dir / f"margin_{datetime.today().strftime('%Y%m')}.csv"
        if cache_file.exists():
            logger.info(f"使用缓存的 FINRA 数据: {cache_file}")
            df = pd.read_csv(cache_file, parse_dates=["date"])
            if not df.empty:
                return df

        # Step 2-3: 下载
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        ) as client:
            df = await self._try_fallback_urls(client)

            if df is None:
                df = await self._scrape_page_for_link(client)

            if df is None:
                raise RuntimeError(
                    "FINRA 保证金债务数据自动获取失败。\n"
                    "请手动下载最新数据:\n"
                    "  1. 访问 https://www.finra.org/investors/research-and-tools/margin-statistics\n"
                    "  2. 下载 XLSX 文件\n"
                    "  3. 使用 --margin-debt-csv <path> 参数传入文件路径"
                )

        # Step 5: 缓存
        df.to_csv(cache_file, index=False)
        logger.info(f"FINRA 数据已缓存: {cache_file} ({len(df)} 条记录)")
        return df

    async def _try_fallback_urls(
        self, client: httpx.AsyncClient
    ) -> Optional[pd.DataFrame]:
        """尝试一系列已知的备用 URL。"""
        for url in FALLBACK_URLS:
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                logger.info(f"成功通过备用 URL 下载: {url}")
                df = self._parse_finra_xlsx(resp.content)
                if df is not None:
                    return df
            except Exception as e:
                logger.debug(f"备用 URL 失败 {url}: {e}")
        return None

    async def _scrape_page_for_link(
        self, client: httpx.AsyncClient
    ) -> Optional[pd.DataFrame]:
        """解析 FINRA 页面 HTML，查找最新 XLSX 文件链接。"""
        try:
            resp = await client.get(FINRA_MARGIN_URL)
            resp.raise_for_status()

            matches = FINRA_FILE_PATTERN.findall(resp.text)
            if not matches:
                logger.warning("未在 FINRA 页面中找到 XLSX 下载链接")
                return None

            # 取第一个匹配项（通常是最新文件）
            file_url = matches[0]
            # 确保 URL 是绝对路径
            if file_url.startswith("//"):
                file_url = "https:" + file_url

            logger.info(f"从 FINRA 页面解析到文件链接: {file_url}")

            file_resp = await client.get(file_url)
            file_resp.raise_for_status()

            return self._parse_finra_xlsx(file_resp.content)

        except Exception as e:
            logger.error(f"FINRA 页面解析失败: {e}")
            return None

    def _parse_finra_xlsx(self, content: bytes) -> Optional[pd.DataFrame]:
        """
        解析 FINRA XLSX 文件内容。

        FINRA 表格格式（典型）:
            - 前 1-3 行可能是标题/空行
            - 列: Month-Year | Debit Balances | Free Credit Cash | Free Credit Margin
            - 日期列可能命名为 "Month/Year", "Month", "Year-Month" 等
        """
        try:
            # 尝试不带 skiprows 读取，查看实际结构
            raw_df = pd.read_excel(BytesIO(content), header=None)
            if raw_df.empty:
                return None

            # 查找表头行（包含 "date" 或 "month" 关键词的行）
            header_row = None
            for i in range(min(5, len(raw_df))):
                row_values = [str(v).lower() for v in raw_df.iloc[i].dropna().values]
                combined = " ".join(row_values)
                if "month" in combined or "date" in combined or "year" in combined:
                    header_row = i
                    break

            if header_row is None:
                # 默认假设第 2 行是表头
                df = pd.read_excel(BytesIO(content), skiprows=1)
            else:
                df = pd.read_excel(BytesIO(content), skiprows=header_row)

            return self._normalize_finra_df(df)

        except Exception as e:
            logger.error(f"XLSX 解析失败: {e}")
            # 回退：尝试 skiprows=2 标准格式
            try:
                df = pd.read_excel(BytesIO(content), skiprows=2)
                return self._normalize_finra_df(df)
            except Exception:
                return None

    @staticmethod
    def _normalize_finra_df(df: pd.DataFrame) -> pd.DataFrame:
        """
        标准化 FINRA DataFrame 格式。

        将原始 FINRA 列名映射为标准名称:
            - 日期列 → date
            - 保证金债务列 → debit_balance

        处理:
            - 自动识别日期列（支持 Month/Year, Month, Year-Month 等格式）
            - 自动识别保证金债务列（含 "debit" 关键词）
            - 千位分隔符移除
            - 按日期排序
        """
        # 清理列名
        df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]

        if df.empty:
            return df

        # 识别日期列（优先级: month > date > year）
        date_col = None
        for pattern in ["month", "date", "year"]:
            for col in df.columns:
                if pattern in col.lower():
                    date_col = col
                    break
            if date_col:
                break

        if date_col is None:
            date_col = df.columns[0]
            logger.warning(f"无法自动识别日期列，使用第一列: '{date_col}'")

        df = df.rename(columns={date_col: "date"})

        # 解析日期：尝试多种格式
        df["date"] = pd.to_datetime(df["date"], errors="coerce")

        # 丢弃无法解析日期的行
        before = len(df)
        df = df.dropna(subset=["date"])
        if len(df) < before:
            logger.debug(f"丢弃了 {before - len(df)} 行无效日期数据")

        # 识别保证金债务列（含 "debit" 关键词）
        debit_col = None
        for col in df.columns:
            if "debit" in col.lower():
                debit_col = col
                break

        if debit_col:
            df = df.rename(columns={debit_col: "debit_balance"})

            # 清洗数值：移除千位分隔符、货币符号等
            if df["debit_balance"].dtype == object:
                df["debit_balance"] = (
                    df["debit_balance"]
                    .astype(str)
                    .str.replace(r"[$,£€¥\s]", "", regex=True)
                    .str.replace(",", "")
                )
            df["debit_balance"] = pd.to_numeric(df["debit_balance"], errors="coerce")
        else:
            logger.warning("未找到 'debit_balance' 列，仅保留日期列")
            df["debit_balance"] = None

        # 只保留需要的列
        keep_cols = ["date"]
        if "debit_balance" in df.columns:
            keep_cols.append("debit_balance")
        df = df[keep_cols]

        # 删除全空行
        df = df.dropna(subset=["debit_balance"], how="all")
        df = df.dropna(subset=["date"])

        # 按日期排序
        df = df.sort_values("date").reset_index(drop=True)

        logger.info(
            f"FINRA 数据标准化完成: {len(df)} 条记录, "
            f"日期范围: {df['date'].min().strftime('%Y-%m')} -> {df['date'].max().strftime('%Y-%m')}"
        )

        return df
