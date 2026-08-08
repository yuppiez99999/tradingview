$root = "E:\各种PY程序\28-终极量化交易系统8.4"
$exclude = "venv|env|node_modules|\.git|__pycache__|vibe_trading|qlib|research\\references|_archive"
$files = Get-ChildItem -Path $root -Recurse -Include "*.py" -ErrorAction SilentlyContinue | Where-Object { $_.FullName -notmatch $exclude }
$byDir = @{}
$total = 0
foreach ($f in $files) {
    $matches = Select-String -Path $f.FullName -Pattern "(?<![\w\.])print\s*\(" -ErrorAction SilentlyContinue
    if ($matches) {
        foreach ($m in $matches) {
            $line = $m.Line.Trim()
            if ($line -match "^\s*#") { continue }
            if ($line -match "^\s*>>>|Usage:|Example:") { continue }
            # 取顶级目录
            $rel = $f.FullName.Substring($root.Length + 1)
            $topDir = $rel -split "[\\/]" | Select-Object -First 1
            if (-not $byDir.ContainsKey($topDir)) { $byDir[$topDir] = 0 }
            $byDir[$topDir]++
            $total++
        }
    }
}
Write-Host "Top-level directory distribution of print() calls:"
Write-Host "Total: $total"
$byDir.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { Write-Host ("  {0,-30} {1}" -f $_.Key, $_.Value) }
