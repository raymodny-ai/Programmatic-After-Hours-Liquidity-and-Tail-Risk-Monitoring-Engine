"""
本地 Web 风险看板 (web_dashboard.py) v1.2

替代原 Google Sheets 推送，提供浏览器端交互式数据可视化。

v1.2 新增:
    - 自动刷新（每 5 分钟 meta refresh）
    - API Key 认证中间件（X-API-Key Header）
    - 月度宏观流动性面板（Margin Debt / M2 Ratio 图表）
    - 移动端响应式布局（CSS Grid）

启动方式:
    python -m src.presentation.web_dashboard
    python -m src.main --serve

访问: http://localhost:8080

API 端点:
    GET  /                   主看板页面（HTML）
    GET  /api/latest         最新一日风险快照（JSON，需认证）
    GET  /api/history/{ticker}  指定标的历史 Skew 数据（JSON）
    GET  /api/export/csv     导出全部历史数据为 CSV
    GET  /api/macro          宏观流动性数据（JSON，v1.2）
"""

from datetime import date, datetime
from io import StringIO
import os
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import plotly.graph_objects as go
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from loguru import logger

from src.data_ingestion.data_writer import DataWriter

# ---------------------------------------------------------------------------
# FastAPI 应用初始化
# ---------------------------------------------------------------------------
app = FastAPI(
    title="尾部风险监控看板",
    description="程序化盘后流动性与尾部风险监控引擎 - Web Dashboard",
    version="1.2.1",
)

# 允许跨域访问（方便局域网内其他设备查看）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── v1.2: API Key 认证中间件 ──
_API_KEY = os.getenv("WEB_DASHBOARD_API_KEY", "")


@app.middleware("http")
async def api_key_auth_middleware(request: Request, call_next):
    """对 /api/ 端点进行 X-API-Key 认证。"""
    if request.url.path.startswith("/api/") and _API_KEY:
        # /api/export/csv 和主页面不需要认证
        api_key = request.headers.get("X-API-Key", "")
        if api_key != _API_KEY:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "message": "请提供有效的 X-API-Key"},
            )
    response = await call_next(request)
    return response

_writer = DataWriter()

# 标的颜色映射
TICKER_COLORS = {
    "SPY": "#1f77b4",
    "QQQ": "#ff7f0e",
    "IWM": "#2ca02c",
    "DIA": "#d62728",
}

# ---------------------------------------------------------------------------
# Plotly 图表构建函数
# ---------------------------------------------------------------------------


def _build_skew_chart(df: pd.DataFrame) -> str:
    """
    生成多标的 Skew 历史走势图（Plotly HTML 片段）。

    功能:
        - 折线图显示各 ETF 的 Skew Spread 随时间变化
        - 红色三角标记预警触发日
        - 支持悬停交互
    """
    fig = go.Figure()
    tickers = [t for t in df["ticker"].unique() if not str(t).startswith("CROSS:")]

    for ticker in tickers:
        sub = df[df["ticker"] == ticker].sort_values("date")
        if sub.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["date"],
                y=sub["skew_spread"],
                mode="lines+markers",
                name=ticker,
                line=dict(color=TICKER_COLORS.get(ticker, "#9467bd"), width=2),
                marker=dict(size=5),
                hovertemplate=(
                    f"<b>{ticker}</b><br>"
                    "日期: %{x}<br>"
                    "Skew: %{y:.4f}<br>"
                    "<extra></extra>"
                ),
            )
        )

    # 标记预警点（所有标的共用）
    alerts_df = df[df["alert_flag"] == True]
    if not alerts_df.empty:
        fig.add_trace(
            go.Scatter(
                x=alerts_df["date"],
                y=alerts_df["skew_spread"],
                mode="markers",
                name="预警触发",
                marker=dict(symbol="triangle-up", size=14, color="#f85149", line=dict(width=1, color="#ffcc00")),
                hovertemplate=(
                    "⚠ <b>预警</b><br>"
                    "日期: %{x}<br>"
                    "Skew: %{y:.4f}<br>"
                    "<extra></extra>"
                ),
            )
        )

    # 零线参考
    fig.add_hline(y=0, line_dash="dash", line_color="#484f58", opacity=0.5)

    fig.update_layout(
        title=dict(text="ETF 25Δ IV Skew 历史走势", font=dict(size=16)),
        xaxis_title="日期",
        yaxis_title="Skew Spread (IV_Put_25Δ - IV_Call_25Δ)",
        hovermode="x unified",
        height=500,
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        legend=dict(
            bgcolor="#161b22",
            bordercolor="#30363d",
            borderwidth=1,
        ),
        margin=dict(l=50, r=20, t=50, b=50),
        xaxis=dict(gridcolor="#21262d", showgrid=True),
        yaxis=dict(gridcolor="#21262d", showgrid=True, zerolinecolor="#484f58"),
    )
    return fig.to_html(full_html=False, include_plotlyjs="cdn", config={"displayModeBar": True})


def _build_cross_asset_chart(df: pd.DataFrame) -> str:
    """
    生成跨标的 Skew 剪刀差走势图。
    """
    cross_df = df[df["ticker"].astype(str).str.startswith("CROSS:")].copy()
    if cross_df.empty:
        return '<p style="color:#8b949e; padding:40px; text-align:center">暂无跨标的剪刀差数据</p>'

    fig = go.Figure()
    colors = ["#58a6ff", "#f0883e", "#3fb950", "#bc8cff"]
    for i, pair_name in enumerate(cross_df["ticker"].unique()):
        sub = cross_df[cross_df["ticker"] == pair_name].sort_values("date")
        if sub.empty:
            continue
        display_name = str(pair_name).replace("CROSS:", "")
        fig.add_trace(
            go.Scatter(
                x=sub["date"],
                y=sub["skew_spread"],
                mode="lines",
                name=display_name,
                line=dict(color=colors[i % len(colors)], width=2),
                hovertemplate=(
                    f"<b>{display_name}</b><br>"
                    "日期: %{x}<br>"
                    "剪刀差: %{y:.4f}<br>"
                    "<extra></extra>"
                ),
            )
        )

    fig.add_hline(y=0, line_dash="dash", line_color="#484f58", opacity=0.5)

    fig.update_layout(
        title="跨标的 Skew 剪刀差 (Cross-Asset Skew Spread)",
        xaxis_title="日期",
        yaxis_title="Spread",
        height=380,
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", borderwidth=1),
        margin=dict(l=50, r=20, t=50, b=50),
        xaxis=dict(gridcolor="#21262d"),
        yaxis=dict(gridcolor="#21262d", zerolinecolor="#484f58"),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": True})


def _build_zscore_chart(df: pd.DataFrame) -> str:
    """
    生成 Z-Score 追踪图（多标的预警强度历史）。
    """
    fig = go.Figure()
    tickers = [t for t in df["ticker"].unique() if not str(t).startswith("CROSS:")]

    for ticker in tickers:
        sub = df[df["ticker"] == ticker].sort_values("date")
        if sub.empty or sub["z_score"].isna().all():
            continue
        fig.add_trace(
            go.Scatter(
                x=sub["date"],
                y=sub["z_score"],
                mode="lines",
                name=ticker,
                line=dict(color=TICKER_COLORS.get(ticker, "#9467bd"), width=1.5, dash="dot"),
                hovertemplate=(
                    f"<b>{ticker}</b><br>"
                    "日期: %{x}<br>"
                    "Z-Score: %{y:.2f}<br>"
                    "<extra></extra>"
                ),
            )
        )

    # ±2σ 阈值线
    fig.add_hline(y=2, line_dash="dash", line_color="#f85149", opacity=0.7, annotation_text="+2σ 预警线")
    fig.add_hline(y=-2, line_dash="dash", line_color="#f85149", opacity=0.7, annotation_text="-2σ")

    fig.update_layout(
        title="Skew Z-Score 追踪（90日滚动窗口）",
        xaxis_title="日期",
        yaxis_title="Z-Score",
        height=350,
        plot_bgcolor="#0d1117",
        paper_bgcolor="#0d1117",
        font=dict(color="#c9d1d9", size=12),
        legend=dict(bgcolor="#161b22", bordercolor="#30363d", borderwidth=1),
        margin=dict(l=50, r=20, t=50, b=50),
        xaxis=dict(gridcolor="#21262d"),
        yaxis=dict(gridcolor="#21262d"),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": True})


# ---------------------------------------------------------------------------
# HTML 构建函数
# ---------------------------------------------------------------------------

SEVERITY_COLORS = {
    "extreme": "#f85149",
    "high": "#f0883e",
    "elevated": "#d29922",
    "normal": "#3fb950",
    "unknown": "#8b949e",
}


def _build_alert_table(df: pd.DataFrame) -> str:
    """生成最新预警 HTML 表格。"""
    latest_date = df["date"].max()
    today_df = df[df["date"] == latest_date]
    alerts = today_df[today_df["alert_flag"] == True]
    non_cross = alerts[~alerts["ticker"].astype(str).str.startswith("CROSS:")]

    if non_cross.empty:
        return '<p style="color:#3fb950; font-size:15px; padding:16px">✅ 今日无预警触发，所有标的 Skew 处于正常范围。</p>'

    rows = ""
    for _, row in non_cross.iterrows():
        severity = str(row.get("alert_severity", "unknown"))
        color = SEVERITY_COLORS.get(severity, "#8b949e")
        z = row.get("z_score", float("nan"))
        z_str = f"{z:.2f}" if pd.notna(z) else "N/A"
        skew = row.get("skew_spread")
        skew_str = f"{skew:.4f}" if skew is not None else "N/A"

        rows += f"""<tr>
            <td><span style="font-weight:bold">{row['ticker']}</span></td>
            <td>{skew_str}</td>
            <td>{z_str}</td>
            <td><span style="color:{color}; font-weight:bold">● {severity.upper()}</span></td>
            <td>{row.get('alert_direction', '-')}</td>
        </tr>"""

    return f"""<table class="alert-table">
      <thead><tr>
        <th>标的</th><th>Skew</th><th>Z-Score</th><th>严重程度</th><th>方向</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""


def _build_summary_cards(df: pd.DataFrame) -> str:
    """生成最新一日各标的摘要卡片（v1.2.1: 含 data_source 质量标记）。"""
    latest_date = df["date"].max()
    latest = df[df["date"] == latest_date]
    non_cross = latest[~latest["ticker"].astype(str).str.startswith("CROSS:")]

    cards = ""
    for _, row in non_cross.iterrows():
        alert_flag = row.get("alert_flag", False)
        alert_color = "#f85149" if alert_flag else "#3fb950"
        border_color = "#f85149" if alert_flag else "#30363d"
        status_text = "⚠ 预警" if alert_flag else "✓ 正常"

        z = row.get("z_score", float("nan"))
        z_str = f"{z:.2f}" if pd.notna(z) else "N/A"

        skew = row.get("skew_spread")
        skew_str = f"{skew:.4f}" if skew is not None else "N/A"

        iv_put = row.get("iv_put_25d")
        iv_call = row.get("iv_call_25d")
        iv_put_str = f"{iv_put:.4f}" if iv_put is not None else "-"
        iv_call_str = f"{iv_call:.4f}" if iv_call is not None else "-"

        # ── v1.2.1: data_source 质量标记 ──
        data_source = row.get("data_source", "polygon")
        source_badge = ""
        if data_source == "yfinance":
            source_badge = (
                '<span style="background:#d29922;color:#0d1117;font-size:10px;'
                'padding:1px 6px;border-radius:8px;margin-left:6px">EST</span>'
            )
        elif data_source == "polygon":
            source_badge = (
                '<span style="background:#3fb950;color:#0d1117;font-size:10px;'
                'padding:1px 6px;border-radius:8px;margin-left:6px">PRIMARY</span>'
            )

        cards += f"""
        <div class="metric-card" style="border-color:{border_color}">
            <div class="metric-ticker">{row['ticker']}{source_badge}</div>
            <div class="metric-value">{skew_str}</div>
            <div class="metric-label">Skew Spread</div>
            <div class="metric-detail">
                <span>Put IV: {iv_put_str}</span>
                <span>Call IV: {iv_call_str}</span>
            </div>
            <div class="metric-detail">
                <span>Z-Score: {z_str}</span>
            </div>
            <div class="metric-status" style="color:{alert_color}">{status_text}</div>
        </div>"""

    # 跨标的剪刀差卡片
    cross = latest[latest["ticker"].astype(str).str.startswith("CROSS:")]
    for _, row in cross.iterrows():
        pair_name = str(row["ticker"]).replace("CROSS:", "")
        spread = row.get("skew_spread")
        spread_str = f"{spread:.4f}" if spread is not None else "N/A"
        cards += f"""
        <div class="metric-card" style="border-color:#30363d">
            <div class="metric-ticker" style="color:#8b949e">{pair_name}</div>
            <div class="metric-value" style="font-size:18px">{spread_str}</div>
            <div class="metric-label">剪刀差</div>
            <div class="metric-detail"><span>跨标的 Spread</span></div>
            <div class="metric-status" style="color:#58a6ff">Cross-Asset</div>
        </div>"""

    return cards


# ---------------------------------------------------------------------------
# 路由端点
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard(ticker_filter: Optional[str] = Query(None, alias="ticker")):
    """
    主看板页面。

    显示内容:
        - 最新一日各标的 Skew 摘要卡片
        - 今日预警状态表格
        - Skew 历史走势图
        - Z-Score 追踪图
        - 跨标的剪刀差图
    """
    df = _writer.load_master_snapshot()

    if df.empty:
        return HTMLResponse(content=_render_empty_state(), status_code=200)

    # 确保 date 列是 datetime 类型
    df["date"] = pd.to_datetime(df["date"])

    # 可选: 按标的筛选
    if ticker_filter:
        ticker_upper = ticker_filter.upper()
        mask = (df["ticker"] == ticker_upper) | (df["ticker"] == f"CROSS:{ticker_upper}")
        if mask.any():
            df = df[mask].copy()

    latest_date = df["date"].max().strftime("%Y-%m-%d")
    total_records = len(df)

    # 构建图表
    skew_chart = _build_skew_chart(df)
    cross_chart = _build_cross_asset_chart(df)
    zscore_chart = _build_zscore_chart(df)
    macro_chart = _build_macro_chart()
    volatility_cards = _build_volatility_cards()  # v1.2.1
    alert_table = _build_alert_table(df)
    summary_cards = _build_summary_cards(df)

    # 统计数据
    alert_count = len(df[(df["date"] == df["date"].max()) & (df["alert_flag"] == True)])

    html = _render_page(
        latest_date=latest_date,
        total_records=total_records,
        alert_count=alert_count,
        summary_cards=summary_cards,
        alert_table=alert_table,
        skew_chart=skew_chart,
        zscore_chart=zscore_chart,
        cross_chart=cross_chart,
        macro_chart=macro_chart,
        volatility_cards=volatility_cards,
    )
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# JSON API 端点
# ---------------------------------------------------------------------------


@app.get("/api/latest")
async def api_latest():
    """
    返回最新一日的风险快照 JSON。

    用途: 供外部系统（如大屏展示、自动化报警）对接。
    """
    df = _writer.load_master_snapshot()
    if df.empty:
        return {"error": "no_data", "message": "本地暂无风险快照数据"}

    df["date"] = pd.to_datetime(df["date"])
    latest = df[df["date"] == df["date"].max()]
    return {
        "date": df["date"].max().strftime("%Y-%m-%d"),
        "total_records": len(latest),
        "records": latest.where(pd.notna(latest), None).to_dict(orient="records"),
    }


@app.get("/api/history/{ticker}")
async def api_ticker_history(ticker: str):
    """
    返回指定标的的完整历史 Skew 数据 JSON。

    参数:
        ticker: 标的符号 (如 SPY, QQQ, IWM, DIA, CROSS:QQQ-SPY)
    """
    df = _writer.load_master_snapshot()
    if df.empty:
        return {"ticker": ticker.upper(), "history": [], "message": "暂无数据"}

    ticker_upper = ticker.upper()
    sub = df[df["ticker"] == ticker_upper].sort_values("date")
    if sub.empty and not ticker_upper.startswith("CROSS:"):
        # 尝试 CROSS: 前缀
        sub = df[df["ticker"] == f"CROSS:{ticker_upper}"].sort_values("date")

    sub["date"] = sub["date"].astype(str)
    return {
        "ticker": ticker_upper,
        "count": len(sub),
        "history": sub.where(pd.notna(sub), None).to_dict(orient="records"),
    }


@app.get("/api/export/csv")
async def api_export_csv():
    """
    导出全部历史风险数据为 CSV 文件。

    用途: 支持本地下载分析，替代原 Google Sheets 导出功能。
    """
    df = _writer.load_master_snapshot()
    if df.empty:
        return StreamingResponse(
            iter(["<empty>"]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=risk_data_empty.csv"},
        )

    stream = StringIO()
    df.to_csv(stream, index=False)
    stream.seek(0)

    filename = f"risk_snapshot_{date.today().isoformat()}.csv"
    return StreamingResponse(
        iter([stream.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/stats")
async def api_stats():
    """返回数据统计摘要。"""
    df = _writer.load_master_snapshot()
    if df.empty:
        return {"status": "empty", "message": "暂无数据"}

    df["date"] = pd.to_datetime(df["date"])
    latest_date = df["date"].max()
    oldest_date = df["date"].min()

    tickers = [t for t in df["ticker"].unique() if not str(t).startswith("CROSS:")]
    ticker_stats = {}
    for t in tickers:
        sub = df[df["ticker"] == t]
        ticker_stats[t] = {
            "days": len(sub),
            "avg_skew": round(sub["skew_spread"].mean(), 6) if not sub["skew_spread"].isna().all() else None,
            "max_skew": round(sub["skew_spread"].max(), 6) if not sub["skew_spread"].isna().all() else None,
            "alert_days": int(sub["alert_flag"].sum()),
        }

    return {
        "oldest_date": oldest_date.strftime("%Y-%m-%d"),
        "latest_date": latest_date.strftime("%Y-%m-%d"),
        "total_records": len(df),
        "ticker_stats": ticker_stats,
    }


# ── v1.2: 宏观流动性 API ──

@app.get("/api/macro")
async def api_macro():
    """
    返回宏观流动性分析数据（FINRA Margin Debt / M2 Ratio）。

    数据来源：已缓存的月度宏观分析结果。
    """
    from config.settings import PROCESSED_DATA_DIR

    macro_file = Path(PROCESSED_DATA_DIR) / "macro_leverage_snapshot.parquet"
    if not macro_file.exists():
        return {"status": "empty", "message": "暂无宏观流动性数据，请先运行 python -m src.main --macro"}

    try:
        df = pd.read_parquet(macro_file)
        df["date_margin"] = pd.to_datetime(df["date_margin"])
        df = df.sort_values("date_margin")

        return {
            "status": "ok",
            "count": len(df),
            "latest": {
                "date": df["date_margin"].max().strftime("%Y-%m-%d"),
                "leverage_ratio": round(float(df["leverage_ratio"].iloc[-1]) * 100, 2),
                "margin_debt": float(df["margin_debt"].iloc[-1]),
                "m2_supply": float(df["m2_supply"].iloc[-1]),
            },
            "history": df.where(pd.notna(df), None).to_dict(orient="records"),
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ── v1.2.1: VIX/VXN 波动率状态 API ──


@app.get("/api/volatility")
async def api_volatility():
    """返回 VIX/VXN 波动率状态数据（v1.2.1）。

    数据来源：run_full_pipeline 持久化的 volatility_regime_snapshot.json。
    """
    from config.settings import PROCESSED_DATA_DIR

    vol_file = Path(PROCESSED_DATA_DIR) / "volatility_regime_snapshot.json"
    if not vol_file.exists():
        return {
            "status": "empty",
            "message": "暂无波动率状态数据，请先运行流水线",
        }

    try:
        import json
        data = json.loads(vol_file.read_text(encoding="utf-8"))
        return {"status": "ok", "data": data}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ---------------------------------------------------------------------------
# v1.2: 宏观流动性图表
# ---------------------------------------------------------------------------


def _build_volatility_cards() -> str:
    """构建 VIX/VXN 波动率状态卡片（v1.2.1）。"""
    from config.settings import PROCESSED_DATA_DIR
    import json

    vol_file = Path(PROCESSED_DATA_DIR) / "volatility_regime_snapshot.json"
    if not vol_file.exists():
        return '<p style="color:#8b949e; padding:20px; text-align:center">暂无波动率状态数据。运行流水线后刷新。</p>'

    try:
        data = json.loads(vol_file.read_text(encoding="utf-8"))
    except Exception:
        return '<p style="color:#f85149; padding:20px">波动率数据加载失败</p>'

    cards = ""

    # VIX 卡片
    vix = data.get("vix", {})
    if vix and vix.get("status") == "ok":
        alert_color = "#f85149" if vix.get("is_alert") else "#3fb950"
        cards += f"""
        <div class="metric-card" style="border-color:#30363d; border-left: 3px solid #1f77b4">
            <div class="metric-ticker">VIX</div>
            <div class="metric-value">{vix.get('current_level', 'N/A')}</div>
            <div class="metric-label">当前水平</div>
            <div class="metric-detail">
                <span>Z: {vix.get('z_score', 'N/A')}</span>
                <span>20d: {_fmt_pct(vix.get('change_20d'))}</span>
            </div>
            <div class="metric-status" style="color:{alert_color}">{vix.get('alert_level', 'normal').upper()}</div>
        </div>"""

    # VXN 卡片
    vxn = data.get("vxn", {})
    if vxn and vxn.get("status") == "ok":
        alert_color = "#f85149" if vxn.get("is_alert") else "#3fb950"
        cards += f"""
        <div class="metric-card" style="border-color:#30363d; border-left: 3px solid #ff7f0e">
            <div class="metric-ticker">VXN (Nasdaq)</div>
            <div class="metric-value">{vxn.get('current_level', 'N/A')}</div>
            <div class="metric-label">当前水平</div>
            <div class="metric-detail">
                <span>Z: {vxn.get('z_score', 'N/A')}</span>
                <span>20d: {_fmt_pct(vxn.get('change_20d'))}</span>
            </div>
            <div class="metric-status" style="color:{alert_color}">{vxn.get('alert_level', 'normal').upper()}</div>
        </div>"""

    # VXN-VIX Spread 卡片
    spread = data.get("vxn_vix_spread", {})
    if spread and spread.get("status") == "ok":
        alert_color = "#f85149" if spread.get("is_alert") else "#3fb950"
        cards += f"""
        <div class="metric-card" style="border-color:#30363d; border-left: 3px solid #bc8cff">
            <div class="metric-ticker">VXN-VIX Spread</div>
            <div class="metric-value">{spread.get('spread', 'N/A')}</div>
            <div class="metric-label">相对压力</div>
            <div class="metric-detail">
                <span>Z: {spread.get('z_score', 'N/A')}</span>
            </div>
            <div class="metric-status" style="color:{alert_color}">
                {'⚠ 科技板块压力偏高' if spread.get('is_alert') else '✓ 正常'}
            </div>
        </div>"""

    # QQQ 三因子确认卡片
    qqq = data.get("qqq_tail_confirmation", {})
    if qqq:
        score = qqq.get("confirmation_score", 0)
        sev_color = {"normal": "#3fb950", "high": "#f0883e", "critical": "#f85149"}
        sev = qqq.get("severity", "normal")
        cards += f"""
        <div class="metric-card" style="border-color:#30363d; border-left: 3px solid {sev_color.get(sev, '#8b949e')}">
            <div class="metric-ticker">QQQ 尾部风险确认</div>
            <div class="metric-value" style="font-size:22px">{score}/3</div>
            <div class="metric-label">三因子确认分数</div>
            <div class="metric-detail">
                <span>Skew: {'⚠' if qqq.get('components', {}).get('qqq_skew') else '✓'}</span>
                <span>VXN: {'⚠' if qqq.get('components', {}).get('vxn_level') else '✓'}</span>
                <span>Spread: {'⚠' if qqq.get('components', {}).get('vxn_vix_relative') else '✓'}</span>
            </div>
            <div class="metric-status" style="color:{sev_color.get(sev, '#8b949e')}">{sev.upper()}</div>
        </div>"""

    if not cards:
        return '<p style="color:#8b949e; padding:20px; text-align:center">波动率状态数据不完整</p>'

    return cards


def _fmt_pct(value) -> str:
    """格式化百分比"""
    if value is None:
        return "N/A"
    try:
        return f"{float(value) * 100:+.1f}%"
    except (ValueError, TypeError):
        return "N/A"


def _build_macro_chart() -> str:
    """生成宏观流动性杠杆占比历史走势图。"""
    from config.settings import PROCESSED_DATA_DIR

    macro_file = Path(PROCESSED_DATA_DIR) / "macro_leverage_snapshot.parquet"
    if not macro_file.exists():
        return '<p style="color:#8b949e; padding:40px; text-align:center">暂无宏观流动性数据。运行 <code>python -m src.main --macro</code> 后刷新。</p>'

    try:
        df = pd.read_parquet(macro_file)
        df["date_margin"] = pd.to_datetime(df["date_margin"])
        df = df.sort_values("date_margin")

        fig = go.Figure()

        # 杠杆占比折线
        fig.add_trace(
            go.Scatter(
                x=df["date_margin"],
                y=df["leverage_ratio"] * 100,
                mode="lines+markers",
                name="杠杆占比 (%)",
                line=dict(color="#58a6ff", width=2),
                marker=dict(size=4),
                hovertemplate="日期: %{x}<br>杠杆占比: %{y:.2f}%<extra></extra>",
            )
        )

        # 6% 阈值线
        fig.add_hline(
            y=6.0,
            line_dash="dash",
            line_color="#f85149",
            opacity=0.7,
            annotation_text="6% 预警阈值",
        )

        fig.update_layout(
            title="宏观杠杆占比: Margin Debt / M2 Supply",
            xaxis_title="日期",
            yaxis_title="杠杆占比 (%)",
            height=380,
            plot_bgcolor="#0d1117",
            paper_bgcolor="#0d1117",
            font=dict(color="#c9d1d9", size=12),
            legend=dict(bgcolor="#161b22", bordercolor="#30363d", borderwidth=1),
            margin=dict(l=50, r=20, t=50, b=50),
            xaxis=dict(gridcolor="#21262d"),
            yaxis=dict(gridcolor="#21262d"),
        )
        return fig.to_html(full_html=False, include_plotlyjs=False, config={"displayModeBar": True})

    except Exception as e:
        return f'<p style="color:#f85149; padding:20px">宏观数据加载失败: {e}</p>'


# ---------------------------------------------------------------------------
# HTML 渲染
# ---------------------------------------------------------------------------


def _render_page(
    latest_date: str,
    total_records: int,
    alert_count: int,
    summary_cards: str,
    alert_table: str,
    skew_chart: str,
    zscore_chart: str,
    cross_chart: str,
    macro_chart: str = "",
    volatility_cards: str = "",
) -> str:
    """渲染完整的看板 HTML 页面（v1.2.1: 含波动率面板）。"""
    alert_badge = (
        f'<span class="badge badge-alert">⚠ {alert_count} 预警</span>'
        if alert_count > 0
        else '<span class="badge badge-ok">✓ 正常</span>'
    )

    macro_section = ""
    if macro_chart:
        macro_section = f"""
        <div class="section">
            <div class="section-title">
                <span class="icon">🏦</span> 宏观流动性面板 - Margin Debt / M2 Ratio
            </div>
            <div class="chart-box">{macro_chart}</div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta http-equiv="refresh" content="300">
    <title>尾部风险监控看板 | After-Hours Tail Risk Monitor</title>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: #0d1117;
            color: #c9d1d9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            font-size: 14px;
            line-height: 1.5;
        }}
        .header {{
            background: #161b22;
            border-bottom: 1px solid #30363d;
            padding: 16px 28px;
            display: flex;
            align-items: center;
            gap: 12px;
            position: sticky;
            top: 0;
            z-index: 100;
        }}
        .header h1 {{ font-size: 18px; color: #f0f6fc; font-weight: 600; }}
        .badge {{
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
        }}
        .badge-alert {{ background: #da3633; color: #fff; }}
        .badge-ok {{ background: #238636; color: #fff; }}
        .badge-info {{ background: #1f6feb; color: #fff; }}
        .date-tag {{ color: #8b949e; font-size: 12px; margin-left: auto; }}
        .header-actions {{ display: flex; gap: 8px; margin-left: 16px; }}
        .btn {{
            background: #21262d;
            color: #c9d1d9;
            border: 1px solid #30363d;
            padding: 5px 14px;
            border-radius: 6px;
            font-size: 12px;
            cursor: pointer;
            text-decoration: none;
            transition: background 0.2s;
        }}
        .btn:hover {{ background: #30363d; }}
        .btn-primary {{ background: #1f6feb; border-color: #1f6feb; color: #fff; }}
        .btn-primary:hover {{ background: #388bfd; }}
        .main {{ padding: 24px 28px; max-width: 1500px; margin: 0 auto; }}
        .section {{ margin-bottom: 32px; }}
        .section-title {{
            color: #f0f6fc;
            font-size: 15px;
            font-weight: 600;
            margin-bottom: 14px;
            padding-bottom: 8px;
            border-bottom: 1px solid #21262d;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .section-title .icon {{ font-size: 16px; }}
        .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }}
        .metric-card {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 16px 20px;
            min-width: 160px;
            text-align: center;
            flex: 1 1 auto;
            transition: border-color 0.3s;
        }}
        .metric-card:hover {{ border-color: #58a6ff; }}
        .metric-ticker {{ color: #f0f6fc; font-size: 13px; font-weight: 600; margin-bottom: 6px; }}
        .metric-value {{ color: #f0f6fc; font-size: 26px; font-weight: 700; margin-bottom: 4px; }}
        .metric-label {{ color: #8b949e; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }}
        .metric-detail {{
            color: #8b949e;
            font-size: 11px;
            margin-top: 6px;
            display: flex;
            justify-content: center;
            gap: 12px;
        }}
        .metric-status {{ font-size: 12px; font-weight: 600; margin-top: 8px; }}
        .chart-box {{
            background: #0d1117;
            border: 1px solid #21262d;
            border-radius: 10px;
            padding: 16px;
            overflow: hidden;
        }}
        .alert-box {{
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 16px;
        }}
        .alert-table {{ width: 100%; border-collapse: collapse; }}
        .alert-table th, .alert-table td {{
            padding: 10px 14px;
            text-align: left;
            border-bottom: 1px solid #21262d;
        }}
        .alert-table th {{ color: #8b949e; font-weight: 600; font-size: 12px; text-transform: uppercase; }}
        .alert-table tbody tr:hover {{ background: #1c2128; }}
        .empty-state {{
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 60vh;
            color: #8b949e;
            text-align: center;
        }}
        .empty-state h2 {{ color: #f0f6fc; margin-bottom: 12px; }}
        .empty-state code {{
            background: #161b22;
            border: 1px solid #30363d;
            padding: 4px 10px;
            border-radius: 4px;
            font-size: 13px;
        }}
        .footer {{
            padding: 20px 28px;
            text-align: center;
            color: #484f58;
            font-size: 11px;
            border-top: 1px solid #21262d;
        }}
        /* ── v1.2: 移动端响应式 ── */
        @media (max-width: 768px) {{
            .header {{ flex-wrap: wrap; padding: 12px 16px; gap: 8px; }}
            .header h1 {{ font-size: 14px; }}
            .date-tag {{ margin-left: 0; font-size: 10px; }}
            .header-actions {{ margin-left: 0; }}
            .main {{ padding: 12px 12px; }}
            .cards {{ flex-direction: column; }}
            .metric-card {{ min-width: 100%; padding: 12px; }}
            .metric-value {{ font-size: 20px; }}
            .chart-box {{ padding: 8px; }}
            .alert-table th, .alert-table td {{ padding: 6px 8px; font-size: 11px; }}
            .btn {{ padding: 4px 10px; font-size: 10px; }}
            .section-title {{ font-size: 13px; }}
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🛡 程序化盘后尾部风险监控引擎</h1>
        <span class="badge badge-info">Web UI v1.2</span>
        {alert_badge}
        <span class="date-tag">最新数据: {latest_date} | 总计 {total_records} 条记录</span>
        <div class="header-actions">
            <a href="/api/export/csv" class="btn">📥 导出 CSV</a>
            <a href="/api/latest" class="btn" target="_blank">📋 JSON API</a>
        </div>
    </div>
    <div class="main">
        <div class="section">
            <div class="section-title">
                <span class="icon">📊</span> 今日 Skew 快照
            </div>
            <div class="cards">{summary_cards}</div>
        </div>

        <div class="section">
            <div class="section-title">
                <span class="icon">⚠</span> 今日预警状态
            </div>
            <div class="alert-box">{alert_table}</div>
        </div>

        <div class="section">
            <div class="section-title">
                <span class="icon">📈</span> Skew 历史走势（SPY / QQQ / IWM / DIA）
            </div>
            <div class="chart-box">{skew_chart}</div>
        </div>

        <div class="section">
            <div class="section-title">
                <span class="icon">📉</span> Z-Score 追踪（90日滚动窗口）
            </div>
            <div class="chart-box">{zscore_chart}</div>
        </div>

        <div class="section">
            <div class="section-title">
                <span class="icon">🔀</span> 跨标的 Skew 剪刀差
            </div>
            <div class="chart-box">{cross_chart}</div>
        </div>
        {macro_section}
        <div class="section">
            <div class="section-title">
                <span class="icon">🌡</span> 波动率状态 - VIX / VXN (v1.2.1)
            </div>
            <div class="cards">{volatility_cards}</div>
        </div>
    </div>
    <div class="footer">
        程序化盘后流动性与尾部风险监控引擎 &copy; 2024-2026 |
        数据来源: Polygon.io / FRED / Cboe |
        启动命令: <code>python -m src.main --serve</code>
    </div>
</body>
</html>"""


def _render_empty_state() -> str:
    """渲染空数据状态页面。"""
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
    <meta charset="UTF-8">
    <title>尾部风险监控看板 - 无数据</title>
    <style>
        body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, sans-serif; }}
        .empty-state {{
            display: flex; flex-direction: column; align-items: center;
            justify-content: center; height: 80vh; text-align: center;
        }}
        .empty-state h2 {{ color: #f0f6fc; margin-bottom: 12px; }}
        code {{
            background: #161b22; border: 1px solid #30363d;
            padding: 6px 14px; border-radius: 6px; font-size: 14px;
        }}
    </style>
</head>
<body>
    <div class="empty-state">
        <h2>📭 暂无风险监控数据</h2>
        <p style="color:#8b949e; margin-bottom: 20px">
            请先运行数据流水线以生成每日风险快照：
        </p>
        <code>python -m src.main</code>
        <p style="color:#8b949e; margin-top: 16px; font-size:13px">
            流水线完成后重新加载本页面即可查看 Skew 走势和预警信息。
        </p>
    </div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------


def start_dashboard(host: str = "0.0.0.0", port: int = 8080) -> None:
    """启动 Web 看板服务器。"""
    import uvicorn

    logger.info(f"Web 风险看板已启动: http://localhost:{port}")
    uvicorn.run(
        "src.presentation.web_dashboard:app",
        host=host,
        port=port,
        log_level="warning",
        reload=False,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="尾部风险监控 Web 看板")
    parser.add_argument("--port", type=int, default=8080, help="监听端口（默认 8080）")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    args = parser.parse_args()

    start_dashboard(host=args.host, port=args.port)
