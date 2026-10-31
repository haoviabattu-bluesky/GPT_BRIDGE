# =====================================================================
# GPT_BRIDGE — khởi động trên Windows (PowerShell)
# KHÔNG chiếm chuột/bàn phím. KHÔNG mở cửa sổ Chrome. KHÔNG đụng app khác.
#
#   .\start.ps1              chạy 1 vòng
#   .\start.ps1 -Loop        chạy nền theo scan_interval_seconds
#   .\start.ps1 -Doctor      kiểm tra môi trường
#   .\start.ps1 -Stop        dừng an toàn
# =====================================================================
param(
    [switch]$Loop,
    [switch]$Doctor,
    [switch]$Stop,
    [switch]$Confirm,
    [switch]$Auto
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    $Py = "python"
    Write-Host "(!) chưa có .venv — dùng python hệ thống: $Py" -ForegroundColor Yellow
}

if ($Stop) {
    & $Py bridge.py stop
    exit $LASTEXITCODE
}

if ($Doctor) {
    & $Py bridge.py doctor
    exit $LASTEXITCODE
}

if ($Loop) {
    Write-Host "GPT_BRIDGE — chế độ nền. Dừng: .\start.ps1 -Stop hoặc Ctrl-C" -ForegroundColor Cyan
    & $Py bridge.py loop --clear-stop @(if ($Confirm) { "--confirm" }) @(if ($Auto) { "--auto" })
    exit $LASTEXITCODE
}

& $Py bridge.py run @(if ($Confirm) { "--confirm" }) @(if ($Auto) { "--auto" })
exit $LASTEXITCODE
