"""每日盘后流水线调度器。

使用 APScheduler 的 BackgroundScheduler，注册：

- ``post_market_pipeline``  — 美东 21:00 触发（盘后固化窗口）
- ``midday_health_ping``   — 30 分钟一次的健康检查

时区：通过 ``zoneinfo`` 强制美东。
"""

from __future__ import annotations

import asyncio
import logging
import threading

logger = logging.getLogger(__name__)

_scheduler = None
_lock = threading.Lock()


def _ensure_timezone():
    """确保 zoneinfo / pytz 已安装。"""
    try:
        from zoneinfo import ZoneInfo  # Python 3.9+

        return ZoneInfo("America/New_York")
    except Exception:
        try:
            import pytz  # type: ignore

            return pytz.timezone("America/New_York")
        except Exception:
            return None


def run_post_market_pipeline():
    """调用 v1.2.1 的 run_full_pipeline，复用既有计算逻辑。"""
    from src.main import run_full_pipeline  # 局部导入避免循环

    return run_full_pipeline()


def start_scheduler() -> None:
    """启动后台调度器（idempotent）。"""
    global _scheduler
    with _lock:
        if _scheduler is not None:
            return
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            tz = _ensure_timezone()
            from v13.quant_api_node.app.core.config import settings

            scheduler = BackgroundScheduler(timezone=tz) if tz else BackgroundScheduler()

            # 美东 21:00 触发
            trigger_kwargs: dict = {
                "hour": settings.pipeline_cron_hour_et,
                "minute": settings.pipeline_cron_minute_et,
            }
            if tz is not None:
                trigger_kwargs["timezone"] = tz
            scheduler.add_job(
                _safe_invoke,
                CronTrigger(**trigger_kwargs),
                args=[run_post_market_pipeline],
                id="post_market_pipeline",
                name="V13 盘后流水线 (美东 21:00)",
                replace_existing=True,
                misfire_grace_time=600,
            )

            # 每 30 分钟一次健康巡检
            scheduler.add_job(
                _health_ping,
                "interval",
                minutes=30,
                id="midday_health_ping",
                name="V13 健康巡检 (30 分钟)",
                next_run_time=None,  # 仅由 interval 决定首次时间
                replace_existing=True,
            )

            scheduler.start()
            _scheduler = scheduler
            logger.info(
                "APScheduler 已启动: 美东 %02d:%02d 触发盘后流水线",
                settings.pipeline_cron_hour_et,
                settings.pipeline_cron_minute_et,
            )
        except Exception as e:
            logger.warning("APScheduler 启动失败 (将继续工作，但不调度): %s", e)


def stop_scheduler() -> None:
    global _scheduler
    with _lock:
        if _scheduler is not None:
            try:
                _scheduler.shutdown(wait=False)
            except Exception:
                pass
            _scheduler = None
            logger.info("APScheduler 已停止")


def _safe_invoke(coro_factory) -> None:
    """在线程池中安全执行协程。"""
    try:
        result = coro_factory()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
    except Exception as e:
        logger.exception("调度任务执行失败: %s", e)


def _health_ping() -> None:
    """30 分钟一次健康检查（仅写日志，不抛异常）。"""
    try:
        from v13.quant_api_node.app.core.dependencies import get_redis, get_sqlite

        redis_ok = get_redis().ping()
        sqlite_ok = get_sqlite().ping()
        logger.info("健康巡检: redis=%s sqlite=%s", redis_ok, sqlite_ok)
    except Exception as e:
        logger.warning("健康巡检失败: %s", e)
