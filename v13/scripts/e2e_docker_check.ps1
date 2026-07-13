"""V1.3 端到端 Docker 部署验证脚本（PowerShell 版）。

使用场景:
    - Windows 10/11 + Docker Desktop
    - WSL2 + Docker Engine
    - NAS 通过 SSH 远程（需先 ssh 登录后跑）

执行:
    pwsh -File v13/scripts/e2e_docker_check.ps1
    或：
    .\v13\scripts\e2e_docker_check.ps1

退出码:
    0 = 全部通过
    非零 = 有失败项

前置依赖:
    - Docker Desktop 已安装并运行
    - 已执行过 docker compose up -d --build（容器已启动）
"""

[CmdletBinding()]
param(
    [string]$ComposeDir = "v13",
    [int]$WaitSeconds = 60,
    [switch]$SkipWebSocket,
    [switch]$ShowLogs
)

$ErrorActionPreference = "Continue"

# ── 工具函数 ──────────────────────────────────────────────────────────

function Write-Header([string]$Text) {
    Write-Host ""
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "  $Text" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
}

function Write-OK([string]$Text) {
    Write-Host "  [OK]   $Text" -ForegroundColor Green
}

function Write-Fail([string]$Text) {
    Write-Host "  [FAIL] $Text" -ForegroundColor Red
    $script:FailedCount++
}

function Write-Warn([string]$Text) {
    Write-Host "  [WARN] $Text" -ForegroundColor Yellow
}

$script:FailedCount = 0

# ── 0. 前置检查 ────────────────────────────────────────────────────────

Write-Header "[0] 前置检查"

# Docker CLI
try {
    $dockerVersion = docker --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Docker CLI: $dockerVersion"
    } else {
        Write-Fail "Docker CLI 未安装或不可用"
        exit 1
    }
} catch {
    Write-Fail "Docker CLI 未安装: $_"
    exit 1
}

# Docker daemon
try {
    $dockerInfo = docker info 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $dockerInfo -match "Server Version") {
        Write-OK "Docker daemon: 运行中"
    } else {
        Write-Fail "Docker daemon 未运行（请启动 Docker Desktop）"
        exit 1
    }
} catch {
    Write-Fail "Docker daemon 不可访问: $_"
    exit 1
}

# 工作目录
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Resolve-Path (Join-Path $ScriptRoot "..\..")
Set-Location $ProjectRoot
Write-OK "项目根目录: $ProjectRoot"

# docker-compose.yml 存在
$ComposeFile = Join-Path $ComposeDir "docker-compose.yml"
if (Test-Path $ComposeFile) {
    Write-OK "docker-compose.yml: 找到 ($ComposeFile)"
} else {
    Write-Fail "docker-compose.yml 未找到: $ComposeFile"
    exit 1
}

# ── 1. 容器状态 ────────────────────────────────────────────────────────

Write-Header "[1] 容器状态（docker compose ps）"

$ComposeArgs = @("-f", $ComposeFile, "ps", "--format", "json")
$PsOutput = & docker compose @ComposeArgs 2>&1 | Out-String

$ExpectedServices = @("quant-state-node", "quant-api-node", "quant-ui-node", "nginx")

foreach ($svc in $ExpectedServices) {
    $SvcInfo = docker ps --filter "name=$svc" --format "{{.Names}} {{.Status}}" 2>&1
    if ($LASTEXITCODE -eq 0 -and $SvcInfo -match "$svc.*Up") {
        Write-OK "$svc: $($SvcInfo -split "`n" | Where-Object { $_ -match $svc })"
    } else {
        Write-Fail "$svc: 未运行或异常"
    }
}

# ── 2. API 健康检查 ────────────────────────────────────────────────────

Write-Header "[2] API 健康检查"

# 后端健康（直接访问容器内端口）
$HealthChecks = @(
    @{ Url = "http://localhost:8080/health";           Name = "Backend /health (direct)" },
    @{ Url = "http://localhost/api/health";            Name = "Backend /api/health (via Nginx)" },
    @{ Url = "http://localhost/";                       Name = "Next.js root (via Nginx)" },
    @{ Url = "http://localhost:3000/";                  Name = "Next.js root (direct)" }
)

foreach ($check in $HealthChecks) {
    try {
        $r = Invoke-WebRequest -Uri $check.Url -Method Get -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-OK "$($check.Name) -> $($r.StatusCode)"
        } else {
            Write-Fail "$($check.Name) -> $($r.StatusCode)"
        }
    } catch {
        Write-Fail "$($check.Name) -> 异常: $($_.Exception.Message)"
    }
}

# ── 3. 后端关键 REST 端点 ──────────────────────────────────────────────

Write-Header "[3] 后端 REST 端点（通过 Nginx /api 反代）"

$Endpoints = @(
    "/api/v1/options/skew",
    "/api/v1/macro/leverage",
    "/api/v1/macro/series/M2",
    "/api/v1/alerts/recent",
    "/api/v1/alerts/stats",
    "/api/v1/config",
    "/api/v1/audit",
    "/api/latest",
    "/api/stats",
    "/api/skipped"
)

foreach ($ep in $Endpoints) {
    try {
        $r = Invoke-WebRequest -Uri "http://localhost$ep" -Method Get -TimeoutSec 5 -UseBasicParsing
        if ($r.StatusCode -eq 200) {
            Write-OK "GET $ep -> 200"
        } else {
            Write-Fail "GET $ep -> $($r.StatusCode)"
        }
    } catch {
        $status = $_.Exception.Response.StatusCode.value__
        if ($status -eq 404) {
            Write-Fail "GET $ep -> 404 (路由未注册)"
        } else {
            Write-Fail "GET $ep -> 异常: $($_.Exception.Message)"
        }
    }
}

# ── 4. WebSocket ───────────────────────────────────────────────────────

if (-not $SkipWebSocket) {
    Write-Header "[4] WebSocket /ws/alerts"

    # PowerShell 原生不支持 WS，但可用 python 临时验证
    $PyCheck = @'
import asyncio, json
import websockets

async def main():
    try:
        async with websockets.connect("ws://localhost/ws/alerts") as ws:
            await ws.send(json.dumps({"type": "hello"}))
            msg = await asyncio.wait_for(ws.recv(), timeout=5)
            print(f"[OK] WS 收到消息: {msg[:100]}")
            return 0
    except Exception as e:
        print(f"[FAIL] WS 异常: {e}")
        return 1

import sys
sys.exit(asyncio.run(main()))
'@

    try {
        $PyOut = py -3 -c $PyCheck 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-OK "WebSocket /ws/alerts: 已连接并收到消息"
        } else {
            Write-Fail "WebSocket /ws/alerts: $PyOut"
        }
    } catch {
        Write-Warn "WebSocket 检查跳过（需 py + websockets）"
    }
} else {
    Write-Warn "WebSocket 检查已跳过 (-SkipWebSocket)"
}

# ── 5. SQLite 持久化 ──────────────────────────────────────────────────

Write-Header "[5] SQLite 持久化检查"

try {
    $ApiExec = docker exec quant-api-node ls -la /app/data/ 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "容器数据目录可访问"
        if ($ApiExec -match "v13_state.db") {
            Write-OK "SQLite 文件存在: v13_state.db"
        } else {
            Write-Warn "SQLite 文件尚未生成（首次部署正常，需先触发 pipeline）"
        }
    } else {
        Write-Fail "容器内 /app/data 不可访问: $ApiExec"
    }
} catch {
    Write-Fail "Docker exec 失败: $_"
}

# ── 6. Redis 健康 ─────────────────────────────────────────────────────

Write-Header "[6] Redis 健康检查"

try {
    $RedisPing = docker exec quant-state-node redis-cli ping 2>&1
    if ($RedisPing -match "PONG") {
        Write-OK "Redis PING -> PONG"
    } else {
        Write-Fail "Redis PING 异常: $RedisPing"
    }
} catch {
    Write-Fail "redis-cli 不可用: $_"
}

# ── 7. 调度器状态 ─────────────────────────────────────────────────────

Write-Header "[7] APScheduler 调度器状态"

try {
    $SchedulerLog = docker logs quant-api-node --tail 50 2>&1 | Select-String "APScheduler"
    if ($SchedulerLog) {
        Write-OK "调度器已启动: $($SchedulerLog[0])"
    } else {
        Write-Warn "调度器未在日志中（QUANT_ENABLE_SCHEDULER 可能为 false）"
    }
} catch {
    Write-Fail "无法读取容器日志: $_"
}

# ── 8. 可选：显示最近日志 ─────────────────────────────────────────────

if ($ShowLogs) {
    Write-Header "[8] 最近 30 行容器日志"
    foreach ($svc in @("quant-api-node", "quant-ui-node")) {
        Write-Host "  --- $svc ---" -ForegroundColor Yellow
        docker logs $svc --tail 30 2>&1 | ForEach-Object { Write-Host "    $_" }
    }
}

# ── 总结 ──────────────────────────────────────────────────────────────

Write-Header "验证总结"

if ($script:FailedCount -eq 0) {
    Write-Host ""
    Write-Host "  [OK] 全部检查通过！V1.3 部署健康。" -ForegroundColor Green
    Write-Host ""
    Write-Host "  下一步："
    Write-Host "  1. 访问 http://localhost/ 查看 Next.js 控制台"
    Write-Host "  2. 访问 http://localhost/api/docs 查看 Swagger"
    Write-Host "  3. 等待 21:00 美东调度自动触发，或手动:"
    Write-Host "     docker exec quant-api-node py -3 -c 'from src.main import run_full_pipeline; run_full_pipeline()'"
    Write-Host ""
    exit 0
} else {
    Write-Host ""
    Write-Host "  [FAIL] 有 $script:FailedCount 项检查未通过" -ForegroundColor Red
    Write-Host ""
    Write-Host "  排查建议："
    Write-Host "  1. 查看容器日志: docker compose -f $ComposeFile logs --tail 100"
    Write-Host "  2. 重启服务:    docker compose -f $ComposeFile restart"
    Write-Host "  3. 完全重建:    docker compose -f $ComposeFile down -v && docker compose up -d --build"
    Write-Host "  4. 加 -ShowLogs 参数查看最近日志"
    Write-Host ""
    exit 1
}