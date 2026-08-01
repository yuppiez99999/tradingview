<#
.SYNOPSIS
    Migrate quant system data from C/E drive to D drive
.DESCRIPTION
    1. Check D drive free space
    2. Stop running Python processes
    3. Create D:\QuantData directory structure
    4. Migrate data_cache/output/reports/models/logs
    5. Clean C drive temp files
    6. Verify migration integrity
.NOTES
    Run: powershell -ExecutionPolicy Bypass -File .\scripts\migrate_data_to_d_drive.ps1
#>

param(
    [string]$SourceRoot = "",
    [string]$DestRoot = "D:\QuantData",
    [switch]$DryRun,
    [switch]$SkipStopProc,
    [switch]$SkipCleanC
)

# Auto-derive SourceRoot from script location (avoids Chinese path encoding issues)
if ($SourceRoot -eq "") {
    $SourceRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 3.0

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Quant System Data Migration: C/E -> D" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

if ($DryRun) {
    Write-Host "[DryRun Mode] Preview only, no files will be copied/deleted`n" -ForegroundColor Yellow
}

# ============================================================
# Step 1: Check D drive
# ============================================================
Write-Host "[Step 1] Checking D drive..." -ForegroundColor Green

if (-not (Test-Path "D:\")) {
    Write-Host "ERROR: D drive does not exist!" -ForegroundColor Red
    exit 1
}

$dDrive = Get-PSDrive D
$dFreeGB = [math]::Round($dDrive.Free / 1GB, 2)
Write-Host "  D drive free space: ${dFreeGB} GB" -ForegroundColor White

if ($dFreeGB -lt 5) {
    Write-Host "WARNING: D drive free space < 5GB, may not be enough!" -ForegroundColor Yellow
}

# ============================================================
# Step 2: Check source data size
# ============================================================
Write-Host "`n[Step 2] Checking source data size..." -ForegroundColor Green

$dataDirs = @("data_cache", "output", "reports", "models", "logs")
$totalSizeMB = 0

foreach ($dir in $dataDirs) {
    $srcPath = Join-Path $SourceRoot $dir
    if (Test-Path $srcPath) {
        $size = (Get-ChildItem $srcPath -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object Length -Sum).Sum
        $sizeMB = [math]::Round($size / 1MB, 2)
        $totalSizeMB += $sizeMB
        Write-Host "  $dir : ${sizeMB} MB" -ForegroundColor White
    } else {
        Write-Host "  $dir : not found (skip)" -ForegroundColor DarkGray
    }
}

Write-Host "  ------------------------" -ForegroundColor DarkGray
Write-Host "  Total: ${totalSizeMB} MB ($([math]::Round($totalSizeMB/1024, 2)) GB)" -ForegroundColor Cyan

if ($totalSizeMB / 1024 -gt $dFreeGB) {
    Write-Host "`nERROR: Source data ($([math]::Round($totalSizeMB/1024, 2)) GB) > D drive free (${dFreeGB} GB)!" -ForegroundColor Red
    exit 1
}

# ============================================================
# Step 3: Stop running Python processes
# ============================================================
if (-not $SkipStopProc) {
    Write-Host "`n[Step 3] Stopping Python processes..." -ForegroundColor Green

    $pyProcs = @(Get-Process python -ErrorAction SilentlyContinue)
    if ($pyProcs.Count -gt 0) {
        foreach ($proc in $pyProcs) {
            $memMB = [math]::Round($proc.WorkingSet64 / 1MB, 1)
            Write-Host "  Stopping PID=$($proc.Id) (mem ${memMB} MB)..." -ForegroundColor Yellow
            if (-not $DryRun) {
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Seconds 2
        Write-Host "  Stopped $($pyProcs.Count) Python process(es)" -ForegroundColor Green
    } else {
        Write-Host "  No running Python processes" -ForegroundColor DarkGray
    }
} else {
    Write-Host "`n[Step 3] Skipped (-SkipStopProc)" -ForegroundColor DarkGray
}

# ============================================================
# Step 4: Create destination directories
# ============================================================
Write-Host "`n[Step 4] Creating D drive directory structure..." -ForegroundColor Green

if (-not $DryRun) {
    New-Item -ItemType Directory -Path $DestRoot -Force | Out-Null
    Write-Host "  Created: $DestRoot" -ForegroundColor White
}

foreach ($dir in $dataDirs) {
    $destPath = Join-Path $DestRoot $dir
    if (-not $DryRun) {
        New-Item -ItemType Directory -Path $destPath -Force | Out-Null
    }
    Write-Host "  Created: $destPath" -ForegroundColor White
}

# ============================================================
# Step 5: Migrate data (copy, do not delete source)
# ============================================================
Write-Host "`n[Step 5] Migrating data..." -ForegroundColor Green

foreach ($dir in $dataDirs) {
    $srcPath = Join-Path $SourceRoot $dir
    $destPath = Join-Path $DestRoot $dir

    if (-not (Test-Path $srcPath)) {
        Write-Host "  Skip $dir (source not found)" -ForegroundColor DarkGray
        continue
    }

    $fileCount = (Get-ChildItem $srcPath -Recurse -File -ErrorAction SilentlyContinue).Count
    Write-Host "  Copying $dir ($fileCount files)..." -ForegroundColor White

    if (-not $DryRun) {
        $robocopyArgs = @($srcPath, $destPath, "/MIR", "/MT:8", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NP")
        & robocopy @robocopyArgs 2>&1 | Out-Null

        if ($LASTEXITCODE -ge 8) {
            Write-Host "  ERROR: $dir copy failed (robocopy exit=$LASTEXITCODE)" -ForegroundColor Red
        } else {
            Write-Host "  OK: $dir migrated" -ForegroundColor Green
        }
    }
}

# ============================================================
# Step 6: Verify migration integrity
# ============================================================
if (-not $DryRun) {
    Write-Host "`n[Step 6] Verifying migration..." -ForegroundColor Green

    $allOk = $true
    foreach ($dir in $dataDirs) {
        $srcPath = Join-Path $SourceRoot $dir
        $destPath = Join-Path $DestRoot $dir

        if (-not (Test-Path $srcPath)) { continue }

        $srcCount = (Get-ChildItem $srcPath -Recurse -File -ErrorAction SilentlyContinue).Count
        $destCount = (Get-ChildItem $destPath -Recurse -File -ErrorAction SilentlyContinue).Count

        if ($srcCount -eq $destCount) {
            Write-Host "  $dir : $destCount/$srcCount files OK" -ForegroundColor Green
        } else {
            Write-Host "  $dir : $destCount/$srcCount files MISMATCH!" -ForegroundColor Red
            $allOk = $false
        }
    }

    if ($allOk) {
        Write-Host "`n  Verification passed!" -ForegroundColor Green
    } else {
        Write-Host "`n  Verification FAILED! Check mismatched dirs." -ForegroundColor Red
    }
}

# ============================================================
# Step 7: Clean C drive temp files
# ============================================================
if (-not $SkipCleanC -and -not $DryRun) {
    Write-Host "`n[Step 7] Cleaning C drive temp files..." -ForegroundColor Green

    $tempDirs = @(
        "$env:TEMP",
        "$env:LOCALAPPDATA\Temp",
        "C:\Windows\Temp"
    )

    $cleanedMB = 0
    foreach ($tempDir in $tempDirs) {
        if (Test-Path $tempDir) {
            $tempFiles = Get-ChildItem $tempDir -Recurse -File -ErrorAction SilentlyContinue |
                         Where-Object { $_.LastWriteTime -lt (Get-Date).AddDays(-1) }
            foreach ($f in $tempFiles) {
                $cleanedMB += $f.Length / 1MB
                Remove-Item $f.FullName -Force -ErrorAction SilentlyContinue
            }
        }
    }
    $cleanedMB = [math]::Round($cleanedMB, 2)
    Write-Host "  Cleaned temp files: ${cleanedMB} MB" -ForegroundColor White

    Write-Host "  Cleaning pip cache..." -ForegroundColor White
    & python -m pip cache purge 2>&1 | Out-Null

    $cDrive = Get-PSDrive C
    $cFreeGB = [math]::Round($cDrive.Free / 1GB, 2)
    Write-Host "  C drive free after cleanup: ${cFreeGB} GB" -ForegroundColor Cyan
} elseif ($DryRun) {
    Write-Host "`n[Step 7] Skipped (DryRun)" -ForegroundColor DarkGray
} else {
    Write-Host "`n[Step 7] Skipped (-SkipCleanC)" -ForegroundColor DarkGray
}

# ============================================================
# Done
# ============================================================
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  Migration Complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nData root: $DestRoot" -ForegroundColor White
Write-Host "Config updated: .env QUANT_DATA_ROOT=$DestRoot`n" -ForegroundColor White

if (-not $DryRun) {
    Write-Host "Next steps:" -ForegroundColor Yellow
    Write-Host "  1. Verify config: python -c `"from utils.path_config import describe_paths; import json; print(json.dumps(describe_paths(), indent=2))`"" -ForegroundColor White
    Write-Host "  2. Restart backtest: python research\run_backtest_rerun.py" -ForegroundColor White
    Write-Host "  3. After verification, delete source data if needed" -ForegroundColor DarkGray
}
