# 启动本机抖音 sidecar（Evil0ctal V4.1.2，纯 Python，无需 Docker）
#
# 用法：
#   .\scripts\start-douyin-sidecar.ps1                 # 默认 127.0.0.1:8000
#   .\scripts\start-douyin-sidecar.ps1 -Port 8010
#   $env:DISTILL_EVERYTHING_PYTHON = 'C:\...\python.exe'   # 指定解释器
#
# 首次准备见 docs/platform-sources.md §3.2「本机运行（无 Docker）」

param(
    [int]$Port = 8000,
    [string]$Python = $env:DISTILL_EVERYTHING_PYTHON
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sidecar = Join-Path $root '.local-artifacts\douyin-sidecar'

if (-not (Test-Path $sidecar)) {
    throw "未找到 sidecar：$sidecar。请先按 docs/platform-sources.md §3.2 克隆并安装。"
}
if (-not $Python) {
    $Python = 'python'
}

Write-Host "启动 sidecar：$sidecar"
Write-Host "监听：http://127.0.0.1:$Port  （Ctrl+C 停止）"

Push-Location $sidecar
try {
    & $Python -m uvicorn app.main:app --host 127.0.0.1 --port $Port
}
finally {
    Pop-Location
}
