"""
终端预警输出模块 (terminal_alerts.py)

功能：
- 使用 rich 库在终端输出彩色格式化的预警信息
- 显示当前 Skew 值、Z-Score、突破阈值的时间戳
- 提供风险看板摘要表格
"""

from datetime import date, datetime
from typing import Any, Optional

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich import box

# 全局 rich Console 实例
console = Console()


def print_alert_banner(alerts: list[dict[str, Any]]) -> None:
    """
    在终端打印预警横幅。

    如果有预警触发，显示红色警告横幅；否则显示绿色正常状态。

    Args:
        alerts: check_all_ticker_alerts() 返回的预警列表
    """
    active_alerts = [a for a in alerts if a.get("is_alert")]

    if not active_alerts:
        console.print(
            Panel(
                "[green]所有标的的 Skew 值处于正常范围内[/green]\n"
                "未触发尾部风险预警信号。",
                title="[bold green]风险监控状态: 正常[/bold green]",
                border_style="green",
            )
        )
        return

    # 有预警：显示红色警告
    alert_text = Text()
    alert_text.append(f"共 {len(active_alerts)} 个标的触发预警:\n\n", style="bold red")

    for a in active_alerts:
        severity_color = {
            "extreme": "bold red",
            "high": "red",
            "elevated": "yellow",
        }.get(a.get("severity", ""), "white")

        alert_text.append(
            f"  [{a.get('severity', 'unknown').upper()}] ",
            style=severity_color,
        )
        alert_text.append(f"{a['ticker']}: ")
        alert_text.append(
            f"Skew={a['skew_value']:.4f}, Z-Score={a['z_score']:.2f}",
            style="bold",
        )
        alert_text.append(
            f" (均值={a['rolling_mean']:.4f}, σ={a['rolling_std']:.4f})\n"
        )

    console.print(
        Panel(
            alert_text,
            title="[bold red]⚠ 尾部风险预警 ⚠[/bold red]",
            border_style="red",
        )
    )


def print_skew_summary_table(
    skew_results: dict[str, dict[str, Any]],
    cross_asset_results: Optional[list[dict[str, Any]]] = None,
) -> None:
    """
    打印 Skew 计算结果摘要表格。

    Args:
        skew_results: process_all_tickers() 的返回结果
        cross_asset_results: 跨标的剪刀差结果（可选）
    """
    table = Table(
        title=f"每日 Skew 监控摘要 ({date.today().isoformat()})",
        box=box.ROUNDED,
        header_style="bold cyan",
        border_style="cyan",
    )

    table.add_column("标的", style="bold white", width=10)
    table.add_column("Skew Spread", justify="right", width=14)
    table.add_column("IV Put 25Δ", justify="right", width=12)
    table.add_column("IV Call 25Δ", justify="right", width=12)
    table.add_column("Put 点数", justify="right", width=10)
    table.add_column("Call 点数", justify="right", width=10)
    table.add_column("状态", width=10)

    for ticker, result in skew_results.items():
        skew = result.get("skew_spread")
        iv_put = result.get("iv_put_25d")
        iv_call = result.get("iv_call_25d")
        put_n = result.get("put_data_points", 0)
        call_n = result.get("call_data_points", 0)
        error = result.get("error")

        if error:
            status = f"[red]错误[/red]"
            skew_str = "N/A"
        elif skew is None:
            status = "[yellow]插值失败[/yellow]"
            skew_str = "N/A"
        else:
            # Skew 正值 = 下行保护需求强
            if skew > 0.08:
                status = "[red]高[/red]"
            elif skew > 0.04:
                status = "[yellow]中[/yellow]"
            else:
                status = "[green]低[/green]"
            skew_str = f"{skew:.4f}"

        table.add_row(
            ticker,
            skew_str,
            f"{iv_put:.4f}" if iv_put is not None else "N/A",
            f"{iv_call:.4f}" if iv_call is not None else "N/A",
            str(put_n),
            str(call_n),
            status,
        )

    # 跨标的剪刀差
    if cross_asset_results:
        table.add_section()
        for cr in cross_asset_results:
            pair_name = f"{cr['pair'][0]}-{cr['pair'][1]}"
            spread = cr.get("spread")
            table.add_row(
                f"[bold]{pair_name}[/bold]",
                f"[bold]{spread:.4f}[/bold]" if spread is not None else "N/A",
                "-", "-", "-", "-",
                "[dim]剪刀差[/dim]",
            )

    console.print(table)


def print_term_structure_status(status: dict[str, Any]) -> None:
    """
    打印 VIX 期限结构状态。

    Args:
        status: calculate_term_structure_spread() 或 analyze_term_structure_history() 的返回
    """
    current = status.get("current_status", status)

    if not current:
        return

    status_str = current.get("status", "unknown")
    color_map = {
        "contango": "green",
        "flat": "yellow",
        "backwardation": "red",
    }
    color = color_map.get(status_str, "white")

    panel_text = Text()
    panel_text.append(f"价差: {current.get('spread', 'N/A')} 点  |  ", style="bold")
    panel_text.append(
        f"升贴水: {current.get('contango_pct', 'N/A')}%  |  ",
        style="bold",
    )
    panel_text.append(
        f"倒挂: {'是 ⚠' if current.get('is_inverted') else '否'}",
        style="bold red" if current.get("is_inverted") else "green",
    )

    if "inversion_days" in status:
        panel_text.append(
            f"\n历史倒挂频率: {status['inversion_days']}/{status['total_days']} 天 "
            f"({status['inversion_pct']}%)",
        )

    console.print(
        Panel(
            panel_text,
            title=f"[bold {color}]VIX 期限结构: {status_str.upper()}[/bold {color}]",
            border_style=color,
        )
    )


def print_macro_leverage_status(result: dict[str, Any]) -> None:
    """
    打印宏观杠杆状态。

    Args:
        result: run_leverage_analysis() 的返回
    """
    if "error" in result:
        console.print(f"[yellow]宏观杠杆分析: {result['error']}[/yellow]")
        return

    is_alert = result.get("is_alert", False)
    color = "red" if is_alert else "green"
    title = "⚠ 宏观杠杆预警" if is_alert else "宏观杠杆: 正常"

    text = Text()
    text.append(
        f"杠杆占比: {result['ratio_pct']}% (阈值: 6.0%)\n",
        style=f"bold {color}",
    )
    text.append(f"保证金债务: {result['current_margin_debt']:.1f}B USD\n")
    text.append(f"M2 供应量: {result['current_m2']:.1f}B USD\n")

    if result.get("mom_pct") is not None:
        text.append(f"环比变化: {result['mom_pct']:+.2f}%\n")
    if result.get("yoy_pct") is not None:
        text.append(f"同比变化: {result['yoy_pct']:+.2f}%\n")

    if is_alert:
        text.append("\n预警原因:\n", style="bold red")
        for reason in result.get("alert_reasons", []):
            text.append(f"  - {reason}\n", style="red")

    console.print(
        Panel(
            text,
            title=f"[bold {color}]{title}[/bold {color}]",
            border_style=color,
        )
    )


def print_full_report(
    skew_results: dict[str, dict[str, Any]],
    alerts: list[dict[str, Any]],
    cross_asset_results: Optional[list[dict[str, Any]]] = None,
    term_structure: Optional[dict[str, Any]] = None,
    macro_leverage: Optional[dict[str, Any]] = None,
) -> None:
    """
    在终端打印完整的风险监控日报。

    整合所有模块的输出为一份统一的终端报告。
    """
    console.rule("[bold]程序化盘后流动性与尾部风险监控引擎[/bold]")
    console.print(
        f"报告时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        style="dim",
    )
    console.print()

    # 1. 预警横幅
    print_alert_banner(alerts)

    # 2. Skew 摘要表格
    print_skew_summary_table(skew_results, cross_asset_results)

    # 3. 期限结构
    if term_structure:
        print_term_structure_status(term_structure)

    # 4. 宏观杠杆
    if macro_leverage:
        print_macro_leverage_status(macro_leverage)

    console.rule()
