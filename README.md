# 程序化盘后流动性与尾部风险监控引擎 v1.2.1

自动化的本地数据管道，在每日美股盘后自动抓取、清洗并计算关键的宏观流动性指标与期权衍生品风险信号。通过程序化扫描核心宽基 ETF 的隐含波动率斜率及保证金债务动量，为量化策略和主观资产配置提供"尾部风险"预警信号。

本项目隶属于 **alphaear-logic-visualizer** 架构下的宏观与衍生品监控模块。

> **v1.2 更新**: PCHIP 单调样条插值、yfinance 备用数据源降级、VXN 独立接入、NYSE 交易日历、API Key 认证、宏观流动性 Web 面板、移动端响应式、完整测试覆盖。
> **v1.2.1 更新**: VXN 六维积分制自动化告警引擎（含分层状态机与冷却/升级机制）、QQQ 三因子尾部风险联合确认、全链路数据质量可见性（signal_quality / greeks_source / skipped ticker 展示）、BSM Delta 参数校验加固、VXN 管线鲁棒性增强（数据不可用标记、状态文件原子写入、触发原因持久化）。

---

## 系统架构

系统采用**解耦三层处理流水线**架构：

```
┌─────────────────────────────────────────────────────────┐
│                     数据接入层                           │
│  Polygon.io API  │  FRED API  │  Cboe VIX/VXN Futures  │
│  (EOD 期权链)      (M2 货币供应)  (期限结构)             │
│  yfinance 备用源  │  FINRA 自动爬取 (保证金债务 XLSX)   │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                     计算引擎层                           │
│  数据清洗 → Delta IV 插值 → Skew 计算 → Z-Score 预警    │
│  宏观杠杆分析 (Margin Debt / M2) → 动量反转检测          │
│  跨标的剪刀差统计检验 (滚动 Z-Score + 语义解释)          │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   展现与预警层                           │
│  Web 看板 (FastAPI+Plotly)  │  终端 Rich 输出  │  Webhook│
│  http://localhost:8080          │  彩色报告      │  推送   │
└─────────────────────────────────────────────────────────┘
```

### 三大核心功能模块

| 模块 | 监控标的 | 关键指标 | 频率 |
|------|---------|---------|------|
| **核心指数ETF期权暗流扫描** | SPY, QQQ, IWM, DIA | 25Δ IV Skew, 跨标的剪刀差, VIX期限结构 | 每日 |
| **VXN 自动化告警引擎** 🆕 v1.2.1 | VXN, VIX, QQQ | 六维积分制风险评分, QQQ 三因子尾部风险确认, 分层状态机 | 每日 |
| **宏观流动性与杠杆压力测试** | M2货币供应, FINRA保证金债务 | 杠杆占比(Ratio), MoM/YoY动量 | 每月 |
| **数据清洗与输出管道** | 以上全部 | 数据质量过滤, signal_quality/greeks_source 溯源, Web UI 看板 | 持续 |

---

## 环境要求

- Python 3.10+
- Poetry (推荐) 或 pip 用于依赖管理
- 以下 API 密钥（参见 [API 密钥申请指南](#api-密钥申请指南)）：
  - Polygon.io API Key (免费层可用)
  - FRED API Key (免费)
- 现代浏览器（Chrome / Firefox / Edge），用于访问本地 Web 看板

---

## 快速开始

### 1. 克隆项目并安装依赖

```bash
# 进入项目目录
cd "Programmatic After-Hours Liquidity and Tail Risk Monitoring Engine"

# 使用 pip 安装
pip install -e .

# 或使用 Poetry
poetry install
```

### 2. 配置环境变量

```bash
# 复制模板文件
copy .env.template .env

# 编辑 .env 文件，填入你的 API 密钥
```

### 3. 配置监控标的池（可选）

编辑 `config/tickers.yaml` 可以自定义监控标的列表和预警阈值。

### 4. 运行

```bash
# 运行完整 ETL 流水线（数据拉取 → 计算 → 报告）
python -m src.main

# 运行完整流水线后自动启动 Web 看板
python -m src.main --serve

# 仅启动 Web 看板（使用已有数据，不运行流水线）
python -m src.main --mode push-only

# 自定义 Web 看板端口
python -m src.main --serve --port 3000

# 仅拉取数据（不计算）
python -m src.main --mode fetch-only

# 仅计算（使用本地已保存的数据）
python -m src.main --mode calc-only

# 运行月度宏观流动性分析（自动爬取 FINRA 数据）
python -m src.main --macro

# 运行月度宏观流动性分析（手动指定 CSV）
python -m src.main --macro --margin-debt-csv path/to/finra_margin_debt.csv

# 以调度器模式运行（每日盘后自动执行）+ Web 看板
python -m src.scheduler --serve

# 以调度器模式运行（含自定义端口）
python -m src.scheduler --serve --port 3000
```

### 5. 访问 Web 看板

启动后打开浏览器访问 **http://localhost:8080**，即可查看：
- 📊 各标的 Skew 快照卡片（含数据源标记：PRIMARY / FALLBACK EST + BSM 🆕 v1.2.1）
- 🔬 VIX/VXN 波动率状态卡片 + VXN 告警引擎状态 🆕 v1.2.1
- ⚠ 实时预警状态表格
- 🚫 跳过标的清单（含跳过原因中文解释）🆕 v1.2.1
- 📈 SPY / QQQ / IWM / DIA 的 Skew 历史走势
- 📉 Z-Score 追踪图（含 ±2σ 预警线）
- 🔀 跨标的 Skew 剪刀差图表
- 📥 CSV 数据导出按钮
- 📡 JSON API 端点（/api/latest, /api/stats, /api/skipped, /api/vxn_alert）🆕 v1.2.1

---

## API 密钥申请指南

### Polygon.io

1. 访问 [https://polygon.io/](https://polygon.io/) 注册账户
2. 免费层提供 5 次 API 调用/分钟
3. 获取 API Key 后填入 `.env` 的 `POLYGON_API_KEY`

### FRED API

1. 访问 [https://fred.stlouisfed.org/docs/api/api_key.html](https://fred.stlouisfed.org/docs/api/api_key.html)
2. 填写表单申请免费 API Key
3. 填入 `.env` 的 `FRED_API_KEY`

---

## 项目结构

```
project-root/
├── config/
│   ├── __init__.py
│   ├── settings.py            # 全局配置模块
│   ├── tickers.yaml            # 标的池与阈值定义
│   └── risk_thresholds.yaml    # VXN 自动化告警阈值配置 🆕 v1.2.1
├── src/
│   ├── __init__.py
│   ├── main.py               # 主入口（ETL 流水线）
│   ├── scheduler.py          # 定时任务调度器
│   ├── data_ingestion/       # 数据接入层
│   │   ├── api_client.py     # HTTP 客户端基类（速率限制/重试）
│   │   ├── polygon_client.py # Polygon.io 期权链 API
│   │   ├── fred_client.py    # FRED M2 数据 API
│   │   ├── vix_client.py     # Cboe VIX/VXN 期货数据 🆕 VXN
│   │   ├── finra_scraper.py  # FINRA 保证金债务自动爬取 🆕
│   │   ├── fallback_source.py # yfinance 备用数据源 🆕 v1.2
│   │   ├── data_writer.py    # 本地 JSON/Parquet 存储
│   │   └── eod_fetcher.py    # 日终批量抓取编排（含降级）
│   ├── calculation/          # 计算引擎层
│   │   ├── data_cleaner.py   # 期权链数据清洗（VWMP + OI过滤）
│   │   ├── black_scholes.py  # BSM Delta 计算（含 NaN/Inf 参数校验）🆕 v1.2.1
│   │   ├── delta_interpolator.py  # 25Δ IV PCHIP/CubicSpline 插值 🆕
│   │   ├── skew_calculator.py     # Skew 与剪刀差计算
│   │   ├── volatility_regime.py   # VIX/VXN 波动率状态分析 🆕 v1.2.1
│   │   ├── vxn_alert_engine.py    # VXN 六维积分制自动化告警引擎 🆕 v1.2.1
│   │   ├── term_structure.py      # 波动率期限结构
│   │   ├── cross_asset_signals.py # 跨标的剪刀差统计检验 🆕
│   │   ├── macro_leverage.py      # 宏观杠杆分析
│   │   ├── risk_signals.py        # Z-Score 预警信号
│   │   └── master_aggregator.py   # 主数据帧汇总
│   └── presentation/        # 展现与预警层
│       ├── web_dashboard.py  # FastAPI + Plotly Web 看板 🆕 v1.2（认证+宏观面板+响应式）
│       ├── google_sheets.py  # Google Sheets 推送 (v1.0 遗留，可移除)
│       ├── webhook_pusher.py # Webhook 推送
│       ├── terminal_alerts.py # 终端 Rich 报告
│       └── logging_setup.py  # 日志配置
├── data/                    # 数据存储
│   ├── raw/                 # 原始 API 响应
│   │   └── finra/           # FINRA 自动爬取缓存 🆕
│   └── processed/           # 计算后数据
├── tests/                   # 测试（v1.2.1: 106 项测试, 覆盖 BSM/VIX/VXN/Skew）
│   ├── test_calculation/
│   │   ├── test_black_scholes.py        # BSM Delta 精度与边界测试 🆕 v1.2.1
│   │   ├── test_delta_interpolator.py   # Skew 插值单元测试 🆕
│   │   ├── test_risk_signals.py         # Z-Score 边界测试 🆕
│   │   ├── test_volatility_regime.py    # VIX/VXN 波动率状态分析测试 🆕 v1.2.1
│   │   └── test_vxn_alert_engine.py     # VXN 告警引擎完整测试 🆕 v1.2.1
│   ├── test_data_ingestion/
│   ├── test_integration/
│   │   └── test_pipeline.py            # 集成测试骨架 🆕
│   └── test_presentation/
├── .env.template            # 环境变量模板
├── pyproject.toml           # 项目依赖与配置
└── README.md
```

---

## 核心算法说明

### 25Δ IV Skew 计算

1. 从 Polygon.io 获取指定标的约30天到期的完整期权链快照（含 Greeks）
2. 剔除 Bid/Ask 价差异常和 IV 缺失的脏数据
3. 分别对 Put 和 Call 在 Delta-IV 平面上做 PCHIP 单调保形样条插值（v1.2 默认；可选 CubicSpline）
4. 在插值曲线上查询 25Δ 对应的隐含波动率
5. 计算：`Skew_spread = IV_Put_25Δ - IV_Call_25Δ`

### 预警触发逻辑

- **Skew Z-Score 预警**：当前 Skew 值超过过去90个交易日均值 +2σ 时触发
- **期限结构倒挂预警**：VIX 近月期货 > 次月期货时触发（Backwardation）
- **宏观杠杆预警**：杠杆占比 > 6% 且环比连续两月萎缩或同比转负

### 跨标的剪刀差

- `Skew_QQQ - Skew_SPY`：差值走阔预示科技巨头面临极端去杠杆压力

### VXN 自动化告警引擎 🆕 v1.2.1

六维积分制分层状态机，用于 QQQ 科技板块尾部风险识别：

| 维度 | 权重 | 触发条件 |
|------|------|---------|
| VXN Z-Score | 1 | Z ≥ 1.0 |
| VXN 滚动分位数 | 1 | ≥ 80% |
| VXN 5日动量 | 1 | ≥ 15% |
| VXN 绝对水平 | 1 | ≥ 35 |
| VXN-VIX 相对压力 | 2 | Spread Z ≥ 2.0 |
| QQQ 25Δ Skew 共振 | 2 | Skew Z ≥ 2.0 |

**告警层级**: `normal` (0) → `watch` (1) → `elevated` (2-3, 需两日确认) → `high` (4-5, 立即推送+24h 冷却) → `critical` (≥6 或 Z≥3/level≥45, 立即推送)

**QQQ 三因子联合确认**: QQQ Skew + VXN Z-Score + VXN-VIX Spread 三者中任意两项触发 → 升级为 `high`/`critical`

**状态管理**: `AlertStateManager` 提供升级突破冷却、24h 同等级去重、连续 3 日 normal 后解除通知、数据不可用标记、原子写入持久化

---

## 技术栈

| 层级 | 核心技术 |
|------|---------|
| 数据接入 | `httpx` (异步HTTP), `tenacity` (重试), `fredapi`, Polygon REST API, `yfinance` 备用源, FINRA 网页爬取 |
| 计算引擎 | `numpy`, `scipy` (PCHIP/CubicSpline 插值), `pandas` (时间序列), 滚动 Z-Score 统计检验 |
| 数据存储 | 本地 JSON (原始), Parquet/`pyarrow` (处理后), 数据溯源 (data_source 字段) |
| 展现预警 | `FastAPI` (Web 服务, API Key 认证), `Plotly` (交互式图表), `uvicorn` (ASGI), `rich` (终端), `loguru` (日志) |
| 任务调度 | `APScheduler` (Cron) + NYSE 交易日历 + last_run.json 状态持久化 |
| 开发质量 | `pytest`, `ruff`, `pre-commit` |

---

## 风险与注意事项

1. **Polygon.io 速率限制**：免费层 5次/分钟，本项目已内置令牌桶速率限制器
2. **期权链数据量**：单个标的单到期日可能有数百条记录，插值前已做 DTE 过滤
3. **插值边界处理**：当期权链中不存在精确 25Δ 合约时，样条插值需注意外推边界（已内置保护）
4. **FINRA 数据获取**：v1.1 已实现自动爬取，含月度缓存机制；若自动爬取失败，仍支持手动 CSV 传入
5. **Web 看板部署**：默认监听 `0.0.0.0:8080`，局域网内其他设备可直接访问；生产环境建议配合 nginx 反向代理
6. **跨标的统计检验可靠性**：Z-Score 需至少 10 个交易日的历史数据，初期积累不足时会自动降级为非预警

---

## 开发路线图

- [x] Phase 1 (Week 1): 多标的数据管道连通
- [x] Phase 2 (Week 2): 插值算法与矩阵对比
- [x] Phase 3 (Week 3): 自动化看板对接
- [x] Phase 4 (v1.1): Web UI 看板替代 Google Sheets + FINRA 自动爬取 + 跨标的统计检验
- [x] Phase 5 (v1.2): PCHIP 插值 + yfinance 备用源降级 + VXN 接入 + NYSE 交易日历 + Web UI 深化 + 测试覆盖
- [x] Phase 6 (v1.2.1): VXN 六维积分制自动化告警引擎 + QQQ 三因子尾部风险联合确认 + 全链路数据质量可见性 + BSM Delta 参数校验加固 + VXN 管线鲁棒性增强

### v1.2 变更摘要

| 缺口 | 修复方式 | 涉及文件 |
|:--|:--|:--|
| CubicSpline 对稀疏链不稳定 | 换用 PCHIP 单调保形样条插值 | `delta_interpolator.py` |
| DTE 窗口模糊、Bid/Ask 算术均值 | DTE 严格 [25,35] + VWMP 成交量加权中点 + OI>0 过滤 | `data_cleaner.py` |
| Polygon 单点故障无降级 | yfinance 备用源自动降级 + data_source 溯源字段 | `fallback_source.py`, `eod_fetcher.py` |
| VXN 死代码未接入 | 独立拉取 Cboe VXN 历史序列 + 期限结构方法 | `vix_client.py` |
| 无数据新鲜度校验 | `load_master_snapshot()` 检查最新日期是否在 3 天内 | `data_writer.py` |
| 节假日空跑污染历史数据 | NYSE 交易日历前置判断 + `last_run.json` 状态持久化 | `scheduler.py` |
| Web UI 无自动刷新/认证/宏观面板/移动端 | meta refresh + API Key 中间件 + 宏观流动性图表 + CSS 响应式 | `web_dashboard.py` |
| 核心逻辑无测试覆盖 | Skew 插值单元测试 + Z-Score 边界测试 + 集成测试骨架 | `tests/test_calculation/`, `tests/test_integration/` |

### v1.1 变更摘要

| 缺口 | 修复方式 | 涉及文件 |
|:--|:--|:--|
| VIX Term Structure 死代码 | 在 `run_full_pipeline()` 中调用 VIXClient | `main.py` |
| Google Sheets 替换为 Web UI | 新增 FastAPI + Plotly 看板，含 JSON API | `web_dashboard.py` |
| FINRA 手动传参 | 自动爬取 FINRA XLSX，含月度缓存 | `finra_scraper.py`, `main.py` |
| 跨标的剪刀差无统计检验 | 独立 Z-Score + 金融语义解释 | `cross_asset_signals.py` |
| `push-only` 模式缺失 | 补全 `--serve` / `--port` 参数 | `main.py`, `scheduler.py` |

### v1.2.1 变更摘要

| 缺口 | 修复方式 | 涉及文件 |
|:--|:--|:--|
| VXN 数据仅拉取未告警 | 六维积分制分层状态机 + AlertStateManager 冷却/升级/解除 | `vxn_alert_engine.py` |
| VXN 缺乏与 QQQ Skew 的联合研判 | QQQ 三因子尾部风险联合确认（Skew + VXN Z + VXN-VIX spread） | `volatility_regime.py`, `main.py` |
| 数据质量不可见（数据源/计算方式/跳过标的） | signal_quality / greeks_source 全链路透传 + skipped ticker 看板展示 | `skew_calculator.py`, `master_aggregator.py`, `web_dashboard.py` |
| BSM Delta 对 NaN/Inf 参数无校验 | rate / dividend_yield 有限值校验 + option_type 严格过滤 | `black_scholes.py` |
| VXN/VIX 拉取失败时静默跳过 | 构造 explicit `unavailable` 状态标记写入 snapshot | `main.py` |
| AlertStateManager 不持久化触发原因 | 新增 `last_reasons` 持久化 + `should_notify` 接收 reasons 参数 | `vxn_alert_engine.py` |
| 状态文件写入非原子 | `_save` 改为 temp + replace 原子写入 | `vxn_alert_engine.py` |

---

## 许可证

MIT License
