# V1.3 端到端 Docker 部署验证指南

> 本文档配套 [v13/scripts/e2e_docker_check.ps1](../scripts/e2e_docker_check.ps1)（Windows PowerShell）与 [v13/scripts/e2e_docker_check.sh](../scripts/e2e_docker_check.sh)（Linux/Bash/NAS）两个一键验证脚本。

## 一、脚本覆盖范围

| 步骤 | 检查项 | 失败影响 |
|---|---|---|
| [0] 前置检查 | Docker CLI / daemon / 项目目录 / compose 文件 | 立即终止 |
| [1] 容器就绪 | 等待 60 秒内 3 核心容器全部 Up | 性能与启动问题 |
| [2] 容器状态 | 4 服务全部运行（state/api/ui/nginx） | 部署链路中断 |
| [3] 健康检查 | `/health` `/api/health` `/` 端口全可达 | 反代或镜像异常 |
| [4] REST 端点 | 10 个关键 endpoint（含 4 个 v1.2.1 兼容）| 路由注册失败 |
| [5] WebSocket | `/ws/alerts` 建立 + 收到消息 | pub/sub 或 WS 升级异常 |
| [6] SQLite | 数据卷挂载 + db 文件存在 | 持久化失败 |
| [7] Redis | `redis-cli ping` 返回 PONG | 缓存失效 |
| [8] 调度器 | 启动日志含 `APScheduler 已启动` | cron 未启用 |

## 二、使用方法

### 2.1 Windows / PowerShell

```powershell
# 前置：启动 Docker Desktop，然后执行 docker compose up -d --build
cd "d:\Financial Project\Programmatic After-Hours Liquidity and Tail Risk Monitoring Engine"
cd v13
cp deploy/cron.env .env             # 填入 POLYGON_API_KEY / FRED_API_KEY
docker compose up -d --build

# 跑端到端验证（默认）
cd ..
pwsh -File v13/scripts/e2e_docker_check.ps1

# 加日志模式（看最近 30 行容器日志）
pwsh -File v13/scripts/e2e_docker_check.ps1 -ShowLogs

# 跳过 WebSocket 检查（无 python 时）
pwsh -File v13/scripts/e2e_docker_check.ps1 -SkipWebSocket
```

### 2.2 Linux / macOS / NAS / WSL2

```bash
# 前置：docker compose up -d --build
cd /path/to/project
cd v13 && cp deploy/cron.env .env && docker compose up -d --build
cd ..

# 跑端到端验证
bash v13/scripts/e2e_docker_check.sh

# 自定义等待秒数（默认 60s）
bash v13/scripts/e2e_docker_check.sh --wait 90

# 加日志模式
bash v13/scripts/e2e_docker_check.sh --show-logs
```

### 2.3 SSH 远程 NAS

```bash
ssh user@nas.local
cd /volume1/docker/quant-v13  # 或实际部署路径
docker compose up -d --build
bash v13/scripts/e2e_docker_check.sh
```

## 三、预期输出示例

```
============================================================
  [0] 前置检查
============================================================
  [OK]   Docker CLI: Docker version 24.0.7, build afdd53b
  [OK]   Docker daemon: 运行中
  [OK]   项目根目录: d:\Financial Project\...
  [OK]   docker-compose.yml: 找到 (v13\docker-compose.yml)

============================================================
  [1] 等待容器就绪（最多 60s）
============================================================
  [OK]   3 个核心容器已就绪（耗时 12s）

============================================================
  [2] 容器状态
============================================================
  [OK]   quant-state-node: Up 15 seconds (healthy)
  [OK]   quant-api-node:   Up 14 seconds (health: starting)
  [OK]   quant-ui-node:    Up 13 seconds (health: starting)
  [OK]   nginx:            Up 12 seconds

============================================================
  [3] 健康检查
============================================================
  [OK]   Backend /health (direct) -> 200
  [OK]   Backend /api/health (via Nginx) -> 200
  [OK]   Next.js root (via Nginx) -> 200
  [OK]   Next.js root (direct) -> 200

============================================================
  [4] 后端 REST 端点
============================================================
  [OK]   GET /api/v1/options/skew -> 200
  [OK]   GET /api/v1/macro/leverage -> 200
  ...（10 个全 OK）

============================================================
  [5] WebSocket /ws/alerts
============================================================
  [OK]   WS 收到消息: {"type":"ready","ts":...}

============================================================
  [6] SQLite 持久化
============================================================
  [OK]   容器数据目录存在
  [WARN] SQLite 文件未生成（首次部署正常，需先触发 pipeline）

============================================================
  [7] Redis 健康
============================================================
  [OK]   Redis PING -> PONG

============================================================
  [8] APScheduler 调度器
============================================================
  [OK]   调度器已启动: APScheduler 已启动: 美东 21:00 触发盘后流水线

============================================================
  验证总结
============================================================

  [OK] 全部检查通过！V1.3 部署健康。
```

## 四、常见失败排查

| 失败项 | 可能原因 | 排查命令 |
|---|---|---|
| Docker daemon 未运行 | Docker Desktop 未启动 | 系统托盘启动 Docker Desktop |
| 容器未就绪 | 镜像构建中 / 端口冲突 | `docker compose logs --tail 50` |
| Backend /health 异常 | uvicorn 启动失败 | `docker logs quant-api-node --tail 100` |
| Nginx 反代 502 | api-node 健康检查中 | 等 30s 后重试，或 `docker restart quant-nginx` |
| WebSocket 连接失败 | Nginx Upgrade 头未传 | 检查 `deploy/nginx.conf` 的 `/ws/` location |
| Redis PING 异常 | 容器内部 OOM / 网络 | `docker inspect quant-state-node` |
| 调度器未启动 | `QUANT_ENABLE_SCHEDULER=false` | 检查 `cron.env` 设置 |

## 五、首次部署后的下一步动作

```powershell
# 1. 手动触发一次 pipeline（生成 SQLite / 缓存数据）
docker exec quant-api-node py -3 -c "from src.main import run_full_pipeline; run_full_pipeline()"

# 2. 等待 10 秒后重跑验证，确认 SQLite 已生成
pwsh -File v13/scripts/e2e_docker_check.ps1
# 预期 [6] SQLite 不再 WARN

# 3. 访问 Next.js 控制台验证 UI
start http://localhost/

# 4. 远程访问（部署 ddns-go 后）
start https://quant.your-domain.com/
```

## 六、脚本返回码契约

| 返回码 | 含义 | CI 集成 |
|---|---|---|
| 0 | 全部 8 项检查通过 | ✅ 可发布 |
| 1 | 前置检查失败（无 Docker / 目录错误） | ❌ 终止 |
| ≥1 | 有失败项（见末尾总结） | ❌ 修复后重跑 |

## 七、CI/CD 集成示例（GitHub Actions）

```yaml
name: V1.3 E2E
on: [push]
jobs:
  docker-e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build & Up
        run: |
          cd v13
          cp deploy/cron.env .env
          docker compose up -d --build
      - name: E2E Verify
        run: bash v13/scripts/e2e_docker_check.sh --wait 90
      - name: Show logs on fail
        if: failure()
        run: docker compose -f v13/docker-compose.yml logs --tail 200
      - name: Cleanup
        if: always()
        run: docker compose -f v13/docker-compose.yml down -v
```

---

**脚本路径**：
- Windows: `v13/scripts/e2e_docker_check.ps1`
- Linux/NAS: `v13/scripts/e2e_docker_check.sh`