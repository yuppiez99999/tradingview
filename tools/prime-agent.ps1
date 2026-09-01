# Prime Agent launcher for quant trading system v8.6
# Usage: powershell -File tools\prime-agent.ps1 [command] [args]
# Run from quant system root dir. First run: execute /login in TUI.
$ErrorActionPreference = "Continue"
$paCli = "E:\各种PY程序\10_第三方项目\prime-agent\packages\coding-agent\dist\cli.js"
$paArgs = $args
if ($paArgs.Count -eq 0) {
    & node $paCli
} else {
    & node $paCli @paArgs
}
