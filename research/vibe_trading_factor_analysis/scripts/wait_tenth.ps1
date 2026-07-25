# 等待第十批次流水线完成
$logFile = "C:\Users\ADMINI~1\AppData\Local\Temp\trae-agent-toolhost\jobs\job-cfe0ab2d8bfc4829a67d0bf02f4ec5c2\output.log"
$stateFile = "C:\Users\ADMINI~1\AppData\Local\Temp\trae-agent-toolhost\jobs\job-cfe0ab2d8bfc4829a67d0bf02f4ec5c2\state.json"
$maxWaitMinutes = 12
$startTime = Get-Date

while ($true) {
    if (Test-Path $logFile) {
        $size = (Get-Item $logFile -ErrorAction SilentlyContinue).Length
    } else {
        $size = 0
    }
    $elapsed = (Get-Date) - $startTime
    Write-Host "$(Get-Date -Format 'HH:mm:ss') - log size: $size bytes (elapsed: $($elapsed.TotalSeconds)s)"

    # 检查任务状态
    if (Test-Path $stateFile) {
        $state = Get-Content $stateFile -Raw | ConvertFrom-Json
        if ($state.status -eq "Completed" -or $state.status -eq "Failed") {
            Write-Host "Task $($state.status). Exit code: $($state.exit_code)"
            break
        }
    }
    if ($elapsed.TotalMinutes -gt $maxWaitMinutes) {
        Write-Host "Timeout after $maxWaitMinutes minutes"
        break
    }
    Start-Sleep -Seconds 30
}

# 输出最后部分日志
if (Test-Path $logFile) {
    Write-Host ""
    Write-Host "=== Final log (last 80 lines) ==="
    Get-Content $logFile -Encoding UTF8 | Select-Object -Last 80
}
