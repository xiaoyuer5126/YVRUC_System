# 一键启动脚本（Windows）
#   .\start.ps1            常规启动（已有数据直接跑）
#   .\start.ps1 -RebuildDB 重置演示数据
#   .\start.ps1 -BuildWeb  先重新构建前端
param(
    [switch]$RebuildDB,
    [switch]$BuildWeb
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $MyInvocation.MyCommand.Path)

Write-Host "== 人大校青协志愿服务管理平台 ==" -ForegroundColor DarkRed

# 1) 数据库（Docker Postgres，失败自动降级 SQLite）
docker info *> $null
if ($LASTEXITCODE -eq 0) {
    docker compose up -d 2>&1 | Out-Null
    Write-Host "[db] PostgreSQL 容器已就绪 (127.0.0.1:5433)"
} else {
    Write-Host "[db] Docker 未运行，将使用 SQLite 演示库" -ForegroundColor Yellow
}

# 2) 前端构建产物检查
if ($BuildWeb -or -not (Test-Path "web\dist\index.html")) {
    Push-Location web
    if (-not (Test-Path "node_modules")) {
        Write-Host "[web] 安装前端依赖..."
        npm install --registry=https://registry.npmmirror.com --no-fund --no-audit
    }
    Write-Host "[web] 构建前端..."
    npm run build
    Pop-Location
}

# 3) 演示数据
if ($RebuildDB) {
    Write-Host "[seed] 重置演示数据..."
    python -m backend.seed
}

Write-Host ""
Write-Host "服务地址: http://127.0.0.1:8000   (Ctrl+C 停止)" -ForegroundColor Green
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
