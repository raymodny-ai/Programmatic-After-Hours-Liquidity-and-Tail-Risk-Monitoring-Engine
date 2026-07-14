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
from src.data_ingestion.vix_client import VIXClient
from src.data_ingestion.finra_scraper import FINRAScraper
from src.calculation.skew_calculator import process_all_tickers, calculate_cross_asset_spreads
from src.calculation.master_aggregator import aggregate_results
from src.calculation.risk_signals import format_alert_summary
from src.calculation.term_structure import analyze_term_structure_history, generate_term_structure_alert
from src.calculation.cross_asset_signals import check_all_cross_asset_alerts
from src.calculation.volatility_regime import (
    compute_volatility_regime,
    compute_vxn_vix_spread,
    build_qqq_tail_risk_confirmation,
)
from src.calculation.vxn_alert_engine import (
    calculate_vxn_alert,
    AlertStateManager,
)
from src.presentation.logging_setup import setup_logging
from src.presentation.terminal_alerts import print_full_report
# web_dashboard 是 v1.2.1 单体面板 (含 plotly)。V1.3 Headless API 不需要 plotly。
# 改为函数内 lazy import (仅 push-only / --serve 模式才用)。


async def run_full_pipeline() -> dict[str, Any]:
    """
    运行完整的 ETL 流水线 (V1.2 - Web UI 版)。

    流程:
        1. 验证配置
        2. 抓取所有标的的期权数据 + VIX 期限结构数据
        3. 清洗数据 + 计算 Skew + 跨标的统计检验
        4. 汇总结果到主数据帧 + 保存快照
        5. 显示终端报告
        6. 提示 Web 看板可用
    """
    # Step 0: 验证配置
    warnings = validate_config()
    if warnings:
        for w in warnings:
            logger.warning(f"配置警告: {w}")

    writer = DataWriter()

    # ─────────────────────────────────────────────────────────────
    # Phase 1: 数据抓取
    # ─────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Phase 1/4: 数据抓取 - EOD 期权链 + VIX 期限结构")
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

    # ── v1.2.1: VIX + VXN 并行拉取与波动率状态分析 ──
    term_structure_status = None
    vix_regime = None
    vxn_regime = None
    vxn_vix_signal = None
    qqq_tail_confirmation = None
    # ── v1.2.2 (OpenClaw patch 2026-07-11): 在 try 之前初始化，避免抓取异常导致 UnboundLocalError ──
    vxn_alert_result = None
    vxn_notification_action = None

    try:
        vix_client = VIXClient()

        # 并行拉取 VIX 和 VXN
        vix_result, vxn_result = await asyncio.gather(
            vix_client.fetch_vix_history(),
            vix_client.fetch_vxn_history(),
            return_exceptions=True,
        )

        if isinstance(vix_result, pd.DataFrame):
            vix_regime = compute_volatility_regime(
                vix_result, index_name="VIX",
            )
            logger.info(
                f"VIX 波动率状态: level={vix_regime.get('current_level')}, "
                f"z_score={vix_regime.get('z_score')}, "
                f"alert={vix_regime.get('is_alert')}"
            )
            # 兼容旧版 term_structure 接口
            ts_raw = vix_client.get_term_structure(vix_result)
            term_structure_status = ts_raw
            ts_alert = generate_term_structure_alert(ts_raw)
            logger.info(
                f"VIX 期限结构: 近月={ts_raw.get('front_month', 'N/A')}, "
                f"次月={ts_raw.get('second_month', 'N/A')} | "
                f"{ts_alert.get('alert_message', 'N/A')}"
            )
        else:
            logger.warning(f"VIX 拉取失败: {vix_result}")
            vix_regime = {
                "status": "unavailable",
                "is_alert": False,
                "reason": "vix_fetch_failed",
                "as_of_date": date.today().isoformat(),
            }

        if isinstance(vxn_result, pd.DataFrame):
            vxn_regime = compute_volatility_regime(
                vxn_result, index_name="VXN",
            )
            logger.info(
                f"VXN 波动率状态: level={vxn_regime.get('current_level')}, "
                f"z_score={vxn_regime.get('z_score')}, "
                f"alert={vxn_regime.get('is_alert')}"
            )
        else:
            logger.warning(f"VXN 拉取失败: {vxn_result}")
            vxn_regime = {
                "status": "unavailable",
                "is_alert": False,
                "reason": "vxn_fetch_failed",
                "as_of_date": date.today().isoformat(),
            }

        # VXN-VIX spread
        if isinstance(vix_result, pd.DataFrame) and isinstance(vxn_result, pd.DataFrame):
            vxn_vix_signal = compute_vxn_vix_spread(vxn_result, vix_result)
            logger.info(
                f"VXN-VIX spread: {vxn_vix_signal.get('spread')}, "
                f"z_score={vxn_vix_signal.get('z_score')}, "
                f"alert={vxn_vix_signal.get('is_alert')}"
            )

        # ── v1.2.1: VXN 自动化告警引擎（积分制分层状态机）──
        if isinstance(vxn_result, pd.DataFrame) and not vxn_result.empty:
            vxn_alert_result = calculate_vxn_alert(
                vxn_history=vxn_result,
                vix_history=vix_result if isinstance(vix_result, pd.DataFrame) else None,
                qqq_skew_z=None,  # 待 Phase 2 计算后补充
            )
            logger.info(
                f"VXN 告警引擎: severity={vxn_alert_result.get('severity')}, "
                f"score={vxn_alert_result.get('score')}, "
                f"reasons={vxn_alert_result.get('reasons')}"
            )

    except Exception as e:
        logger.warning(f"VIX / VXN 波动率状态分析失败（非致命）: {e}")

    # ─────────────────────────────────────────────────────────────
    # Phase 2: 计算引擎
    # ─────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Phase 2/4: 计算引擎 - Skew 计算 + 统计检验")
    logger.info("=" * 60)

    # 加载历史数据用于 Z-Score 计算
    historical_df = writer.load_master_snapshot()

    skew_results = process_all_tickers(ticker_snapshots)

    # 跨标的剪刀差
    cross_asset_results = calculate_cross_asset_spreads(skew_results)

    # ── 新增: 跨标的剪刀差统计检验（修复缺口四）──
    cross_alerts = check_all_cross_asset_alerts(cross_asset_results, historical_df)
    if cross_alerts:
        for ca in cross_alerts:
            logger.warning(
                f"[跨标的预警] {ca['pair']}: Z={ca['z_score']:.2f} | "
                f"{ca['interpretation']}"
            )
    else:
        logger.info("跨标的剪刀差统计检验：所有对处于正常范围")

    # ─────────────────────────────────────────────────────────────
    # Phase 3: 汇总与存储
    # ─────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Phase 3/4: 汇总与存储 - 更新主数据帧")
    logger.info("=" * 60)

    aggregated = aggregate_results(
        skew_results=skew_results,
        historical_df=historical_df,
        cross_asset_results=cross_asset_results,
        term_structure_status=term_structure_status,
        volatility_regime={
            "vix": vix_regime,
            "vxn": vxn_regime,
            "vxn_vix_spread": vxn_vix_signal,
        },
        as_of_date=date.today(),
    )

    # ── v1.2.1: QQQ 三因子联合确认预警 ──
    qqq_skew_alert = next(
        (a for a in aggregated["alerts"] if a["ticker"] == "QQQ"), None
    )
    qqq_tail_confirmation = build_qqq_tail_risk_confirmation(
        qqq_skew_alert=qqq_skew_alert,
        vxn_regime=vxn_regime,
        vxn_vix_signal=vxn_vix_signal,
    )
    if qqq_tail_confirmation["is_alert"]:
        logger.warning(
            f"[QQQ 尾部风险确认] 三因子确认分数={qqq_tail_confirmation['confirmation_score']}/3, "
            f"严重程度={qqq_tail_confirmation['severity'].upper()}"
        )

    # ── v1.2.1: VXN 告警引擎 — 补充 QQQ Skew Z + 状态管理 ──
    if vxn_alert_result and vxn_alert_result.get("status") == "ok":
        # 提取 QQQ Skew Z-Score
        qqq_skew_z: float | None = None
        if qqq_skew_alert:
            qqq_skew_z = qqq_skew_alert.get("z_score")

        # 重新计算（带 QQQ Skew Z）
        vxn_alert_result = calculate_vxn_alert(
            vxn_history=vxn_result,
            vix_history=vix_result if isinstance(vix_result, pd.DataFrame) else None,
            qqq_skew_z=qqq_skew_z,
        )

        severity = vxn_alert_result.get("severity", "normal")
        as_of = vxn_alert_result.get("as_of_date", "")

        alert_mgr = AlertStateManager()
        should_send, action = alert_mgr.should_notify(
            severity, as_of,
            reasons=vxn_alert_result.get("reasons", []),
        )

        if should_send:
            if action == "upgrade":
                logger.warning(
                    f"[VXN 告警升级] {severity.upper()} (前次: {alert_mgr.get_last_state().get('last_severity')}) | "
                    f"score={vxn_alert_result.get('score')} | "
                    f"reasons={vxn_alert_result.get('reasons')}"
                )
            elif action == "resolved":
                logger.info(
                    f"[VXN 告警解除] 连续 {alert_mgr.resolved_days} 日低于 watch，风险状态解除"
                )
            else:
                logger.warning(
                    f"[VXN 告警] {severity.upper()} | "
                    f"score={vxn_alert_result.get('score')} | "
                    f"reasons={vxn_alert_result.get('reasons')}"
                )
        else:
            logger.info(
                f"[VXN 告警静默] severity={severity}, action={action}, "
                f"score={vxn_alert_result.get('score')}"
            )

    # 保存主数据帧快照
    snapshot_df = aggregated["daily_snapshot_df"]
    writer.save_master_snapshot(snapshot_df)

    # ── V1.3 兼容桥接 ──
    # v1.2.1 写 daily_risk_snapshot.parquet,v13 quant-api-node 通过
    # SnapshotCompat.read_latest_snapshot() 读 latest_snapshot.json。
    # 两者数据契约断裂,此处桥接:parquet -> JSON 字典格式。
    try:
        from v13.quant_state_node.persistence.snapshot_compat import SnapshotCompat
        _snap_json = {
            "date": aggregated.get("as_of_date") or date.today().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "snapshots": {
                row["ticker"]: {
                    "ticker": row["ticker"],
                    "skew_25d": row.get("skew_25d"),
                    "skew_spread": row.get("skew_spread"),
                    "iv_put_25d": row.get("iv_put_25d"),
                    "iv_call_25d": row.get("iv_call_25d"),
                    "put_count": row.get("put_count"),
                    "call_count": row.get("call_count"),
                    "status": row.get("status"),
                    "data_source": row.get("data_source", "yfinance+BSM"),
                    "signal_quality": row.get("signal_quality", "fallback_estimated"),
                }
                for _, row in snapshot_df.iterrows()
            },
            "as_of_date": aggregated.get("as_of_date"),
            "source": "live",
        }
        SnapshotCompat().write_latest_snapshot(_snap_json)
    except Exception as _e:
        logger.warning(f"V1.3 snapshot 桥接失败 (非致命,继续): {_e}")

    # ── v1.2.1: 保存波动率状态快照供 Web 看板读取 ──
    _save_volatility_regime_snapshot(
        vix_regime=vix_regime,
        vxn_regime=vxn_regime,
        vxn_vix_signal=vxn_vix_signal,
        qqq_tail_confirmation=qqq_tail_confirmation,
        vxn_alert=vxn_alert_result,
    )

    # ── v1.2.1: 保存跳过标的快照供 Web 看板展示 ──
    _save_skipped_tickers_snapshot(aggregated.get("skipped_tickers", []))

    # ─────────────────────────────────────────────────────────────
    # Phase 4: 展示层
    # ─────────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("Phase 4/4: 展示层 - 终端报告 + Web 看板可用")
    logger.info("=" * 60)

    # 终端报告（含 VIX 期限结构和宏观杠杆状态）
    print_full_report(
        skew_results=skew_results,
        alerts=aggregated["alerts"],
        cross_asset_results=cross_asset_results,
        term_structure=term_structure_status,
    )

    # 汇总元信息到 aggregated
    aggregated["cross_asset_alerts"] = cross_alerts
    aggregated["term_structure_alert"] = (
        generate_term_structure_alert(term_structure_status)
        if term_structure_status else None
    )
    aggregated["term_structure_status"] = term_structure_status

    # 打印预警摘要
    alert_summary = format_alert_summary(aggregated["alerts"])
    logger.info(f"\n{alert_summary}")
    if cross_alerts:
        logger.warning(f"跨标的预警: {len(cross_alerts)} 个对触发统计显著性异常")

    logger.info(
        "=" * 60 + "\n"
        "✅ 流水线完成。\n"
        "查看 Web 看板: python -m src.presentation.web_dashboard\n"
        "或: python -m src.main --serve\n"
        "访问: http://localhost:8080\n"
        + "=" * 60
    )

    return aggregated


async def run_monthly_macro_pipeline(
    margin_debt_csv: Optional[str] = None,
) -> dict[str, Any]:
    """
    运行月度宏观流动性分析流水线 (V1.1 - 自动 FINRA 爬取)。

    Args:
        margin_debt_csv: FINRA 保证金债务 CSV 文件路径（可选，不传则自动爬取）

    Returns:
        宏观流动性分析结果
    """
    from src.data_ingestion.fred_client import FREDClient
    from src.calculation.macro_leverage import load_margin_debt_csv, run_leverage_analysis
    from src.presentation.terminal_alerts import print_macro_leverage_status

    logger.info("=" * 60)
    logger.info("月度宏观流动性与杠杆压力测试")
    logger.info("=" * 60)

    # ── 新增: 自动爬取 FINRA 数据（修复缺口三）──
    if margin_debt_csv is None:
        logger.info("未指定 CSV 路径，尝试自动爬取 FINRA 保证金债务数据...")
        try:
            scraper = FINRAScraper()
            margin_df = await scraper.fetch_margin_debt()
            logger.info(f"自动爬取成功: {len(margin_df)} 条 FINRA 记录")
        except Exception as e:
            logger.error(
                f"FINRA 自动爬取失败: {e}\n"
                "请手动下载数据:\n"
                "  1. 访问 https://www.finra.org/investors/research-and-tools/margin-statistics\n"
                "  2. 下载 XLSX 文件\n"
                "  3. 使用 --margin-debt-csv <path> 参数传入文件路径"
            )
            return {"error": f"FINRA 数据获取失败: {e}"}
    else:
        margin_df = load_margin_debt_csv(margin_debt_csv)

    try:
        # 加载 M2 数据
        fred = FREDClient()
        m2_df = fred.get_m2_supply()

        # 运行分析
        result = run_leverage_analysis(margin_df, m2_df)

        # 终端输出
        print_macro_leverage_status(result)

        # ── v1.2: 保存宏观分析结果供 Web 看板读取 ──
        if "historical_ratios" in result:
            macro_df = result["historical_ratios"]
            from config.settings import PROCESSED_DATA_DIR
            macro_file = PROCESSED_DATA_DIR / "macro_leverage_snapshot.parquet"
            macro_df.to_parquet(macro_file, index=False)
            logger.info(f"宏观分析结果已保存: {macro_file}")

        return result

    except Exception as e:
        logger.error(f"宏观流动性分析失败: {e}")
        return {"error": str(e)}


def _save_volatility_regime_snapshot(
    vix_regime: Optional[dict] = None,
    vxn_regime: Optional[dict] = None,
    vxn_vix_signal: Optional[dict] = None,
    qqq_tail_confirmation: Optional[dict] = None,
    vxn_alert: Optional[dict] = None,
) -> None:
    """持久化 VIX/VXN 波动率状态快照为 JSON（v1.2.1）。

    Web 看板从此文件读取最新的波动率状态数据。
    """
    import json as _json
    from config.settings import PROCESSED_DATA_DIR

    snapshot = {}
    if vix_regime:
        snapshot["vix"] = vix_regime
    if vxn_regime:
        snapshot["vxn"] = vxn_regime
    if vxn_vix_signal:
        snapshot["vxn_vix_spread"] = vxn_vix_signal
    if qqq_tail_confirmation:
        snapshot["qqq_tail_confirmation"] = qqq_tail_confirmation
    if vxn_alert:
        snapshot["vxn_alert"] = vxn_alert

    if snapshot:
        snapshot["updated_at"] = datetime.now().isoformat()
        file_path = PROCESSED_DATA_DIR / "volatility_regime_snapshot.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            _json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"波动率状态快照已保存: {file_path}")


def _save_skipped_tickers_snapshot(skipped_tickers: list[dict[str, Any]]) -> None:
    """持久化今日跳过标的信息为 JSON（v1.2.1）。

    Web 看板从此文件读取被跳过的标的及原因。
    """
    import json as _json
    from config.settings import PROCESSED_DATA_DIR

    snapshot = {
        "date": date.today().isoformat(),
        "skipped_tickers": skipped_tickers,
        "updated_at": datetime.now().isoformat(),
    }

    file_path = PROCESSED_DATA_DIR / "skipped_tickers_snapshot.json"
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(
        _json.dumps(snapshot, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if skipped_tickers:
        logger.warning(
            f"跳过标的快照已保存: {file_path} "
            f"({len(skipped_tickers)} 个标的被跳过)"
        )
    else:
        logger.info(f"跳过标的快照已保存: {file_path} (今日无跳过标的)")


def parse_args():
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="程序化盘后流动性与尾部风险监控引擎 v1.2",
    )

    parser.add_argument(
        "--mode",
        choices=["full", "fetch-only", "calc-only", "push-only"],
        default="full",
        help="运行模式: full=完整流水线, fetch-only=仅抓取, calc-only=仅计算, push-only=仅启动Web看板",
    )

    parser.add_argument(
        "--serve",
        action="store_true",
        help="运行流水线后自动启动 Web 看板服务器（等价于流水线+push-only）",
    )

    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Web 看板监听端口（默认 8080）",
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
        help="FINRA 保证金债务 CSV 文件路径（可选，不传则自动爬取）",
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

    logger.info("程序化盘后流动性与尾部风险监控引擎 v1.2 启动")
    logger.info(f"运行模式: {args.mode}")

    # ── push-only: 仅启动 Web 看板 ──
    if args.mode == "push-only":
        logger.info("启动 Web 风险看板（push-only 模式）...")
        await web_dashboard.serve_dashboard(port=args.port)
        return {"status": "web_dashboard_started", "port": args.port}

    # ── 月度宏观分析 ──
    if args.macro:
        result = await run_monthly_macro_pipeline(args.margin_debt_csv)
    elif args.mode == "fetch-only":
        # 仅抓取
        tickers = args.tickers or TARGET_SYMBOLS
        logger.info(f"仅抓取模式: tickers={tickers}")
        result = await fetch_all_tickers(tickers=tickers)
        success = sum(1 for v in result.values() if v is not None)
        logger.info(f"抓取完成: {success}/{len(tickers)} 个标的成功")
    elif args.mode == "calc-only":
        # 仅计算（需要已有本地数据）
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

        # ── calc-only 也加入跨标的检验 ──
        cross_alerts = check_all_cross_asset_alerts(cross_asset_results, historical_df)
        if cross_alerts:
            for ca in cross_alerts:
                logger.warning(f"[跨标的预警] {ca['pair']}: Z={ca['z_score']:.2f}")

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

    # ── --serve: 流水线完成后自动启动 Web 看板 ──
    if args.serve and args.mode != "push-only":
        logger.info(f"--serve 已启用，启动 Web 看板 (端口 {args.port})...")
        await web_dashboard.serve_dashboard(port=args.port)

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
