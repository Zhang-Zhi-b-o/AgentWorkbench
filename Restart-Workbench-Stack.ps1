# Restart (or start) the workbench stack: the portal on 8768 and the DSH web
# host on 3080 that the portal owns. The desktop shortcut "Workbench" runs this
# through Restart-Workbench-Stack.vbs, so a double-click is enough.
#
# The portal supervises DSH (plugins/agents/__init__.py): DSH is a child process
# started on demand by GET /agents/ or POST /api/agents/start. So a clean restart
# is: stop DSH, stop the portal, start the portal, ask it to start DSH again.
#
# Usage:
#   .\Restart-Workbench-Stack.ps1                 # restart both, open /agents/
#   .\Restart-Workbench-Stack.ps1 -StartOnly      # start what is missing only
#   .\Restart-Workbench-Stack.ps1 -NoBrowser
#   .\Restart-Workbench-Stack.ps1 -DelaySeconds 20
#
# ASCII-only on purpose: Windows PowerShell 5.1 reads a script without a BOM as
# ANSI, and non-ASCII text would break parsing there.
param(
    [int]$PortalPort = 8768,
    [int]$DshPort = 3080,
    [switch]$StartOnly,
    [switch]$NoBrowser,
    [int]$DelaySeconds = 0
)

$ErrorActionPreference = 'Stop'

$root = 'D:\Apps\Tools\AgentWorkbench'
$logDir = Join-Path $env:TEMP 'workbench'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir 'launcher.log'

function Write-Step([string]$message) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $logFile -Value "[$stamp] $message"
}

function Get-Listener([int]$port) {
    return Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Test-Portal {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$PortalPort/api/health" -TimeoutSec 2
        return ($health.ok -eq $true -and $health.service -eq 'workbench')
    } catch {
        return $false
    }
}

function Test-Dsh {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$DshPort/" -TimeoutSec 3 -UseBasicParsing
        return ($response.StatusCode -eq 200)
    } catch {
        if ($_.Exception.Response -ne $null) { return ($_.Exception.Response.StatusCode.value__ -eq 401) }
        return $false
    }
}

function Stop-Port([int]$port, [string]$label) {
    $listener = Get-Listener $port
    if ($null -eq $listener) {
        Write-Step "$label not running on $port"
        return
    }
    Write-Step "stopping $label PID $($listener.OwningProcess) on $port"
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        if ($null -eq (Get-Listener $port)) { break }
        Start-Sleep -Milliseconds 250
    }
}

function Wait-Portal([int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Portal) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Wait-Dsh([int]$seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        if (Test-Dsh) { return $true }
        Start-Sleep -Seconds 1
    }
    return $false
}

function Start-Portal {
    $restart = Join-Path $root 'Restart-Workbench.ps1'
    & $restart -Port $PortalPort -Quiet -NoBrowser
    Write-Step 'portal start requested through Restart-Workbench.ps1'
}

# The portal owns DSH: this is the same call its /agents/ page makes.
function Start-Agents {
    try {
        $state = Invoke-RestMethod -Uri "http://127.0.0.1:$PortalPort/api/agents/start" -Method Post -Headers @{ 'X-Workbench' = '1' } -TimeoutSec 120
        Write-Step ("agents start reported ready={0} running={1}" -f $state.ready, $state.running)
        if (-not $state.ready) { Write-Step ("agents startup error: {0}" -f $state.error) }
        return ($state.ready -eq $true)
    } catch {
        Write-Step "agents start call failed: $($_.Exception.Message)"
        return $false
    }
}

if ($DelaySeconds -gt 0) {
    Write-Step "stack restart requested; waiting ${DelaySeconds}s"
    Start-Sleep -Seconds $DelaySeconds
}

$portalOk = Test-Portal
$dshOk = Test-Dsh

if ($StartOnly) {
    Write-Step ("start-only mode (portal={0} dsh={1})" -f $portalOk, $dshOk)
    if (-not $portalOk) {
        Start-Portal
        $portalOk = Wait-Portal 30
    }
    if ($portalOk -and -not $dshOk) {
        $dshOk = Start-Agents
        if ($dshOk) { $dshOk = Wait-Dsh 60 }
    }
} else {
    Stop-Port $DshPort 'DSH web'
    $dshOk = $false
    Stop-Port $PortalPort 'workbench portal'
    Start-Sleep -Seconds 1
    Start-Portal
    $portalOk = Wait-Portal 40
    if ($portalOk) {
        $dshOk = Start-Agents
        if ($dshOk) { $dshOk = Wait-Dsh 90 }
    }
}

if (-not $portalOk) { Write-Step 'portal did not answer on /api/health; see %TEMP%\workbench.stderr.log' }
if (-not $dshOk) { Write-Step "DSH did not become ready on $DshPort; see launcher.log and %TEMP%\workbench.stderr.log" }

if ($portalOk -and -not $NoBrowser) { Start-Process "http://127.0.0.1:$PortalPort/agents/" }
if ($portalOk -and $dshOk) { Write-Step 'workbench stack is up' } else { exit 1 }
