$root = "E:\各种PY程序\28-终极量化交易系统8.4"
$exclude = "venv|env|node_modules|\.git|__pycache__|vibe_trading|qlib|research\\references|_archive"
Write-Host "=== Project-wide print() scan (excluding 3rd-party) ===" -ForegroundColor Cyan
$files = Get-ChildItem -Path $root -Recurse -Include "*.py" -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch $exclude }
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
Write-Host "Total real print() in project (excluding 3rd-party): $total"
foreach ($k in ($byFile.Keys | Sort-Object)) {
    $cnt = $byFile[$k].Count
    Write-Host "--- $k ($cnt) ---" -ForegroundColor Yellow
    $byFile[$k] | Select-Object -First 5 | ForEach-Object { Write-Host "  $_" }
    if ($cnt -gt 5) { Write-Host "  ... and $($cnt - 5) more" }
}
