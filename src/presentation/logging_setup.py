"""
日志配置模块 (logging_setup.py)

功能：
- 使用 loguru 配置结构化日志
- 按日期自动轮转日志文件
- 支持控制台和文件双输出
- 不同级别的日志分别记录
"""

import sys
from pathlib import Path

from loguru import logger

from config.settings import LOG_LEVEL, LOG_DIR_STR


def setup_logging(
    log_level: str = LOG_LEVEL,
    log_dir: str = LOG_DIR_STR,
) -> None:
    """
    配置全局日志系统。

    日志输出:
        - 控制台: 彩色格式，INFO 及以上级别
        - 文件 (logs/app_{date}.log): 所有级别，按天轮转，保留 30 天
        - 文件 (logs/error_{date}.log): ERROR 及以上级别单独记录

    Args:
        log_level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_dir: 日志目录路径
    """
    # 移除默认的 handler
    logger.remove()

    # 确保日志目录存在
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # 控制台输出（带颜色）
    logger.add(
        sys.stdout,
        level=log_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    # 文件输出 - 所有级别（按天轮转）
    logger.add(
        log_path / "app_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        rotation="00:00",  # 每天午夜轮转
        retention="30 days",  # 保留 30 天
        compression="zip",  # 压缩旧日志
        encoding="utf-8",
    )

    # 文件输出 - 仅错误级别
    logger.add(
        log_path / "error_{time:YYYY-MM-DD}.log",
        level="ERROR",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}\n{exception}",
        rotation="00:00",
        retention="90 days",
        encoding="utf-8",
    )

    logger.info(f"日志系统已初始化: 级别={log_level}, 目录={log_dir}")
