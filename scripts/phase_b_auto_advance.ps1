#!/usr/bin/env pwsh
#===============================================================================
# Phase B Auto Advance Script
# Daily check: if observation period is complete, run --advance automatically.
#===============================================================================

$ErrorActionPreference = 'Stop'

$ProjectRoot = 'E:\各种PY程序\28-终极量化交易系统8.4'
$ScriptPath = Join-Path $ProjectRoot 'scripts/phase_b_progressive_enabler.py'
$PythonExe = 'python'
$LogDir = Join-Path $ProjectRoot 'reports/evolution/logs'
$LogFile = Join-Path $LogDir 'phase_b_auto_advance.log'

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-Log {
    param([string]$Message)
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = "[$ts] $Message"
    Write-Output $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

try {
    Write-Log '=== Phase B auto check start ==='

    if (-not (Test-Path $ScriptPath)) {
        Write-Log "ERROR: script not found: $ScriptPath"
        exit 1
    }

    Push-Location $ProjectRoot
    $checkOutput = & $PythonExe -X utf8 $ScriptPath --check 2>&1
    Pop-Location

    Write-Log "CHECK OUTPUT:`n$checkOutput"

    $remaining = $null
    $m1 = [regex]::Match($checkOutput, 'observation remaining:\s*(\d+)\s*day', 'IgnoreCase')
    if ($m1.Success) {
        $remaining = [int]$m1.Groups[1].Value
    } else {
        $m2 = [regex]::Match($checkOutput, '(\d+)/(\d+)\s*天')
        if ($m2.Success) {
            $completed = [int]$m2.Groups[1].Value
            $required = [int]$m2.Groups[2].Value
            $remaining = $required - $completed
        }
    }

    if (-not $remaining) {
        Write-Log 'WARN: cannot parse remaining days, skip auto advance'
        $remaining = 1
    }

    Write-Log "remaining observation days: $remaining"

    if ($remaining -gt 0) {
        Write-Log 'observation not complete, skip advance'
        Write-Log '=== Phase B auto check finished (no action) ==='
        exit 0
    }

    Write-Log 'observation complete, running --advance ...'
    Push-Location $ProjectRoot
    $advanceOutput = & $PythonExe -X utf8 $ScriptPath --advance 2>&1
    Pop-Location

    Write-Log "ADVANCE OUTPUT:`n$advanceOutput"

    if ($advanceOutput -match '\[OK\]') {
        Write-Log 'SUCCESS: advanced to next stage'
    } elseif ($advanceOutput -match '\[WAIT\]') {
        Write-Log 'WAIT: conditions not met'
    } elseif ($advanceOutput -match '\[DONE\]') {
        Write-Log 'DONE: already at final stage'
    } else {
        Write-Log 'WARN: unexpected advance output'
    }

    Write-Log '=== Phase B auto check finished ==='
    exit 0

} catch {
    Write-Log "EXCEPTION: $_"
    exit 1
}
