<#
.SYNOPSIS
  Restart the workbench host (and, with it, the DSH instance it supervises).

.DESCRIPTION
  Stops whatever owns the workbench port, then starts the checkout again through
  start.ps1.  DSH is a child of the host, so stopping the host takes the agent
  service down too; the next visit to /agents/ starts a fresh one.

  ASCII-only on purpose: Windows PowerShell 5.1 reads a script without a BOM as
  ANSI, and non-ASCII text would break parsing there.

.EXAMPLE
  .\restart-workbench.ps1
  .\restart-workbench.ps1 -Port 8790 -Quiet
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'
if ($Port -eq 0) {
    $Port = if ($env:WORKBENCH_PORT) { [int]$env:WORKBENCH_PORT } else { 8768 }
}

function Write-Step([string]$text) { if (-not $Quiet) { Write-Host $text } }

$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($listener) {
    $owner = Get-Process -Id $listener.OwningProcess -ErrorAction SilentlyContinue
    Write-Step ("stopping {0} (pid {1}) on port {2}" -f $owner.ProcessName, $listener.OwningProcess, $Port)
    Stop-Process -Id $listener.OwningProcess -Force
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (-not (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
}

$start = Join-Path $PSScriptRoot 'start.ps1'
$arguments = @{ Port = $Port }
if ($NoBrowser -or $Quiet) { $arguments['NoBrowser'] = $true }
Write-Step "starting $start"
& $start @arguments
