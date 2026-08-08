$root = "E:\各种PY程序\28-终极量化交易系统8.4\utils"
Write-Host "=== utils/ print() scan (excluding docstring examples) ===" -ForegroundColor Cyan
$files = Get-ChildItem -Path $root -Recurse -Include "*.py" -ErrorAction SilentlyContinue
$total = 0
$byFile = @{}
foreach ($f in $files) {
    $matches = Select-String -Path $f.FullName -Pattern "(?<![\w\.])print\s*\(" -ErrorAction SilentlyContinue
    if ($matches) {
        foreach ($m in $matches) {
            $line = $m.Line.Trim()
            if ($line -match "^\s*#") { continue }
            if ($line -match "^\s*>>>|Usage:|Example:") { continue }
            $total++
            if (-not $byFile.ContainsKey($f.FullName)) { $byFile[$f.FullName] = @() }
            $byFile[$f.FullName] += "$($m.LineNumber): $line"
        }
    }
}
Write-Host "Total real print() in utils/: $total"
foreach ($k in ($byFile.Keys | Sort-Object)) {
    Write-Host "--- $k ---" -ForegroundColor Yellow
    $byFile[$k] | ForEach-Object { Write-Host "  $_" }
}
