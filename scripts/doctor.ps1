<#
.SYNOPSIS
    Diagnose Whiteout Survival autopilot prerequisites on Windows.

.DESCRIPTION
    Walks every prerequisite the docker-prod quickstart depends on and prints,
    in plain language, what's missing or misconfigured. Read-only — never
    installs / starts / stops anything on its own.

.EXAMPLE
    # From the cloned repo root, in PowerShell:
    .\scripts\doctor.ps1

    # If the script is blocked by execution policy:
    powershell -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
#>

#Requires -Version 5.0
[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

# --- terminal helpers -------------------------------------------------------

$script:Results = [System.Collections.Generic.List[psobject]]::new()

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host "── $Title " -ForegroundColor Cyan -NoNewline
    Write-Host ('─' * [Math]::Max(1, 60 - $Title.Length)) -ForegroundColor DarkGray
}

function Record {
    param(
        [Parameter(Mandatory)][ValidateSet('pass', 'fail', 'warn')] [string]$Status,
        [Parameter(Mandatory)][string]$Check,
        [string]$Detail = '',
        [string]$Fix = ''
    )
    $script:Results.Add([pscustomobject]@{
        Status = $Status
        Check  = $Check
        Detail = $Detail
        Fix    = $Fix
    }) | Out-Null

    $icon, $color = switch ($Status) {
        'pass' { '[OK]   ', 'Green' }
        'fail' { '[FAIL] ', 'Red' }
        'warn' { '[WARN] ', 'Yellow' }
    }
    Write-Host -NoNewline $icon -ForegroundColor $color
    Write-Host $Check
    if ($Detail) { Write-Host "       $Detail" -ForegroundColor DarkGray }
    if ($Fix -and $Status -ne 'pass') {
        Write-Host "       → $Fix" -ForegroundColor Yellow
    }
}

# Run an external command, capture stdout+stderr, return ($exitCode, $output).
function Invoke-CheckCommand {
    param([string]$FilePath, [string[]]$ArgumentList)
    try {
        $output = & $FilePath @ArgumentList 2>&1 | Out-String
        return @($LASTEXITCODE, $output.Trim())
    } catch {
        return @(-1, $_.Exception.Message)
    }
}

# --- checks -----------------------------------------------------------------

function Test-Docker {
    Write-Section 'Docker'

    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if (-not $docker) {
        Record fail 'docker CLI on PATH' `
            -Fix 'Install Docker Desktop from https://docs.docker.com/desktop/install/windows-install/ and reopen PowerShell.'
        return
    }
    Record pass 'docker CLI on PATH' -Detail "Found: $($docker.Source)"

    $code, $out = Invoke-CheckCommand 'docker' @('info', '--format', '{{.ServerVersion}}')
    if ($code -ne 0 -or -not $out) {
        Record fail 'Docker daemon reachable' `
            -Detail 'docker info failed — daemon not running or the WSL2 backend is asleep.' `
            -Fix 'Start Docker Desktop (system tray icon → wait for "Engine running"), then re-run this script.'
        return
    }
    Record pass 'Docker daemon reachable' -Detail "Server version: $out"

    $code, $cv = Invoke-CheckCommand 'docker' @('compose', 'version', '--short')
    if ($code -ne 0) {
        Record fail 'docker compose v2 available' `
            -Detail "docker compose returned: $cv" `
            -Fix 'Update Docker Desktop — Compose v2 ships inside the same installer (no separate docker-compose binary).'
    } else {
        Record pass 'docker compose v2 available' -Detail "Compose version: $cv"
    }

    # Host networking is a beta toggle on Docker Desktop 4.34+; the API
    # exposes it via ``docker info`` plumbing experiments. We probe the user
    # message rather than the toggle directly — easier to read.
    $code, $hni = Invoke-CheckCommand 'docker' @('info', '--format', '{{json .}}')
    if ($code -eq 0 -and $hni -match '"HostNetworking"\s*:\s*true') {
        Record pass 'Host networking enabled (Docker Desktop beta)' `
            -Detail 'bot container can reach 127.0.0.1:5037 on the host without ``adb -a``.'
    } else {
        Record warn 'Host networking enabled (Docker Desktop beta)' `
            -Detail 'Could not confirm Host networking toggle from ``docker info``.' `
            -Fix 'Docker Desktop → Settings → Resources → Network → check "Enable host networking" (beta). Without it the bot container cannot reach the host adb server.'
    }
}

function Test-Adb {
    Write-Section 'Android Debug Bridge (adb)'

    $adb = Get-Command adb -ErrorAction SilentlyContinue
    if (-not $adb) {
        Record fail 'adb on PATH' `
            -Fix 'Download Android Platform Tools (https://developer.android.com/tools/releases/platform-tools), unzip to e.g. %LOCALAPPDATA%\Android\Sdk\platform-tools\, then add that folder to System Properties → Environment Variables → Path. Reopen PowerShell after editing PATH.'
        return
    }
    Record pass 'adb on PATH' -Detail "Found: $($adb.Source)"

    $code, $ver = Invoke-CheckCommand 'adb' @('version')
    if ($code -ne 0) {
        Record fail 'adb runs cleanly' `
            -Detail $ver `
            -Fix 'Antivirus may have quarantined adb.exe. Whitelist the platform-tools folder and try again.'
        return
    }
    $firstLine = ($ver -split "`n")[0]
    Record pass 'adb runs cleanly' -Detail $firstLine

    $code, $devices = Invoke-CheckCommand 'adb' @('devices')
    if ($code -ne 0) {
        Record fail 'adb devices command' -Detail $devices
        return
    }
    # `adb devices` always prints a header; real devices are subsequent lines
    # whose second column is exactly "device" (offline / unauthorized are skipped).
    $live = $devices -split "`n" |
        Select-Object -Skip 1 |
        Where-Object { $_ -match '^\S+\s+device(\s|$)' }

    if (-not $live -or $live.Count -eq 0) {
        Record fail 'At least one ADB device is online' `
            -Detail "adb devices output:`n$devices" `
            -Fix 'Start BlueStacks, then in its Settings → Advanced → Android Debug Bridge → toggle Enabled. ``adb devices`` should then list ``127.0.0.1:5555`` (or similar). If BlueStacks is already running, try ``adb kill-server`` then ``adb start-server``.'
    } else {
        $line = ($live -join '; ').Trim()
        Record pass 'At least one ADB device is online' -Detail $line
    }
}

function Test-BlueStacks {
    Write-Section 'BlueStacks emulator'

    $procs = Get-Process -Name 'BlueStacks*', 'HD-*' -ErrorAction SilentlyContinue
    if (-not $procs) {
        Record warn 'BlueStacks process running' `
            -Detail 'No BlueStacks* / HD-* processes visible.' `
            -Fix 'Launch BlueStacks before the bot starts. Bot will start anyway, but no work will run until an ADB device is online.'
        return
    }
    Record pass 'BlueStacks process running' -Detail "PIDs: $(($procs | Select-Object -ExpandProperty Id) -join ', ')"
}

function Test-RepoLayout {
    Write-Section 'Repository checkout'

    $repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..') -ErrorAction SilentlyContinue
    if (-not $repoRoot) {
        Record fail 'Repo root detectable from doctor.ps1' `
            -Fix 'Run this script from a clone of https://github.com/batazor/whiteout-survival-autopilot, not from a copy of the file alone.'
        return
    }
    Record pass 'Repo root resolved' -Detail $repoRoot

    foreach ($rel in @('docker-compose.prod.yml', 'docker-compose.yml')) {
        $path = Join-Path $repoRoot $rel
        if (Test-Path -LiteralPath $path) {
            Record pass "$rel present"
        } else {
            Record fail "$rel present" `
                -Fix 'You appear to be on a stale checkout. Run ``git pull`` (or re-clone) and try again.'
        }
    }
}

function Test-Ports {
    Write-Section 'Host ports'

    # 5037 = host adb server. 5555 = typical BlueStacks ADB. 6379 / 8000 / 8501
    # = compose-published services — they only listen once `docker compose up`
    # has started, so a missing listen there is informational, not an error.
    $candidates = @(
        @{ Port = 5037; Label = 'adb server (127.0.0.1:5037)'; Required = $true },
        @{ Port = 5555; Label = 'BlueStacks ADB (127.0.0.1:5555)'; Required = $false },
        @{ Port = 6379; Label = 'redis (compose-published, optional)'; Required = $false },
        @{ Port = 8000; Label = 'ocr (compose-published, optional)'; Required = $false },
        @{ Port = 8501; Label = 'bot UI (compose-published, optional)'; Required = $false }
    )

    foreach ($entry in $candidates) {
        $port = $entry.Port
        $label = $entry.Label
        $listening = $null
        try {
            $listening = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
                Where-Object { $_.LocalAddress -in @('127.0.0.1', '::1', '0.0.0.0', '::') }
        } catch {
            # Get-NetTCPConnection not available (older PowerShell) — skip.
        }
        if ($listening) {
            Record pass "$label listening"
        } elseif ($entry.Required) {
            Record fail "$label listening" `
                -Fix 'Run ``adb start-server`` (it lazy-starts on first ``adb`` call). If still missing, ``adb kill-server`` then ``adb start-server``.'
        } else {
            Record warn "$label listening" `
                -Detail 'Not listening yet — expected before ``docker compose -f docker-compose.prod.yml up -d`` has run.'
        }
    }
}

# --- summary ----------------------------------------------------------------

function Write-Summary {
    Write-Section 'Summary'
    $by = $script:Results | Group-Object Status
    $passes = ($by | Where-Object Name -eq 'pass').Count
    $warns  = ($by | Where-Object Name -eq 'warn').Count
    $fails  = ($by | Where-Object Name -eq 'fail').Count
    Write-Host ("  Passed: {0}" -f $passes) -ForegroundColor Green
    if ($warns -gt 0) { Write-Host ("  Warnings: {0}" -f $warns) -ForegroundColor Yellow }
    if ($fails -gt 0) { Write-Host ("  Failed: {0}" -f $fails) -ForegroundColor Red }
    Write-Host ''
    if ($fails -gt 0) {
        Write-Host 'Address each [FAIL] above (follow the → hints), then re-run this script. Once everything is green, start the bot with:' -ForegroundColor Yellow
        Write-Host '  docker compose -f docker-compose.prod.yml up -d' -ForegroundColor White
        exit 1
    } elseif ($warns -gt 0) {
        Write-Host 'You can probably start the bot, but the [WARN] items may bite later:' -ForegroundColor Yellow
        Write-Host '  docker compose -f docker-compose.prod.yml up -d' -ForegroundColor White
        exit 0
    } else {
        Write-Host 'All checks passed. Start the bot with:' -ForegroundColor Green
        Write-Host '  docker compose -f docker-compose.prod.yml up -d' -ForegroundColor White
        Write-Host 'Then open http://127.0.0.1:8501 in your browser.' -ForegroundColor Green
        exit 0
    }
}

# --- entry point ------------------------------------------------------------

Write-Host ''
Write-Host '  Whiteout Survival autopilot — Windows doctor  ' -ForegroundColor White -BackgroundColor DarkBlue
Write-Host ''

Test-Docker
Test-Adb
Test-BlueStacks
Test-RepoLayout
Test-Ports
Write-Summary
