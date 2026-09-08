# ============================================================
# Cron Python task wrapper (source is ASCII-only for safe parsing)
#
# Fix background for 0xC000013A (STATUS_CONTROL_C_EXIT):
#   InteractiveToken + bare python.exe spawns a console attached to
#   the interactive session; when a scheduled trigger fires while no
#   interactive session is present, the console receives CTRL_CLOSE
#   and python exits with 0xC000013A. SYSTEM tasks (LogonType=5)
#   run detached without a console, so this wrapper is invoked via
#   powershell.exe (hidden window) and launches python detached,
#   capturing stdout/stderr byte-exact into a timestamped log file.
#
# Scheduled task Action example:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden
#     -File "<root>\scripts\run_cron_python_wrapper.ps1"
#     -Script "scripts\foo.py" [-PyArgs "--run"] [-Tag cron_task]
#
# Script path is relative to the project root (parent of scripts/).
# Logs  -> <root>\logs\task_logs\<Tag>_<yyyyMMdd_HHmmss>.log
#         plus <Tag>_latest.log mirror (last run).
# Exit code = python exit code (0 success).
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$Script,   # path relative to project root
    [string]$PyArgs = "",
    [string]$Tag = "cron_task"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe   = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ScriptPath  = Join-Path $ProjectRoot $Script
$LogRoot     = Join-Path $ProjectRoot "logs\task_logs"

if (-not (Test-Path -LiteralPath $PythonExe)) {
    Write-Error "python not found: $PythonExe"
    exit 2
}
if (-not (Test-Path -LiteralPath $ScriptPath)) {
    Write-Error "script not found: $ScriptPath"
    exit 3
}
if (-not (Test-Path -LiteralPath $LogRoot)) {
    New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
}

$utf8    = New-Object System.Text.UTF8Encoding($false)
$stamp   = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $LogRoot ("{0}_{1}.log" -f $Tag, $stamp)

$argLine = "-X utf8 `"" + $ScriptPath + "`""
if ($PyArgs.Trim() -ne "") {
    $argLine += " " + $PyArgs.Trim()
}

$header = "[wrapper] start=" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " cmd=" + $PythonExe + " " + $argLine + "`r`n"
[System.IO.File]::AppendAllText($logFile, $header, $utf8)

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName               = $PythonExe
$psi.Arguments              = $argLine
$psi.WorkingDirectory       = $ProjectRoot
$psi.UseShellExecute        = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError  = $true
$psi.CreateNoWindow         = $true
$psi.StandardOutputEncoding = $utf8
$psi.StandardErrorEncoding  = $utf8

try {
    $proc = [System.Diagnostics.Process]::Start($psi)
} catch {
    [System.IO.File]::AppendAllText($logFile, "[wrapper] START FAILED: $_`r`n", $utf8)
    exit 4
}

$stdoutTask = $proc.StandardOutput.ReadToEndAsync()
$stderrTask = $proc.StandardError.ReadToEndAsync()
$proc.WaitForExit()

$stdout = ""
$stderr = ""
try { $stdout = $stdoutTask.GetAwaiter().GetResult() } catch { }
try { $stderr = $stderrTask.GetAwaiter().GetResult() } catch { }
$code = $proc.ExitCode

$body = ""
if ($stdout -ne "") { $body += "[stdout]`r`n" + $stdout + "`r`n" }
if ($stderr -ne "") { $body += "[stderr]`r`n" + $stderr + "`r`n" }
$body += "[wrapper] end=" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " exit_code=$code`r`n"
[System.IO.File]::AppendAllText($logFile, $body, $utf8)

# Mirror latest run for quick inspection, then prune old logs.
$lastFile = Join-Path $LogRoot ($Tag + "_latest.log")
try { Copy-Item -LiteralPath $logFile -Destination $lastFile -Force | Out-Null } catch { }
try {
    $old = Get-ChildItem -LiteralPath $LogRoot -Filter ($Tag + "_*.log") |
        Sort-Object Name -Descending | Select-Object -Skip 40
    foreach ($f in $old) { Remove-Item -LiteralPath $f.FullName -Force }
} catch { }

exit $code
