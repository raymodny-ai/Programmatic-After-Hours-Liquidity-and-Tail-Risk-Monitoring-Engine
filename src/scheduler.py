"""
任务调度器模块 (scheduler.py) v1.2

功能：
- 使用 APScheduler 配置每日盘后（美东时间 17:00）自动执行
- v1.2: 集成 NYSE 交易日历，节假日自动跳过
- v1.2: last_run.json 状态持久化，重启后可感知执行历史
- 支持 Windows 任务计划程序 / Linux cron 作为备选方案
- 提供调度器的启动、停止和状态查询
- 集成 VIX 期限结构、FINRA 自动爬取、跨标的统计检验

使用方式:
    # 以调度模式运行（持续等待定时触发）
    python -m src.scheduler

    # 以调度模式运行 + 同时启动 Web 看板
    python -m src.scheduler --serve

    # 或直接运行一次完整流水线
    python -m src.main
"""

import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.main import run_full_pipeline
from src.presentation.logging_setup import setup_logging

# ── v1.2: NYSE 交易日历（可选依赖）──
try:
    import pandas_market_calendars as mcal

    _nyse_calendar = mcal.get_calendar("NYSE")
    _has_trading_calendar = True
    logger.debug("NYSE 交易日历已加载")
except ImportError:
    _nyse_calendar = None
    _has_trading_calendar = False
    logger.debug("pandas_market_calendars 未安装，交易日历功能不可用")

# ── v1.2: 任务状态文件路径 ──
LAST_RUN_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "last_run.json"


class PipelineScheduler:
    """
    ETL 流水线调度器。

    默认每日美东时间 17:00 (北京时间次日 05:00) 执行完整流水线。
    支持额外配置月度宏观分析任务。
    """

    def __init__(self) -> None:
        self._scheduler: Optional[AsyncIOScheduler] = None

    @property
    def scheduler(self) -> AsyncIOScheduler:
        if self._scheduler is None:
            self._scheduler = AsyncIOScheduler(
                timezone="US/Eastern",  # 美东时间
                job_defaults={
                    "coalesce": True,        # 合并错过的执行
                    "max_instances": 1,      # 同时最多运行一个实例
                    "misfire_grace_time": 900,  # 15分钟宽限期
                },
            )
        return self._scheduler

    def configure_daily_jobs(self) -> None:
        """
        配置每日盘后任务。

        - 17:00 EST: 运行完整 EOD 流水线（美股收盘后1小时）
        - 周一至周五执行（美股交易日）
        """
        self.scheduler.add_job(
            self._run_daily_pipeline,
            trigger=CronTrigger(
                day_of_week="mon-fri",
                hour=17,
                minute=0,
                timezone="US/Eastern",
            ),
            id="daily_eod_pipeline",
            name="每日 EOD 流水线",
            replace_existing=True,
        )
        logger.info("已配置每日盘后任务: 美东时间 17:00 (周一至周五)")

    def configure_monthly_jobs(self, margin_debt_csv: Optional[str] = None) -> None:
        """
        配置月度宏观分析任务。

        - 每月第一个交易日: 运行宏观流动性分析
        """
        self.scheduler.add_job(
            self._run_monthly_macro,
            trigger=CronTrigger(
                day="1",
                hour=18,
                minute=0,
                timezone="US/Eastern",
            ),
            id="monthly_macro_analysis",
            name="月度宏观流动性分析",
            kwargs={"margin_debt_csv": margin_debt_csv},
            replace_existing=True,
        )
        logger.info("已配置月度宏观分析任务: 美东时间 每月1日 18:00")

    async def _run_daily_pipeline(self) -> None:
        """每日流水线执行包装（v1.2: 含交易日检查和状态持久化）。"""
        logger.info("=" * 60)
        logger.info(f"定时任务触发: 每日 EOD 流水线 ({datetime.now().isoformat()})")
        logger.info("=" * 60)

        # ── v1.2: NYSE 交易日历检查 ──
        if not self._is_trading_day():
            logger.info("今日为非交易日（节假日/周末），跳过流水线执行")
            return

        # ── v1.2: 检查今日是否已执行 ──
        last = self._load_last_run()
        today_str = date.today().isoformat()
        if last.get("date") == today_str and last.get("status") == "success":
            logger.info(f"今日 ({today_str}) 已成功执行，跳过重复运行")
            return

        try:
            result = await run_full_pipeline()
            if "error" in result:
                logger.error(f"每日流水线执行异常: {result['error']}")
                self._save_last_run(today_str, "failed", str(result.get("error", "")))
            else:
                logger.info("每日流水线执行成功")
                self._save_last_run(today_str, "success")
        except Exception as e:
            logger.exception(f"每日流水线执行失败: {e}")
            self._save_last_run(date.today().isoformat(), "failed", str(e))

    async def _run_monthly_macro(self, margin_debt_csv: Optional[str] = None) -> None:
        """月度宏观分析执行包装。"""
        logger.info("=" * 60)
        logger.info(f"定时任务触发: 月度宏观分析 ({datetime.now().isoformat()})")
        logger.info("=" * 60)

        try:
            from src.main import run_monthly_macro_pipeline

            result = await run_monthly_macro_pipeline(margin_debt_csv)
            if "error" in result:
                logger.warning(f"月度宏观分析: {result['error']}")
            else:
                logger.info("月度宏观分析执行成功")
        except Exception as e:
            logger.exception(f"月度宏观分析执行失败: {e}")

    def _is_trading_day(self) -> bool:
        """v1.2: 检查今天是否为 NYSE 交易日。"""
        if not _has_trading_calendar:
            # 无交易日历时，仅跳过周末
            today = date.today()
            return today.weekday() < 5  # 周一=0, 周日=6
        try:
            today = date.today()
            schedule = _nyse_calendar.schedule(
                start_date=today,
                end_date=today,
            )
            return not schedule.empty
        except Exception:
            return True  # 容错：日历查询失败时默认执行

    def _load_last_run(self) -> dict:
        """v1.2: 加载上次执行状态。"""
        try:
            if LAST_RUN_FILE.exists():
                return json.loads(LAST_RUN_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_last_run(self, run_date: str, status: str, error: str = "") -> None:
        """v1.2: 持久化执行状态到 last_run.json。"""
        try:
            LAST_RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
            LAST_RUN_FILE.write_text(
                json.dumps(
                    {"date": run_date, "status": status, "error": error, "timestamp": datetime.now().isoformat()},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug(f"无法写入 last_run.json: {e}")

    def start(self, blocking: bool = True) -> None:
        """
        启动调度器。

        Args:
            blocking: 是否阻塞主线程（True=持续运行，False=后台运行）
        """
        self.scheduler.start()
        logger.info(f"调度器已启动 (时区: US/Eastern)")

        if blocking:
            try:
                # 保持进程运行
                asyncio.get_event_loop().run_forever()
            except (KeyboardInterrupt, SystemExit):
                self.shutdown()

    def shutdown(self) -> None:
        """关闭调度器。"""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("调度器已关闭")

    def list_jobs(self) -> list[dict]:
        """列出所有已配置的定时任务。"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run": str(job.next_run_time) if job.next_run_time else "N/A",
                "trigger": str(job.trigger),
            })
        return jobs


def main():
    """调度器入口点。"""
    import argparse

    parser = argparse.ArgumentParser(description="尾部风险监控引擎 - 调度器模式")
    parser.add_argument("--serve", action="store_true", help="同时启动 Web 看板服务器")
    parser.add_argument("--port", type=int, default=8080, help="Web 看板端口（默认 8080）")
    args = parser.parse_args()

    setup_logging()

    logger.info("程序化盘后流动性与尾部风险监控引擎 v1.1 - 调度器模式")

    scheduler = PipelineScheduler()
    scheduler.configure_daily_jobs()

    # 打印已配置的任务
    for job in scheduler.list_jobs():
        logger.info(f"  任务: {job['name']} | 下次执行: {job['next_run']} | 触发器: {job['trigger']}")

    # ── 可选: 启动 Web 看板 ──
    if args.serve:
        import threading
        from src.presentation.web_dashboard import start_dashboard

        web_thread = threading.Thread(
            target=start_dashboard,
            kwargs={"port": args.port},
            daemon=True,
        )
        web_thread.start()
        logger.info(f"Web 看板已启动于后台: http://localhost:{args.port}")

    logger.info("调度器运行中，按 Ctrl+C 退出...")
    scheduler.start(blocking=True)


if __name__ == "__main__":
    main()
