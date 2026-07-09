﻿# ============================================================
# 修复 5 个中文命名任务 (UTF-8 BOM, 正确任务名)
# ============================================================
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")

$pyExe = @'
C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe
'@.TrimEnd()
$qdir = @'
e:\各种PY程序\11_量化策略
'@.TrimEnd()

$repairs = @(
    @{
        Name = @'
晨间PY程序自动运行
'@.TrimEnd()
        Script = 'morning_workflow.py'
        WorkDir = @'
e:\各种PY程序\11_量化策略
'@.TrimEnd()
        ArgsExtra = '--schedule'
    },
    @{
        Name = @'
每日工作流_早间报告
'@.TrimEnd()
        Script = 'premarket_report.py'
        WorkDir = @'
e:\各种PY程序\11_量化策略
'@.TrimEnd()
        ArgsExtra = ''
    },
    @{
        Name = @'
量化策略_01盘前计划
'@.TrimEnd()
        Script = 'auto_premarket_plan.py'
        WorkDir = @'
e:\各种PY程序\11_量化策略
'@.TrimEnd()
        ArgsExtra = ''
    },
    @{
        Name = @'
量化策略_02盘中监控
'@.TrimEnd()
        Script = 'auto_intraday_decision.py'
        WorkDir = @'
e:\各种PY程序\11_量化策略
'@.TrimEnd()
        ArgsExtra = ''
    },
    @{
        Name = @'
量化策略_03盘后报告
'@.TrimEnd()
        Script = 'daily_close_report.py'
        WorkDir = @'
e:\各种PY程序\11_量化策略
'@.TrimEnd()
        ArgsExtra = ''
    }
)

$success = 0
$failed = 0

Write-Host "============================================================"
Write-Host "  Repairing $($repairs.Count) Chinese-named tasks"
Write-Host "============================================================"

foreach ($cfg in $repairs) {
    $name = $cfg.Name
    Write-Host ""
    Write-Host ("---- Task: {0} ----" -f $name)

    try {
        $task = $rootFolder.GetTask($name)
        $def = $task.Definition
        $action = $def.Actions.Item(1)

        $scriptPath = Join-Path $qdir $cfg.Script
        $args = '"' + $scriptPath + '"'
        if ($cfg.ArgsExtra -and $cfg.ArgsExtra.Length -gt 0) { $args += " " + $cfg.ArgsExtra }

        $action.Path = $pyExe
        $action.Arguments = $args
        $action.WorkingDirectory = $cfg.WorkDir

        # 保留原 Principal
        $principal = $def.Principal
        $userId = $principal.UserId
        if (-not $userId) { $userId = $env:USERNAME }
        $logonType = $principal.LogonType
        if (-not $logonType) { $logonType = 3 }

        # TASK_UPDATE = 4 (保留触发器+Principal, 仅替换 Action)
        $rootFolder.RegisterTaskDefinition($name, $def, 4, $userId, $null, $logonType) | Out-Null

        Write-Host ("  [OK] Path={0}" -f $action.Path)
        Write-Host ("  [OK] Args={0}" -f $action.Arguments)
        Write-Host ("  [OK] WorkDir={0}" -f $action.WorkingDirectory)
        $success++
    } catch {
        Write-Host ("  [ERROR] {0}" -f $_.Exception.Message)
        $failed++
    }
}

Write-Host ""
Write-Host "============================================================"
Write-Host ("  Repair Summary: Success={0}, Failed={1}" -f $success, $failed)
Write-Host "============================================================"
