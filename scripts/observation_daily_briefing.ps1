# ============================================================
# 观察期每日播报启动器 (v84_ObservationBriefing)
# ============================================================
# 每日 16:10 运行, 调用 Python 生成播报并打印到 stdout
# ============================================================

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$ProjectRoot = "E:\各种PY程序\28-终极量化交易系统8.4"
$ScriptPath = "$ProjectRoot\scripts\observation_daily_briefing.py"

# 优先使用 Python 3.8 (项目标准解释器)
$Python = "py -3.8"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    $Python = "python"
}

Set-Location $ProjectRoot

# 调用 Python 脚本
& cmd /c "$Python `"$ScriptPath`" 2>&1"

exit $LASTEXITCODE
