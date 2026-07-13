#!/usr/bin/env bash
# V1.3 端到端 Docker 部署验证脚本（Bash 版）
# ----------------------------------------------------------------------------
# 适用场景: Linux / macOS / NAS (群晖/威联通) / WSL2 / 远程服务器
#
# 用法:
#   bash v13/scripts/e2e_docker_check.sh                    # 默认验证
#   bash v13/scripts/e2e_docker_check.sh --show-logs        # 显示最近日志
#   bash v13/scripts/e2e_docker_check.sh --skip-websocket   # 跳过 WS 检查
#   bash v13/scripts/e2e_docker_check.sh --wait 90          # 自定义等待秒数
#
# 退出码: 0 = 全部通过, 非零 = 有失败
# 前置: docker compose up -d --build 已执行
# ----------------------------------------------------------------------------

set -u

# ── 参数解析 ──────────────────────────────────────────────────────────

COMPOSE_DIR="v13"
WAIT_SECONDS=60
SKIP_WEBSOCKET=0
SHOW_LOGS=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --compose-dir)  COMPOSE_DIR="$2"; shift 2 ;;
        --wait)         WAIT_SECONDS="$2"; shift 2 ;;
        --skip-websocket) SKIP_WEBSOCKET=1; shift ;;
        --show-logs)    SHOW_LOGS=1; shift ;;
        -h|--help)
            grep "^# " "$0" | sed 's/^# //'
            exit 0
            ;;
        *)
            echo "未知参数: $1"; exit 1 ;;
    esac
done

# ── 工具函数 ──────────────────────────────────────────────────────────

FAILED_COUNT=0

color() {
    local code=$1; shift
    case "$code" in
        red)    printf '\033[31m%s\033[0m' "$*" ;;
        green)  printf '\033[32m%s\033[0m' "$*" ;;
        yellow) printf '\033[33m%s\033[0m' "$*" ;;
        cyan)   printf '\033[36m%s\033[0m' "$*" ;;
    esac
}

header() {
    echo ""
    color cyan "============================================================"
    color cyan "  $*"
    color cyan "============================================================"
}

ok()   { color green "  [OK]   $*"; }
fail() { color red   "  [FAIL] $*"; FAILED_COUNT=$((FAILED_COUNT+1)); }
warn() { color yellow "  [WARN] $*"; }

check_http() {
    local url=$1 name=$2
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url" 2>/dev/null || echo "000")
    if [[ "$code" == "200" ]]; then
        ok "$name -> $code"
    else
        fail "$name -> $code"
    fi
}

check_ws() {
    local url=$1
    python3 - <<PY 2>/dev/null
import asyncio, json, sys
try:
    import websockets
    async def main():
        async with websockets.connect("$url") as ws:
            await ws.send(json.dumps({"type": "hello"}))
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"[OK] WS 收到消息: {msg[:100]}")
            return 0
    sys.exit(asyncio.run(main()))
except Exception as e:
    print(f"[FAIL] WS 异常: {e}")
    sys.exit(1)
PY
    if [[ $? -eq 0 ]]; then
        ok "WebSocket $url 已连接"
    else
        fail "WebSocket $url 连接失败"
    fi
}

# ── 0. 前置检查 ──────────────────────────────────────────────────────

header "[0] 前置检查"

if ! command -v docker >/dev/null 2>&1; then
    fail "Docker CLI 未安装"
    exit 1
fi
ok "Docker CLI: $(docker --version)"

if ! docker info >/dev/null 2>&1; then
    fail "Docker daemon 未运行"
    exit 1
fi
ok "Docker daemon: 运行中"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$PROJECT_ROOT"
ok "项目根目录: $PROJECT_ROOT"

COMPOSE_FILE="$COMPOSE_DIR/docker-compose.yml"
if [[ ! -f "$COMPOSE_FILE" ]]; then
    fail "docker-compose.yml 未找到: $COMPOSE_FILE"
    exit 1
fi
ok "docker-compose.yml: 找到"

# ── 等待容器就绪 ─────────────────────────────────────────────────────

header "[1] 等待容器就绪（最多 ${WAIT_SECONDS}s）"
for i in $(seq 1 $WAIT_SECONDS); do
    UP_COUNT=$(docker ps --filter "name=quant-" --format "{{.Names}}" 2>/dev/null | wc -l)
    if [[ "$UP_COUNT" -ge 3 ]]; then
        ok "3 个核心容器已就绪（耗时 ${i}s）"
        break
    fi
    if [[ $((i % 10)) -eq 0 ]]; then
        echo "  等待中... ${i}/${WAIT_SECONDS}s (当前 up=$UP_COUNT)"
    fi
    sleep 1
done

# ── 2. 容器状态 ─────────────────────────────────────────────────────

header "[2] 容器状态"
for svc in quant-state-node quant-api-node quant-ui-node nginx; do
    if docker ps --filter "name=$svc" --format "{{.Names}}" 2>/dev/null | grep -q "$svc"; then
        STATUS=$(docker ps --filter "name=$svc" --format "{{.Status}}")
        ok "$svc: $STATUS"
    else
        fail "$svc: 未运行"
    fi
done

# ── 3. 健康检查 ─────────────────────────────────────────────────────

header "[3] 健康检查"
check_http "http://localhost:8080/health"    "Backend /health (direct)"
check_http "http://localhost/api/health"     "Backend /api/health (via Nginx)"
check_http "http://localhost/"                "Next.js root (via Nginx)"
check_http "http://localhost:3000/"           "Next.js root (direct)"

# ── 4. 后端 REST 端点 ───────────────────────────────────────────────

header "[4] 后端 REST 端点"
for ep in \
    "/api/v1/options/skew" \
    "/api/v1/macro/leverage" \
    "/api/v1/macro/series/M2" \
    "/api/v1/alerts/recent" \
    "/api/v1/alerts/stats" \
    "/api/v1/config" \
    "/api/v1/audit" \
    "/api/latest" \
    "/api/stats" \
    "/api/skipped"; do
    check_http "http://localhost$ep" "GET $ep"
done

# ── 5. WebSocket ────────────────────────────────────────────────────

if [[ $SKIP_WEBSOCKET -eq 0 ]]; then
    header "[5] WebSocket /ws/alerts"
    if command -v python3 >/dev/null && python3 -c "import websockets" 2>/dev/null; then
        check_ws "ws://localhost/ws/alerts"
    else
        warn "跳过 WebSocket 检查（需 python3 + websockets）"
    fi
else
    warn "WebSocket 检查已跳过"
fi

# ── 6. SQLite ───────────────────────────────────────────────────────

header "[6] SQLite 持久化"
if docker exec quant-api-node test -d /app/data 2>/dev/null; then
    ok "容器数据目录存在"
    if docker exec quant-api-node test -f /app/data/v13_state.db 2>/dev/null; then
        ok "SQLite 文件存在: /app/data/v13_state.db"
    else
        warn "SQLite 文件未生成（首次部署正常，需先触发 pipeline）"
    fi
else
    fail "容器内 /app/data 不存在"
fi

# ── 7. Redis ────────────────────────────────────────────────────────

header "[7] Redis 健康"
REDIS_PONG=$(docker exec quant-state-node redis-cli ping 2>&1 || echo "FAILED")
if [[ "$REDIS_PONG" == "PONG" ]]; then
    ok "Redis PING -> PONG"
else
    fail "Redis PING 异常: $REDIS_PONG"
fi

# ── 8. 调度器 ───────────────────────────────────────────────────────

header "[8] APScheduler 调度器"
SCHEDULER_LOG=$(docker logs quant-api-node --tail 100 2>&1 | grep -i "APScheduler" | head -1 || true)
if [[ -n "$SCHEDULER_LOG" ]]; then
    ok "调度器已启动: $SCHEDULER_LOG"
else
    warn "调度器未在日志中（QUANT_ENABLE_SCHEDULER 可能为 false）"
fi

# ── 9. 可选日志 ─────────────────────────────────────────────────────

if [[ $SHOW_LOGS -eq 1 ]]; then
    header "[9] 最近 30 行容器日志"
    for svc in quant-api-node quant-ui-node; do
        echo ""
        color yellow "  --- $svc ---"
        docker logs "$svc" --tail 30 2>&1 | sed 's/^/    /'
    done
fi

# ── 总结 ───────────────────────────────────────────────────────────

header "验证总结"
echo ""
if [[ $FAILED_COUNT -eq 0 ]]; then
    color green "  [OK] 全部检查通过！V1.3 部署健康。"
    echo ""
    echo "  下一步："
    echo "  1. 访问 http://localhost/ 查看 Next.js 控制台"
    echo "  2. 访问 http://localhost/api/docs 查看 Swagger"
    echo "  3. 等待 21:00 美东调度自动触发，或手动："
    echo "     docker exec quant-api-node py -3 -c 'from src.main import run_full_pipeline; run_full_pipeline()'"
    echo ""
    exit 0
else
    color red "  [FAIL] 有 $FAILED_COUNT 项检查未通过"
    echo ""
    echo "  排查建议："
    echo "  1. 查看容器日志: docker compose -f $COMPOSE_FILE logs --tail 100"
    echo "  2. 重启服务:    docker compose -f $COMPOSE_FILE restart"
    echo "  3. 完全重建:    docker compose -f $COMPOSE_FILE down -v && docker compose up -d --build"
    echo "  4. 重新跑脚本并加 --show-logs 参数"
    echo ""
    exit 1
fi