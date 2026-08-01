# 卸载28终极量化每日自动任务
$ErrorActionPreference = "Stop"

$schedule = New-Object -ComObject Schedule.Service
$schedule.Connect()
$rootFolder = $schedule.GetFolder("\")

$tasks = @(
    "v84_PreMarketDaily",
    "v84_IntradayLLMDecision",
    "v84_PostMarketExecute",
    "v84_PreMarketInstructions"
)

Write-Host ""
Write-Host "卸载28终极量化自动任务..." -ForegroundColor Cyan
Write-Host ""

foreach ($tn in $tasks) {
    try {
        $t = $rootFolder.GetTask($tn)
        if ($t) {
            $rootFolder.DeleteTask($tn)
            Write-Host "  ✓ 已卸载: $tn" -ForegroundColor Green
        }
    } catch {
        Write-Host "  - 未找到: $tn" -ForegroundColor Gray
    }
}

Write-Host ""
Write-Host "完成。" -ForegroundColor Cyan
Write-Host ""
