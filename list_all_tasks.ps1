[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$service = New-Object -ComObject Schedule.Service
$service.Connect()
$root = $service.GetFolder("\")

$tasks = $root.GetTasks(0)
$idx = 0
foreach ($t in $tasks) {
    $name = $t.Name
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($name)
    $hex = ($bytes | ForEach-Object { $_.ToString("X2") }) -join ' '
    Write-Host ("[{0}] Name='{1}' HEX={2}" -f $idx, $name, $hex)
    $idx++
}
Write-Host ""
Write-Host ("Total: {0}" -f $idx)
