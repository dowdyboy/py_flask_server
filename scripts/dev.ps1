# Flask Server 一键开发启动脚本（Windows PowerShell）
# 用法：.\scripts\dev.ps1
# 功能：缺失 .env 时自动从 .env.example 创建 → 启动开发服务器

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1. 检查 .env
if (-not (Test-Path '.env')) {
    Copy-Item '.env.example' '.env'
    Write-Host '[dev] 已从 .env.example 创建 .env，请按需编辑配置' -ForegroundColor Yellow
}

# 2. 检查依赖（可选提示）
if (-not (Test-Path '.venv')) {
    Write-Host '[dev] 未找到 .venv，建议：' -ForegroundColor Yellow
    Write-Host '      python -m venv .venv; .\.venv\Scripts\Activate.ps1' -ForegroundColor Yellow
    Write-Host '      pip install -r requirements.txt -r requirements-dev.txt' -ForegroundColor Yellow
}

# 3. 启动
Write-Host '[dev] 启动开发服务器: http://127.0.0.1:5000/docs' -ForegroundColor Cyan
python server.py
