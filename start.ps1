# Start the workbench in the background and open it in the default browser.
# Safe to double-click-run: it reuses a healthy instance instead of starting a
# second one, because two hosts fighting for the same port is the one failure
# this launcher must avoid.
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$port = if ($env:WORKBENCH_PORT) { [int]$env:WORKBENCH_PORT } else { 8768 }
$url = "http://127.0.0.1:$port"
$log = Join-Path $env:TEMP 'workbench'

function Test-Workbench {
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
        return ($health.ok -eq $true -and $health.service -eq 'workbench')
    } catch { return $false }
}

if (-not (Test-Workbench)) {
    $python = (Get-Command python -ErrorAction Stop).Source
    $windowless = Join-Path (Split-Path $python) 'pythonw.exe'
    if (Test-Path -LiteralPath $windowless) { $python = $windowless }
    $process = Start-Process -FilePath $python -ArgumentList @('-m', 'workbench') `
        -WorkingDirectory $root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput "$log.stdout.log" -RedirectStandardError "$log.stderr.log"
    $deadline = [DateTime]::UtcNow.AddSeconds(20)
    while (-not (Test-Workbench)) {
        if ([DateTime]::UtcNow -ge $deadline -or $process.HasExited) {
            throw "启动失败，请查看 $log.stderr.log"
        }
        Start-Sleep -Milliseconds 400
        $process.Refresh()
    }
}
Start-Process $url
