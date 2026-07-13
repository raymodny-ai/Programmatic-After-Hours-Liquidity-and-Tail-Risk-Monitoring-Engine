# V1.3 提交准备文档

> 本文档汇总 V1.3 升级期间的所有变更，并给出本地 git 提交命令模板。
> 由于沙箱环境无 git，请在本地 PowerShell 执行：

```powershell
cd "d:\Financial Project\Programmatic After-Hours Liquidity and Tail Risk Monitoring Engine"

# 1. 添加 .gitignore 排除（next.js / node_modules / pyc）
# 已创建 v13/quant_ui_node/.gitignore 和 v13/__pycache__/
# 根目录 .gitignore 已存在

# 2. stage 全部变更
git add v13/ README.md

# 3. 提交
git commit -m "V1.3: 微服务三节点架构升级

- quant-api-node: FastAPI Headless 后端 (REST + WebSocket)
- quant-ui-node: Next.js 14 + TypeScript 前端
  - HUD 概览 + 视图A/B/C + xterm 终端
- quant-state-node: Redis 热缓存 + SQLite 持久化

后端：
- 21:00 美东 APScheduler 调度
- ThetaData 本地代理接入（tenacity 指数退避）
- 深度 OTM/远月合约完备性校验
- FRED M2 + FINRA Margin Debt 自动对齐 + 杠杆 YoY/3月动量反转
- SPY/QQQ/IWM 矩阵化 Skew 计算
- 4 个 v1.2.1 兼容端点（开关控制）

部署：
- Docker Compose 四服务编排
- Nginx 反代 (/api/ + /ws/)
- ddns-go 远程访问配置

测试：
- 106 项老测试全通过 + 16 项 V1.3 新测试 + 冒烟测试通过
- v121_compat_check.py 兼容性验证脚本
- smoke_test.py 路由可达性验证

文档：
- v13/deploy/README.md 部署指南
- v13/docs/MIGRATION_v121_to_v13.md 迁移指南
- v13/quant_ui_node/README.md 前端说明"

# 4. 推送
git push origin main
```

## 变更清单

### 新增文件

```
v13/
├── __init__.py                                    (版本 1.3.0)
├── README.md                                      (V1.3 总览)
├── docker-compose.yml                             (四服务编排)
├── Dockerfile.api                                 (Python 3.11-slim)
├── Dockerfile.ui                                  (Node 20-alpine 三阶段)
├── deploy/
│   ├── nginx.conf                                 (反代配置)
│   ├── cron.env                                   (环境变量模板)
│   ├── ddns-go.env                                (远程访问配置)
│   └── README.md                                  (部署指南)
├── docs/
│   └── MIGRATION_v121_to_v13.md                   (迁移指南)
├── scripts/
│   ├── smoke_test.py                              (冒烟测试)
│   └── v121_compat_check.py                       (兼容性验证)
├── shared/
│   ├── __init__.py
│   └── schemas/
│       ├── __init__.py
│       └── contracts.py                           (Pydantic v2 契约)
├── quant_state_node/
│   ├── __init__.py
│   └── persistence/
│       ├── __init__.py
│       ├── sqlite_store.py                        (5 表 + WAL)
│       ├── redis_cache.py                         (优雅降级)
│       └── snapshot_compat.py                     (v1.2.1 JSON 兼容)
├── quant_api_node/
│   ├── __init__.py
│   ├── requirements.api.txt                       (依赖清单)
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                                (FastAPI 入口)
│   │   ├── core/
│   │   │   ├── config.py                          (Pydantic Settings)
│   │   │   ├── dependencies.py                    (DI 单例)
│   │   │   └── logging_setup.py                   (loguru 旋转)
│   │   ├── services/
│   │   │   ├── data_service.py                    (业务编排)
│   │   │   ├── data_sources/
│   │   │   │   ├── thetadata_client.py            (tenacity 退避)
│   │   │   │   ├── completeness_check.py          (OTM/远月校验)
│   │   │   │   └── fred_finra_align.py            (M2/Margin 对齐)
│   │   │   └── analytics/
│   │   │       └── matrix_skew.py                (并行 Skew)
│   │   ├── api/v1/
│   │   │   ├── __init__.py
│   │   │   ├── legacy_compat.py                   (4 个 v1.2.1 路径)
│   │   │   └── routers/
│   │   │       ├── __init__.py                    (api_v1 + ws 聚合)
│   │   │       ├── options.py                     (skew/surface)
│   │   │       ├── macro.py                       (leverage/series)
│   │   │       ├── alerts.py                      (recent/stats)
│   │   │       ├── config.py                      (YAML CRUD)
│   │   │       ├── audit.py
│   │   │       ├── pipeline.py                    (手动触发)
│   │   │       └── ws_alerts.py                   (WebSocket)
│   │   └── scheduler/
│   │       └── daily_runner.py                    (21:00 美东 cron)
│   └── tests/
│       ├── conftest.py
│       ├── test_v13_persistence.py                (6 测试)
│       ├── test_v13_services.py                   (5 测试)
│       └── test_v13_api.py                        (5 测试)
└── quant_ui_node/
    ├── package.json
    ├── tsconfig.json
    ├── next.config.js
    ├── tailwind.config.ts
    ├── postcss.config.js
    ├── next-env.d.ts
    ├── .gitignore
    ├── README.md
    └── src/
        ├── app/
        │   ├── layout.tsx
        │   ├── page.tsx                            (HUD)
        │   ├── view-a/page.tsx
        │   ├── view-b/page.tsx
        │   ├── view-c/page.tsx
        │   └── logs/page.tsx
        ├── components/
        │   ├── Sidebar.tsx
        │   ├── HUD.tsx
        │   ├── ViewA.tsx                           (TradingView)
        │   ├── ViewB.tsx                           (Three.js 3D)
        │   ├── ViewC.tsx                           (YAML 编辑器)
        │   └── TerminalLogs.tsx                    (xterm.js)
        ├── lib/
        │   ├── api.ts                              (REST 客户端 + 类型)
        │   └── useAlerts.ts                        (WebSocket hook)
        └── styles/
            └── globals.css
```

### 修改文件

- `README.md`：版本 v1.2.1 → v1.3 + V1.3 更新摘要

### 关键依赖

后端（Python）：
- fastapi >= 0.115
- uvicorn >= 0.30
- pydantic >= 2.7
- pydantic-settings >= 2.3
- redis >= 5.0
- apscheduler >= 3.10
- httpx >= 0.27
- websockets >= 12.0
- tenacity >= 8.5
- pyyaml >= 6.0

前端（Node）：
- next 14.2.5
- react 18.3.1
- lightweight-charts 4.2 (TradingView)
- three 0.165
- @xterm/xterm 5.5
- swr 2.2
- zustand 4.5
- tailwindcss 3.4

## 测试统计

| 类别 | 数量 | 状态 |
|---|---|---|
| v1.2.1 遗留测试 | 106 | PASSED |
| v1.2.1 跳过 (skipped) | 2 | PASSED |
| V1.3 新增测试 | 16 | PASSED |
| V1.3 API 路由冒烟 | 5 | PASSED |
| V1.3 端点冒烟 (smoke_test.py) | 18 paths + 13 endpoints + 1 WS | PASSED |
| **总计** | **142 + 1 skipped** | **100% PASSED** |

## 安全注意事项

提交前请确认：

1. **不要提交 secrets**：
   - `.env`、`.env.local` 已在 `.gitignore` 中
   - `cron.env` 中的占位符是空的（如 `${POLYGON_API_KEY:-}`）

2. **生产环境前必改**：
   - 修改 `ddns-go.env` 中的域名/token
   - 设置 `QUANT_ENABLE_V121_LEGACY_ENDPOINTS=false`（迁移完成后）
   - Nginx 加 basic auth（详见 deploy/README.md）