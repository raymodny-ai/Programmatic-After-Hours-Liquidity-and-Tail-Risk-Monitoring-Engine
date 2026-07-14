"""
日终（EOD）数据批量抓取编排器 (eod_fetcher.py)

功能：
- 每日盘后循环抓取 SPY/QQQ/IWM/DIA 的 EOD 期权链数据
- 利用 asyncio 并发拉取多个标的，受速率限制器控制
- 自动化全流程：API 拉取 -> 本地存储 -> 返回原始数据字典
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Any, Optional

from loguru import logger

from config.settings import TARGET_SYMBOLS, TARGET_DTE, DTE_TOLERANCE
from src.data_ingestion.data_writer import DataWriter
from src.data_ingestion.polygon_client import PolygonClient


def find_nearest_expiration(
    target_dte: int = TARGET_DTE,
    tolerance: int = DTE_TOLERANCE,
) -> date:
    """
    计算最接近目标 DTE（距离到期日）的到期日。

    美股期权通常在每月第三个周五到期（月度期权），
    以及每周五到期（周度期权）。

    简化逻辑：
        从今天起算 target_dte 天，找到该日期之后最近的一个周五。
        然后在 ±tolerance 天内寻找最接近的到期日。

    Args:
        target_dte: 目标到期天数（默认 30 天）
        tolerance: 容差范围（±天）

    Returns:
        目标到期日 date 对象
    """
    today = date.today()
    target_date = today + timedelta(days=target_dte)

    # 找到 target_date 之后最近的一个周五
    days_until_friday = (4 - target_date.weekday()) % 7
    if days_until_friday == 0:
        days_until_friday = 7  # 如果是周五，取下一个周五
    expiration = target_date + timedelta(days=days_until_friday)

    # 美股期权到期日通常是周五，如果不是标准周五则调整
    # 这里简化处理，直接使用最近的周五

    logger.info(
        f"计算到期日: 目标 DTE={target_dte}, "
        f"最近周五到期日={expiration.isoformat()} "
        f"(实际 DTE={(expiration - today).days} 天)"
    )
    return expiration


async def fetch_single_ticker(
    client: PolygonClient,
    writer: DataWriter,
    ticker: str,
    target_date: date,
) -> Optional[dict[str, Any]]:
    """
    抓取单个标的的期权快照数据并保存（v1.2: 含 yfinance 降级）。

    Args:
        client: Polygon API 客户端
        writer: 数据写入器
        ticker: 标的符号
        target_date: 数据日期

    Returns:
        {"ticker": str, "date": str, "snapshots": list, "file_path": str}
        失败时返回 None
    """
    expiration_str = target_date.isoformat()

    try:
        logger.info(f"[{ticker}] 开始抓取期权链，到期日={expiration_str}")

        # ── v1.2.2 (OpenClaw patch): 首次 ticker 探测 Polygon snapshot 权限 ──
        # 一次 403 探测后 flip 开关，后续 ticker 跳过 5 次重试风暴
        if ticker == TARGET_SYMBOLS[0]:
            entitled = await client.probe_snapshot_entitlement()
            if not entitled:
                logger.warning(
                    "Polygon snapshot 权限探测失败(免费层/key 无权)。"
                    "本次 4 个 ticker 全部跳过 Polygon, 走 yfinance 备用源。"
                )

        snapshots = await client.get_option_snapshots(
            ticker=ticker,
            expiration_date=expiration_str,
        )

        # ── v1.2: 若 Polygon 无数据，尝试 yfinance 降级 ──
        if not snapshots:
            logger.warning(f"[{ticker}] Polygon 未返回数据，尝试 yfinance 备用源...")
            try:
                from src.data_ingestion.fallback_source import fetch_options_fallback

                snapshots = await fetch_options_fallback(ticker)
                if snapshots:
                    logger.info(f"[{ticker}] yfinance 备用源成功: {len(snapshots)} 条快照")
            except ImportError:
                logger.warning("[{ticker}] yfinance 未安装，跳过备用源")
            except Exception as fe:
                logger.error(f"[{ticker}] yfinance 备用源异常: {fe}")

        if not snapshots:
            logger.warning(f"[{ticker}] 所有数据源均未获取到期权快照 (到期日={expiration_str})")
            return None

        # 标注数据源（v1.2: 溯源审计）
        data_source = snapshots[0].get("_data_source", "polygon") if snapshots else "polygon"

        # 构造数据记录
        data_record = {
            "ticker": ticker,
            "fetch_date": date.today().isoformat(),
            "expiration_date": expiration_str,
            "snapshot_count": len(snapshots),
            "snapshots": snapshots,
            "data_source": data_source,  # v1.2: 溯源字段
        }

        # 保存到本地
        file_path = writer.save_raw_json(ticker, date.today(), data_record)
        logger.info(f"[{ticker}] 成功保存 {len(snapshots)} 条快照 -> {file_path}")

        return data_record

    except Exception as e:
        logger.error(f"[{ticker}] 抓取失败: {type(e).__name__}: {e}")
        return None


async def fetch_all_tickers(
    polygon_client: Optional[PolygonClient] = None,
    writer: Optional[DataWriter] = None,
    tickers: Optional[list[str]] = None,
    target_dte: Optional[int] = None,
) -> dict[str, Optional[dict[str, Any]]]:
    """
    并发抓取所有标的的期权数据。

    Args:
        polygon_client: Polygon API 客户端（可选，默认自动创建）
        writer: 数据写入器（可选，默认自动创建）
        tickers: 标的列表（默认使用配置中的 TARGET_SYMBOLS）
        target_dte: 目标 DTE（默认使用配置中的 TARGET_DTE）

    Returns:
        {ticker: data_record_or_None}
    """
    if tickers is None:
        tickers = TARGET_SYMBOLS
    if writer is None:
        writer = DataWriter()

    expiration_date = find_nearest_expiration(target_dte or TARGET_DTE)

    _own_client = polygon_client is None
    if _own_client:
        polygon_client = PolygonClient()

    try:
        logger.info(f"开始批量抓取 {len(tickers)} 个标的: {tickers}")

        # 使用 asyncio.gather 并发拉取（受速率限制器控制）
        tasks = [
            fetch_single_ticker(polygon_client, writer, ticker, expiration_date)
            for ticker in tickers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # 整理结果
        output: dict[str, Optional[dict[str, Any]]] = {}
        for ticker, result in zip(tickers, results):
            if isinstance(result, Exception):
                logger.error(f"[{ticker}] 任务异常: {result}")
                output[ticker] = None
            else:
                output[ticker] = result

        success_count = sum(1 for v in output.values() if v is not None)
        logger.info(f"批量抓取完成: {success_count}/{len(tickers)} 个标的成功")

        return output

    finally:
        if _own_client and polygon_client:
            await polygon_client.close()


# ---------------------------------------------------------------------------
# 便捷同步入口
# ---------------------------------------------------------------------------

def run_eod_fetch_sync(
    tickers: Optional[list[str]] = None,
    target_dte: Optional[int] = None,
) -> dict[str, Optional[dict[str, Any]]]:
    """
    同步方式运行一次 EOD 数据抓取（适用于非异步上下文）。

    用法:
        from src.data_ingestion.eod_fetcher import run_eod_fetch_sync
        results = run_eod_fetch_sync()
    """
    return asyncio.run(fetch_all_tickers(tickers=tickers, target_dte=target_dte))
