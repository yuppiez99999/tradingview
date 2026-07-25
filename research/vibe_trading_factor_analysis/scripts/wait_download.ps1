# 等待下载脚本完成
$target = 105
$maxWaitMinutes = 5
$startTime = Get-Date

while ($true) {
    $count = (Get-ChildItem "e:\各种PY程序\28-终极量化交易系统8.4\cache\fundamentals\*_history.json" -ErrorAction SilentlyContinue | Measure-Object).Count
    $elapsed = (Get-Date) - $startTime
    Write-Host "$(Get-Date -Format 'HH:mm:ss') - downloaded: $count/$target (elapsed: $($elapsed.TotalSeconds)s)"

    if ($count -ge $target) {
        Write-Host "Download completed!"
        break
    }
    if ($elapsed.TotalMinutes -gt $maxWaitMinutes) {
        Write-Host "Timeout after $maxWaitMinutes minutes (current: $count/$target)"
        break
    }
    Start-Sleep -Seconds 15
}
