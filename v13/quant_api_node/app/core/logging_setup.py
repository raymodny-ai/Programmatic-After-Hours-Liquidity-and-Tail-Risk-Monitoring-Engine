"""日志（loguru）。

只初始化一次（uvicorn reload 友好）。同时输出到 stdout 与 logs/v13_api.log。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from loguru import logger

from v13.quant_api_node.app.core.config import settings


_LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
    "<level>{message}</level>"
)


_configured = False


def setup_logging() -> None:
    """幂等初始化日志。"""
    global _configured
    if _configured:
        return

    logger.remove()
    logger.add(sys.stdout, format=_LOG_FORMAT, level="INFO", enqueue=True, backtrace=False, diagnose=False)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "v13_api.log",
        format=_LOG_FORMAT,
        level="DEBUG",
        rotation="20 MB",
        retention="30 days",
        enqueue=True,
        backtrace=False,
        diagnose=False,
    )

    # 将 uvicorn / fastapi 日志转发到 loguru
    class _InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                lvl = logger.level(record.levelname).name
            except ValueError:
                lvl = record.levelno
            logger.opt(depth=6).log(lvl, record.getMessage())

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"):
        l = logging.getLogger(name)
        l.handlers = [_InterceptHandler()]
        l.propagate = False

    _configured = True
    logger.info("日志初始化完成: log_dir={}", log_dir.resolve())


def get_logger():
    return logger
