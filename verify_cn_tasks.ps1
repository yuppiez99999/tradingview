[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")
$pyExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$qdir = "e:\各种PY程序\11_量化策略"

$taskNames = @(
    "晨间PY程序自动运行",
    "每日工作流_早间报告",
    "量化策略_01盘前计划",
    "量化策略_02盘中监控",
    "量化策略_03盘后报告"
)

Write-Host "============================================================"
Write-Host "  Verifying 5 repaired tasks"
Write-Host "============================================================"

foreach ($name in $taskNames) {
    Write-Host ""
    Write-Host "==== Task: $name ===="
    try {
        $task = $rootFolder.GetTask($name)
        Write-Host ("  State: {0}" -f $task.State)
        $def = $task.Definition
        $action = $def.Actions.Item(1)
        Write-Host ("  Path:   {0}" -f $action.Path)
        Write-Host ("  Args:   {0}" -f $action.Arguments)
        Write-Host ("  WorkDir:{0}" -f $action.WorkingDirectory)

        # 提取 Args 中 .py 路径并验证
        $args = $action.Arguments
        if ($args -match '"([^"]+\.py)"') {
            $pyPath = $matches[1]
            $exists = Test-Path -LiteralPath $pyPath
            Write-Host ("  .py exists: {0}" -f $exists)
        }

        # 显示最近运行状态
        Write-Host ("  LastRun: {0} (Exit={1})" -f $task.LastRunTime, $task.LastTaskResult)
        Write-Host ("  NextRun: {0}" -f $task.NextRunTime)
    } catch {
        Write-Host ("  [ERROR] {0}" -f $_.Exception.Message)
    }
}

Write-Host ""
Write-Host "============================================================"
Write-Host "  Done"
Write-Host "============================================================"
