"""
主入口脚本 (main.py)

功能：
- 串联完整的 ETL 流水线：数据拉取 -> 清洗计算 -> 推送看板
- 支持命令行参数选择运行模式
- 运行模式：full（完整）、fetch-only（仅抓取）、calc-only（仅计算）、push-only（仅推送）

用法:
    python -m src.main                    # 默认运行完整流水线
    python -m src.main --mode fetch-only  # 仅拉取数据
    python -m src.main --mode full        # 完整流水线
"""

import argparse
import asyncio
import sys
from datetime import date, datetime
from typing import Any, Optional

from loguru import logger

from config.settings import validate_config, TARGET_SYMBOLS
from src.data_ingestion.data_writer import DataWriter
from src.data_ingestion.eod_fetcher import fetch_all_tickers
from src.calculation.skew_calculator import process_all_tickers, calculate_cross_asset_spreads
from src.calculation.master_aggregator import aggregate_results
from src.calculation.risk_signals import format_alert_summary
from src.presentation.logging_setup import setup_logging
from src.presentation.terminal_alerts import print_full_report


async def run_full_pipeline() -> dict[str, Any]:
    """
    运行完整的 ETL 流水线。

    流程:
        1. 验证配置
        2. 抓取所有标的的期权数据
        3. 清洗数据 + 计算 Skew
        4. 计算跨标的剪刀差
        5. 汇总结果到主数据帧
        6. 保存快照
        7. 显示终端报告
    """
    # Step 0: 验证配置
    warnings = validate_config()
    if warnings:
        for w in warnings:
            logger.warning(f"配置警告: {w}")

    writer = DataWriter()

    # Step 1: 数据抓取
    logger.info("=" * 60)
    logger.info("Phase 1/3: 数据抓取 - 开始拉取 EOD 期权链")
    logger.info("=" * 60)

    raw_data = await fetch_all_tickers(writer=writer)

    # 筛选成功的数据
    ticker_snapshots = {}
    for ticker, data in raw_data.items():
        if data and data.get("snapshots"):
            ticker_snapshots[ticker] = data["snapshots"]
        else:
            logger.warning(f"[{ticker}] 无可用数据，跳过 Skew 计算")

    if not ticker_snapshots:
        logger.error("所有标的数据抓取失败，终止流水线")
        return {"error": "所有标的数据抓取失败"}

    # Step 2: 计算 Skew
    logger.info("=" * 60)
    logger.info("Phase 2/3: 计算引擎 - 开始 Skew 计算")
    logger.info("=" * 60)

    # 加载历史数据用于 Z-Score 计算
    historical_df = writer.load_master_snapshot()

    skew_results = process_all_tickers(ticker_snapshots)

    # 跨标的剪刀差
    cross_asset_results = calculate_cross_asset_spreads(skew_results)

    # Step 3: 汇总与存储
    logger.info("=" * 60)
    logger.info("Phase 3/3: 展示层 - 汇总结果")
    logger.info("=" * 60)

    aggregated = aggregate_results(
        skew_results=skew_results,
        historical_df=historical_df,
        cross_asset_results=cross_asset_results,
        as_of_date=date.today(),
    )

    # 保存主数据帧快照
    snapshot_df = aggregated["daily_snapshot_df"]
    writer.save_master_snapshot(snapshot_df)

    # Step 4: 终端报告
    print_full_report(
        skew_results=skew_results,
        alerts=aggregated["alerts"],
        cross_asset_results=cross_asset_results,
    )

    # 打印预警摘要
    alert_summary = format_alert_summary(aggregated["alerts"])
    logger.info(f"\n{alert_summary}")

    return aggregated


async def run_monthly_macro_pipeline(
    margin_debt_csv: Optional[str] = None,
) -> dict[str, Any]:
    """
    运行月度宏观流动性分析流水线。

    Args:
        margin_debt_csv: FINRA 保证金债务 CSV 文件路径

    Returns:
        宏观流动性分析结果
    """
    from src.data_ingestion.fred_client import FREDClient
    from src.calculation.macro_leverage import load_margin_debt_csv, run_leverage_analysis
    from src.presentation.terminal_alerts import print_macro_leverage_status

    logger.info("=" * 60)
    logger.info("月度宏观流动性与杠杆压力测试")
    logger.info("=" * 60)

    if margin_debt_csv is None:
        logger.warning(
            "未提供 FINRA 保证金债务 CSV 文件路径。\n"
            "请通过 --margin-debt-csv 参数指定 FINRA 保证金债务数据文件。\n"
            "FINRA 数据下载地址: https://www.finra.org/investors/research-and-tools/margin-statistics"
        )
        return {"error": "未提供保证金债务数据"}

    try:
        # 加载 M2 数据
        fred = FREDClient()
        m2_df = fred.get_m2_supply()

        # 加载保证金债务数据
        margin_df = load_margin_debt_csv(margin_debt_csv)

        # 运行分析
        result = run_leverage_analysis(margin_df, m2_df)

        # 终端输出
        print_macro_leverage_status(result)

        return result

    except Exception as e:
        logger.error(f"宏观流动性分析失败: {e}")
        return {"error": str(e)}


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="程序化盘后流动性与尾部风险监控引擎",
    )

    parser.add_argument(
        "--mode",
        choices=["full", "fetch-only", "calc-only"],
        default="full",
        help="运行模式: full=完整流水线, fetch-only=仅抓取数据, calc-only=仅计算(需要已有数据)",
    )

    parser.add_argument(
        "--macro",
        action="store_true",
        help="运行月度宏观流动性分析",
    )

    parser.add_argument(
        "--margin-debt-csv",
        type=str,
        default=None,
        help="FINRA 保证金债务 CSV 文件路径（用于月度宏观分析）",
    )

    parser.add_argument(
        "--tickers",
        type=str,
        nargs="+",
        default=None,
        help="指定要分析的标的列表（空格分隔），默认分析所有配置的标的",
    )

    return parser.parse_args()


async def async_main():
    """异步主函数。"""
    args = parse_args()

    # 初始化日志
    setup_logging()

    logger.info("程序化盘后流动性与尾部风险监控引擎 v0.1.0 启动")
    logger.info(f"运行模式: {args.mode}")

    if args.macro:
        # 月度宏观流动性分析
        result = await run_monthly_macro_pipeline(args.margin_debt_csv)
    elif args.mode == "fetch-only":
        # 仅抓取
        tickers = args.tickers or TARGET_SYMBOLS
        logger.info(f"仅抓取模式: tickers={tickers}")
        result = await fetch_all_tickers(tickers=tickers)
        success = sum(1 for v in result.values() if v is not None)
        logger.info(f"抓取完成: {success}/{len(tickers)} 个标的成功")
    elif args.mode == "calc-only":
        # 仅计算（需要已有数据）
        logger.info("仅计算模式：从本地加载已保存的数据...")
        writer = DataWriter()
        today = date.today()

        tickers = args.tickers or TARGET_SYMBOLS
        ticker_snapshots = {}
        for ticker in tickers:
            try:
                data = writer.load_raw_json(ticker, today)
                if data and data.get("snapshots"):
                    ticker_snapshots[ticker] = data["snapshots"]
                else:
                    logger.warning(f"[{ticker}] 无本地数据")
            except FileNotFoundError:
                logger.warning(f"[{ticker}] 本地文件不存在: {ticker}_{today.isoformat()}.json")

        if not ticker_snapshots:
            logger.error("无可用的本地数据，请先运行 fetch-only 模式")
            return {"error": "无本地数据"}

        historical_df = writer.load_master_snapshot()
        skew_results = process_all_tickers(ticker_snapshots)
        cross_asset_results = calculate_cross_asset_spreads(skew_results)

        aggregated = aggregate_results(
            skew_results=skew_results,
            historical_df=historical_df,
            cross_asset_results=cross_asset_results,
            as_of_date=today,
        )

        writer.save_master_snapshot(aggregated["daily_snapshot_df"])

        print_full_report(
            skew_results=skew_results,
            alerts=aggregated["alerts"],
            cross_asset_results=cross_asset_results,
        )

        result = aggregated
    else:
        # 完整流水线
        result = await run_full_pipeline()

    logger.info("流水线执行完成")
    return result


def main():
    """同步入口点。"""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("用户中断执行")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"未捕获的异常: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
