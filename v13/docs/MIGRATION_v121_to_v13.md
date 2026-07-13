# v1.2.1 → V1.3 迁移指南

## TL;DR

```bash
# 1. 备份 v1.2.1
cp -r data /backup/data_v121_$(date +%Y%m%d)

# 2. 兼容性验证（必须全部通过）
python v13/scripts/v121_compat_check.py

# 3. 启动 V1.3（同时保留 v1.2.1 入口）
cd v13 && docker compose up -d --build

# 4. 验证（3 个端点）
curl http://localhost/api/health
curl http://localhost/api/v1/options/skew
curl http://localhost/api/latest
```

## 一、变更概览

| 维度 | v1.2.1 | V1.3 |
|---|---|---|
| 架构 | 单体 CLI | 三节点微服务（API / UI / State） |
| 持久化 | 仅 JSON 快照 | SQLite + JSON + Redis 三层 |
| 调度 | `cron` 调 Python | APScheduler 内嵌 + 容器化 |
| API | 无（CLI） | REST + WebSocket |
| 前端 | Streamlit (可选) | Next.js 14 (强制) |
| 部署 | 手动 / cron | Docker Compose + Nginx |
| 远程访问 | 直连 IP | ddns-go + HTTPS |

## 二、文件路径变化

| v1.2.1 | V1.3 | 说明 |
|---|---|---|
| `data/processed/latest_snapshot.json` | 同 | **保持原路径**，向下兼容 |
| `data/processed/macro_history.json` | 同 | 同上 |
| `data/processed/volatility_regime_snapshot.json` | 同 | 同上 |
| `data/processed/skipped_tickers_snapshot.json` | 同 | 同上 |
| `config/risk_config.yaml` | 同 | 同上 |
| （无） | `data/v13_state.db` | SQLite 新增（macro_series / risk_config / audit_log / skew_history / alert_log） |
| （无） | Redis :6379 | 热缓存（最新快照 + pub/sub） |
| `src/main.py` | 同 | 仍可独立运行（V1.3 后端通过 `run_full_pipeline` 调用） |

## 三、API 端点映射

### v1.2.1 旧路径（V1.3 通过兼容层保留）

| v1.2.1 等价物 | V1.3 兼容路径 | V1.3 推荐路径 |
|---|---|---|
| `latest_snapshot.json` 文件 | `GET /api/latest` | `GET /api/v1/options/skew?as_of=...` |
| 统计手工统计 | `GET /api/stats` | `GET /api/v1/alerts/stats` |
| 跳过 ticker 列表 | `GET /api/skipped` | `GET /api/v1/alerts/recent` |
| VXN 告警手工触发 | `POST /api/vxn_alert` | `POST /api/v1/pipeline/run` |

V1.3 提供 4 个兼容路径（开关 `QUANT_ENABLE_V121_LEGACY_ENDPOINTS=true`），
旧脚本可直接切换到 V1.3 而无需修改。

### V1.3 新增端点

| 端点 | 方法 | 功能 |
|---|---|---|
| `/health` | GET | 健康检查（API/SQLite/Redis/Scheduler） |
| `/api/v1/options/skew` | GET | Skew 25d 矩阵（as_of 参数） |
| `/api/v1/options/surface` | GET | 波动率曲面（ticker 参数） |
| `/api/v1/macro/series` | GET | M2 / Margin / Ratio 月度序列 |
| `/api/v1/macro/leverage` | GET | 杠杆率 + YoY + 3m 动量 + 反转信号 |
| `/api/v1/alerts/recent` | GET | 最近告警（severity_min 过滤） |
| `/api/v1/alerts/stats` | GET | 告警统计 |
| `/api/v1/config` | GET/PUT | 风险配置 CRUD |
| `/api/v1/config/{name}` | GET/PUT | 单个配置 |
| `/api/v1/audit` | GET | 审计日志 |
| `/api/v1/pipeline/run` | POST | 手动触发管线 |
| `/ws/alerts` | WS | 实时告警推送 |

## 四、配置变更

### 环境变量（新增 `QUANT_*` 前缀）

```bash
# v1.2.1 已有
export POLYGON_API_KEY=xxx
export FRED_API_KEY=xxx

# V1.3 新增
export QUANT_REDIS_HOST=localhost
export QUANT_REDIS_PORT=6379
export QUANT_SQLITE_PATH=./data/v13_state.db
export QUANT_SNAPSHOT_DIR=./data/processed
export QUANT_PIPELINE_CRON_HOUR_ET=21
export QUANT_ENABLE_V121_LEGACY_ENDPOINTS=true
export QUANT_API_PORT=8080
export QUANT_ENABLE_SCHEDULER=true
```

### 配置文件 `config/risk_config.yaml`

完全向后兼容。新增可选字段：

```yaml
# v1.2.1 原字段（保持）
tickers: [SPY, QQQ, IWM, VXN, ...]
z_alert: 1.5
...

# V1.3 新增（可选）
vxn_thresholds:
  z_alert: 1.5
  z_critical: 2.5
leverage:
  yoy_alert_pct: 0.10
  reversal_alert: true
```

## 五、数据迁移

### 自动迁移（推荐）

V1.3 首次启动时，会自动：
1. 读取 `data/processed/*.json` 加载到 SQLite
2. Redis 缓存最新一次快照
3. APScheduler 在美东 21:00 自动跑管线

无需手动操作。

### 手动迁移（仅在自动失败时）

```python
from v13.quant_state_node.persistence import SqliteStore, SnapshotCompat

store = SqliteStore("./data/v13_state.db")
sc = SnapshotCompat("./data/processed")

# 1. 加载 latest_snapshot
import json
snap = json.loads(sc.read_latest_snapshot() or "{}")
for ticker, data in snap.get("snapshots", {}).items():
    store.upsert_skew_snapshot(ticker, snap["updated_at"], data)

# 2. 加载 macro_history
hist = json.loads(sc.read_macro_history() or "{}")
for entry in hist.get("m2", []):
    store.upsert_macro("M2", entry["date"], entry["value"])
for entry in hist.get("margin", []):
    store.upsert_macro("FINRA_MARGIN", entry["date"], entry["value"])

print("迁移完成")
```

## 六、回滚

如需退回 v1.2.1：

```bash
# 1. 停止 V1.3
cd v13 && docker compose down

# 2. 激活 v1.2.1 虚拟环境
source .venv/bin/activate

# 3. 跑 v1.2.1 主入口（与 V1.3 共享 processed/ 数据）
python src/main.py

# 4. 验证
cat data/processed/latest_snapshot.json | jq '.updated_at'
```

**无需任何数据迁移**——v1.2.1 与 V1.3 完全双向兼容 `data/processed/`。

## 七、验证清单

- [ ] `python v13/scripts/v121_compat_check.py` 全部 ✓
- [ ] `curl http://localhost/api/health` 返回 `ok: true`
- [ ] `curl http://localhost/api/v1/options/skew` 返回 VXN/SPY 数据
- [ ] `curl http://localhost/api/latest` 返回 v1.2.1 等价 JSON
- [ ] Next.js 控制台 http://localhost/ 加载正常
- [ ] WebSocket `wscat -c ws://localhost/ws/alerts` 可连接
- [ ] 21:00 美东 cron 自动触发管线（可临时改时区测试）
- [ ] ddns-go 远程访问 HTTPS 可达

## 八、常见问题

### Q1. V1.3 启动失败：`ModuleNotFoundError: No module named 'src'`

确保在项目根目录运行：

```bash
cd /path/to/Programmatic After-Hours Liquidity and Tail Risk Monitoring Engine
PYTHONPATH=. python -m uvicorn v13.quant_api_node.app.main:app
```

### Q2. 旧脚本找不到配置文件？

V1.3 仍在原路径加载 `config/risk_config.yaml`，无需修改脚本中的相对路径。

### Q3. SQLite 数据库锁？

WAL 模式下允许多读单写。如需强制解锁：

```bash
sqlite3 data/v13_state.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

### Q4. Redis 不可用？

V1.3 设计为 Redis 优雅降级：所有操作失败时返回 safe-default（`None`/`False`），
不影响主流程运行。日志会标记 `redis_cache: unavailable`。

### Q5. WebSocket 频繁断开？

Nginx 默认 `proxy_read_timeout 60s` 会切断空闲 WS。V1.3 已设为 86400s，
如自建 Nginx 请同步修改。客户端侧 25 秒心跳保活（`{"type": "ping"}`）。