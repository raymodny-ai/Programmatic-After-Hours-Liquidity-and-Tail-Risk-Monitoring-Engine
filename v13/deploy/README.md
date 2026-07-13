# V1.3 部署指南

## 一键启动

```bash
cd v13
cp deploy/cron.env .env             # 填写 API Key
docker compose up -d --build
```

启动后访问：

| 入口 | 地址 |
|---|---|
| Next.js 控制台 | http://localhost (默认) |
| FastAPI Swagger | http://localhost/api/docs |
| WebSocket | ws://localhost/ws/alerts |
| Redis | localhost:6379 |

## 三节点拓扑

```
            ┌──────────────────────────┐
            │     Nginx :80            │
            │   (quant-nginx)          │
            └────────────┬─────────────┘
                         │
        ┌────────────────┼─────────────────────┐
        │                 │                     │
   /api/ & /ws/      / (静态)         /api/* (内部)
        │                 │                     │
        ▼                 ▼                     ▼
 quant-api-node   quant-ui-node ─────►quant-api-node
   (FastAPI)        (Next.js)             (FastAPI)
   8080             3000                  8080
                       │
                       ▼
                quant-state-node
                (Redis :6379)
```

## 服务详情

### quant-api-node (FastAPI Headless)

- 端口：8080
- 数据目录：`/app/data`（卷：`quant-data`）
- 环境变量前缀：`QUANT_*`
- 调度：21:00 美东 cron（可通过 `QUANT_PIPELINE_CRON_HOUR_ET` 自定义）
- 健康检查：`GET /health`

### quant-ui-node (Next.js)

- 端口：3000
- 5 个页面：HUD / 视图A / 视图B / 视图C / 终端
- 通过 `next.config.js` 的 rewrites 反代 `/api/*` 到后端
- 构建时注入：`NEXT_PUBLIC_API_BASE`

### quant-state-node (Redis)

- 端口：6379（默认映射到宿主机）
- 数据卷：`quant-redis-data`
- 用途：热缓存（最新 Skew / 宏观快照）+ WebSocket pub/sub

### Nginx 反代

- 端口：80
- `/` → `quant-ui-node:3000`
- `/api/*` → `quant-api-node:8080`
- `/ws/*` → `quant-api-node:8080`（带 Upgrade 头）

## 远程访问（NAS 场景）

1. 在路由器开放 80 端口外网访问
2. 部署 [ddns-go](https://github.com/jeessy2/ddns-go)，选择 Cloudflare / 阿里云
3. Next.js 通过 `https://your-domain.com` 访问
4. 详见 `deploy/ddns-go.env` 配置模板

## 验证清单

```bash
# 1. 容器状态
docker compose ps

# 2. 后端健康
curl http://localhost/api/health

# 3. 数据示例（首日缓存后）
curl http://localhost/api/v1/options/skew

# 4. WebSocket
wscat -c ws://localhost/ws/alerts

# 5. Next.js 控制台
curl -I http://localhost/
```

## 常见问题

### Q1. ThetaData 代理无法连接？

确保环境变量 `THETADATA_PROXY_URL` 正确。容器通过 `host.docker.internal:host-gateway` 访问宿主的 25510 端口。

### Q2. Redis 健康检查失败？

```bash
docker exec -it quant-state-node redis-cli ping
```

应返回 `PONG`。

### Q3. 时区不对？

容器已设为 UTC，调度任务使用 `zoneinfo.ZoneInfo("America/New_York")` 计算美东时间，与宿主机时区无关。

### Q4. SQLite 锁冲突？

SQLite WAL 模式下允许多读单写，容器重启不会丢数据。如出现锁：
```bash
docker exec -it quant-api-node rm -f /app/data/v13_state.db-wal /app/data/v13_state.db-shm
```

## 回滚到 v1.2.1

```bash
# 停止 v13 容器
cd v13 && docker compose down

# 恢复 v1.2.1（保留 data/processed 数据）
cd .. && python src/main.py
```

v1.2.1 与 V1.3 共享 `processed/` 目录下的 JSON 快照，可无缝回退。
