"""
任务调度器模块 (scheduler.py)

功能：
- 使用 APScheduler 配置每日盘后（美东时间 17:00）自动执行
- 支持 Windows 任务计划程序 / Linux cron 作为备选方案
- 提供调度器的启动、停止和状态查询

使用方式:
    # 以调度模式运行（持续等待定时触发）
    python -m src.scheduler

    # 或直接运行一次完整流水线
    python -m src.main
"""

import asyncio
import sys
from datetime import datetime, timezone
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from loguru import logger

from src.main import run_full_pipeline
from src.presentation.logging_setup import setup_logging


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
        """每日流水线执行包装（带异常保护）。"""
        logger.info("=" * 60)
        logger.info(f"定时任务触发: 每日 EOD 流水线 ({datetime.now().isoformat()})")
        logger.info("=" * 60)

        try:
            result = await run_full_pipeline()
            if "error" in result:
                logger.error(f"每日流水线执行异常: {result['error']}")
            else:
                logger.info("每日流水线执行成功")
        except Exception as e:
            logger.exception(f"每日流水线执行失败: {e}")

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
    setup_logging()

    logger.info("程序化盘后流动性与尾部风险监控引擎 - 调度器模式")

    scheduler = PipelineScheduler()
    scheduler.configure_daily_jobs()

    # 打印已配置的任务
    for job in scheduler.list_jobs():
        logger.info(f"  任务: {job['name']} | 下次执行: {job['next_run']} | 触发器: {job['trigger']}")

    logger.info("调度器运行中，按 Ctrl+C 退出...")
    scheduler.start(blocking=True)


if __name__ == "__main__":
    main()
