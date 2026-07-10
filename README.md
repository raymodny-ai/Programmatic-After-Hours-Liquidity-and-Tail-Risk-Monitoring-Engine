# 程序化盘后流动性与尾部风险监控引擎

自动化的本地数据管道，在每日美股盘后自动抓取、清洗并计算关键的宏观流动性指标与期权衍生品风险信号。通过程序化扫描核心宽基 ETF 的隐含波动率斜率及保证金债务动量，为量化策略和主观资产配置提供"尾部风险"预警信号。

本项目隶属于 **alphaear-logic-visualizer** 架构下的宏观与衍生品监控模块。

---

## 系统架构

系统采用**解耦三层处理流水线**架构：

```
┌─────────────────────────────────────────────────────────┐
│                     数据接入层                           │
│  Polygon.io API  │  FRED API  │  Cboe VIX Futures      │
│  (EOD 期权链)      (M2 货币供应)  (期限结构)             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                     计算引擎层                           │
│  数据清洗 → Delta IV 插值 → Skew 计算 → Z-Score 预警    │
│  宏观杠杆分析 (Margin Debt / M2) → 动量反转检测          │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                   展现与预警层                           │
│  Google Sheets 看板  │  终端 Rich 输出  │  Webhook 推送  │
└─────────────────────────────────────────────────────────┘
```

### 三大核心功能模块

| 模块 | 监控标的 | 关键指标 | 频率 |
|------|---------|---------|------|
| **核心指数ETF期权暗流扫描** | SPY, QQQ, IWM, DIA | 25Δ IV Skew, 跨标的剪刀差, VIX期限结构 | 每日 |
| **宏观流动性与杠杆压力测试** | M2货币供应, FINRA保证金债务 | 杠杆占比(Ratio), MoM/YoY动量 | 每月 |
| **数据清洗与输出管道** | 以上全部 | 数据质量过滤, Google Sheets自动推送 | 持续 |

---

## 环境要求

- Python 3.10+
- Poetry (推荐) 或 pip 用于依赖管理
- 以下 API 密钥（参见 [API 密钥申请指南](#api-密钥申请指南)）：
  - Polygon.io API Key (免费层可用)
  - FRED API Key (免费)
  - Google Cloud Service Account (免费，用于 Google Sheets 推送)

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

# 仅拉取数据（不计算）
python -m src.main --mode fetch-only

# 仅计算（使用本地已保存的数据）
python -m src.main --mode calc-only

# 运行月度宏观流动性分析
python -m src.main --macro --margin-debt-csv path/to/finra_margin_debt.csv

# 以调度器模式运行（每日盘后自动执行）
python -m src.scheduler
```

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

### Google Sheets API

1. 访问 [Google Cloud Console](https://console.cloud.google.com/)
2. 创建新项目或选择现有项目
3. 启用 **Google Sheets API** 和 **Google Drive API**
4. 创建 Service Account：
   - APIs & Services → Credentials → Create Credentials → Service Account
   - 下载 JSON 凭证文件，保存到 `credentials/google_service_account.json`
5. 将 Service Account 的 email 添加为你的 Google Sheets 的编辑者
6. 填入 `.env` 的 `GOOGLE_SPREADSHEET_ID`（从 Sheets URL 中提取）

---

## 项目结构

```
project-root/
├── config/
│   ├── __init__.py
│   ├── settings.py          # 全局配置模块
│   └── tickers.yaml          # 标的池与阈值定义
├── src/
│   ├── __init__.py
│   ├── main.py               # 主入口（ETL 流水线）
│   ├── scheduler.py          # 定时任务调度器
│   ├── data_ingestion/       # 数据接入层
│   │   ├── api_client.py     # HTTP 客户端基类（速率限制/重试）
│   │   ├── polygon_client.py # Polygon.io 期权链 API
│   │   ├── fred_client.py    # FRED M2 数据 API
│   │   ├── vix_client.py     # Cboe VIX 期货数据
│   │   ├── data_writer.py    # 本地 JSON/Parquet 存储
│   │   └── eod_fetcher.py    # 日终批量抓取编排
│   ├── calculation/          # 计算引擎层
│   │   ├── data_cleaner.py   # 期权链数据清洗
│   │   ├── delta_interpolator.py  # 25Δ IV 样条插值
│   │   ├── skew_calculator.py     # Skew 与剪刀差计算
│   │   ├── term_structure.py      # 波动率期限结构
│   │   ├── macro_leverage.py      # 宏观杠杆分析
│   │   ├── risk_signals.py        # Z-Score 预警信号
│   │   └── master_aggregator.py   # 主数据帧汇总
│   └── presentation/        # 展现与预警层
│       ├── google_sheets.py  # Google Sheets 推送
│       ├── webhook_pusher.py # Webhook 推送
│       ├── terminal_alerts.py # 终端 Rich 报告
│       └── logging_setup.py  # 日志配置
├── data/                    # 数据存储
│   ├── raw/                 # 原始 API 响应
│   └── processed/           # 计算后数据
├── tests/                   # 测试
│   ├── test_data_ingestion/
│   ├── test_calculation/
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
3. 分别对 Put 和 Call 在 Delta-IV 平面上做三次样条插值
4. 在插值曲线上查询 25Δ 对应的隐含波动率
5. 计算：`Skew_spread = IV_Put_25Δ - IV_Call_25Δ`

### 预警触发逻辑

- **Skew Z-Score 预警**：当前 Skew 值超过过去90个交易日均值 +2σ 时触发
- **期限结构倒挂预警**：VIX 近月期货 > 次月期货时触发（Backwardation）
- **宏观杠杆预警**：杠杆占比 > 6% 且环比连续两月萎缩或同比转负

### 跨标的剪刀差

- `Skew_QQQ - Skew_SPY`：差值走阔预示科技巨头面临极端去杠杆压力

---

## 技术栈

| 层级 | 核心技术 |
|------|---------|
| 数据接入 | `httpx` (异步HTTP), `tenacity` (重试), `fredapi`, Polygon REST API |
| 计算引擎 | `numpy`, `scipy` (样条插值), `pandas` (时间序列) |
| 数据存储 | 本地 JSON (原始), Parquet/`pyarrow` (处理后) |
| 展现预警 | `gspread` (Google Sheets), `rich` (终端), `loguru` (日志) |
| 任务调度 | `APScheduler` (Cron) |
| 开发质量 | `pytest`, `ruff`, `pre-commit` |

---

## 风险与注意事项

1. **Polygon.io 速率限制**：免费层 5次/分钟，本项目已内置令牌桶速率限制器
2. **期权链数据量**：单个标的单到期日可能有数百条记录，插值前已做 DTE 过滤
3. **插值边界处理**：当期权链中不存在精确 25Δ 合约时，样条插值需注意外推边界（已内置保护）
4. **FINRA 数据获取**：FINRA 保证金债务无公开 API，需要每月手动下载 CSV 或通过网页抓取
5. **Google Sheets 配额**：免费层 60次读+60次写/分钟，本项目每日仅需少量写入

---

## 开发路线图

- [x] Phase 1 (Week 1): 多标的数据管道连通
- [x] Phase 2 (Week 2): 插值算法与矩阵对比
- [x] Phase 3 (Week 3): 自动化看板对接

---

## 许可证

MIT License
