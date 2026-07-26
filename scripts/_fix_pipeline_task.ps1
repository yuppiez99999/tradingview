# v8.6.9 P1 FIX: 修复 QuantPipelineFactor_06AM 任务计划 (中文路径兼容版)
# 使用 schtasks /change 命令直接修改 Command 和 Arguments

$ErrorActionPreference = "Stop"
chcp 65001 | Out-Null

$taskName = "QuantPipelineFactor_06AM"
$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$scriptPath = "E:\各种PY程序\28-终极量化交易系统8.4\scripts\run_pipeline_factor_offline.py"

# 验证路径
if (-not (Test-Path $pythonExe)) {
    Write-Host "[ERROR] Python not found: $pythonExe" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $scriptPath)) {
    Write-Host "[ERROR] Script not found: $scriptPath" -ForegroundColor Red
    exit 1
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Fixing $taskName Task (via schtasks /change)" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  Python Exe   : $pythonExe"
Write-Host "  Script       : $scriptPath"
Write-Host ""

# schtasks /change /TR 用双引号包裹 Command 和 Arguments
$newCmd = '"' + $pythonExe + '" "' + $scriptPath + '"'
Write-Host "[INFO] New TaskRun: $newCmd" -ForegroundColor Yellow

# 执行修改
& schtasks /change /tn $taskName /tr $newCmd

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Task Updated Successfully!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Host "[ERROR] schtasks /change failed (exit=$LASTEXITCODE)" -ForegroundColor Red
    exit 1
}

# 验证
Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
& schtasks /query /tn $taskName /xml | Select-String -Pattern "Command|Arguments|WorkingDirectory" -SimpleMatch
