<#
.SYNOPSIS
  Start the workbench in the background and open it in the default browser.

.DESCRIPTION
  Idempotent: if a healthy workbench already answers on the port, it just opens
  the browser instead of starting a second one.  Two hosts fighting for the same
  port is the failure this launcher exists to prevent.

.EXAMPLE
  .\start.ps1                      # normal start on 8768
  .\start.ps1 -Port 8790           # different port (e.g. while the old build still owns 8768)
  .\start.ps1 -NoBrowser           # start only
  .\start.ps1 -Foreground          # see the log in this window; Ctrl+C to stop
#>
param(
    [int]$Port = 0,
    [switch]$NoBrowser,
    [switch]$Foreground
)

$ErrorActionPreference = 'Stop'

if ($Port -eq 0) {
    $Port = if ($env:WORKBENCH_PORT) { [int]$env:WORKBENCH_PORT } else { 8768 }
}
$url = "http://127.0.0.1:$Port"
$log = Join-Path $env:TEMP 'workbench'

function Test-Workbench {
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
        return ($health.ok -eq $true -and $health.service -eq 'workbench')
    } catch { return $false }
}

function Get-PortOwner {
    try { return (Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2).service } catch { return $null }
}

if (Test-Workbench) {
    Write-Host "workbench 已在 $url 运行"
    if (-not $NoBrowser) { Start-Process $url }
    return
}

# Something else answering on our port: say who, instead of failing at bind time.
$owner = Get-PortOwner
if ($owner) {
    throw "端口 $Port 上跑的是 '$owner'，不是 workbench。用 -Port 换一个端口（例如 -Port 8790），或者先把它停掉。"
}

$env:WORKBENCH_PORT = "$Port"
$python = (Get-Command python -ErrorAction Stop).Source

if ($Foreground) {
    Write-Host "workbench 前台运行在 $url；Ctrl+C 停止"
    & $python -m workbench
    return
}

$windowless = Join-Path (Split-Path $python) 'pythonw.exe'
if (Test-Path -LiteralPath $windowless) { $python = $windowless }
$process = Start-Process -FilePath $python -ArgumentList @('-m', 'workbench') `
    -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput "$log.stdout.log" -RedirectStandardError "$log.stderr.log"

$deadline = [DateTime]::UtcNow.AddSeconds(20)
while (-not (Test-Workbench)) {
    if ([DateTime]::UtcNow -ge $deadline -or $process.HasExited) {
        throw "启动失败。日志：$log.stderr.log"
    }
    Start-Sleep -Milliseconds 400
    $process.Refresh()
}

Write-Host "workbench 已启动：$url（日志 $log.stdout.log）"
if (-not $NoBrowser) { Start-Process $url }
