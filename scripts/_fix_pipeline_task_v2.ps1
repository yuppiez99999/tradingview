# v8.6.9 P1 FIX: 用 COM API 修改任务计划
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$taskName = "QuantPipelineFactor_06AM"
$pythonExe = "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe"
$scriptPath = "E:\各种PY程序\28-终极量化交易系统8.4\scripts\run_pipeline_factor_offline.py"
$workDir = "E:\各种PY程序\28-终极量化交易系统8.4"

if (-not (Test-Path $pythonExe)) {
    Write-Host "[ERROR] Python not found" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $scriptPath)) {
    Write-Host "[ERROR] Script not found" -ForegroundColor Red
    exit 1
}

Write-Host "Connecting to Task Scheduler..." -ForegroundColor Cyan
$service = New-Object -ComObject Schedule.Service
$service.Connect()
$rootFolder = $service.GetFolder("\")
$task = $rootFolder.GetTask($taskName)
if (-not $task) {
    Write-Host "[ERROR] Task not found" -ForegroundColor Red
    exit 1
}

$taskXml = $task.Xml
$xml = [xml]$taskXml

$execAction = $xml.Task.Actions.Exec
if (-not $execAction) {
    Write-Host "[ERROR] No Exec action" -ForegroundColor Red
    exit 1
}

$execAction.Command = $pythonExe

if (-not $execAction.Arguments) {
    $execAction.AppendChild($xml.CreateElement("Arguments", $xml.DocumentElement.NamespaceURI)) | Out-Null
}
$execAction.Arguments = '"' + $scriptPath + '"'

if (-not $execAction.WorkingDirectory) {
    $wdElement = $xml.CreateElement("WorkingDirectory", $xml.DocumentElement.NamespaceURI)
    $execAction.AppendChild($wdElement) | Out-Null
}
$execAction.WorkingDirectory = $workDir

Write-Host "New Command: $($execAction.Command)" -ForegroundColor Green
Write-Host "New Arguments: $($execAction.Arguments)" -ForegroundColor Green
Write-Host "New WorkingDirectory: $($execAction.WorkingDirectory)" -ForegroundColor Green

$newXml = $xml.OuterXml
$rootFolder.RegisterTaskDefinition($taskName, $newXml, 4, $null, $null, 3) | Out-Null

Write-Host ""
Write-Host "Task updated successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Verification:" -ForegroundColor Cyan
