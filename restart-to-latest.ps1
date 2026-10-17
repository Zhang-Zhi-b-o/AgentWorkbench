<#
.SYNOPSIS
  Stop the old workbench, carry its conversations over, and start this one.

.DESCRIPTION
  One command for the cutover:

    1. stop the old `pythonw -m agenthub`, its DSH child and its serve-web;
    2. wait until ports 8768 / 3080 / 8799 are actually free;
    3. run sync-data.ps1 -- which must happen *after* stopping, because the
       session files keep being appended to while the old host still serves;
    4. start this workbench.

  Run it from a plain PowerShell window, NOT from a terminal hosted by the old
  workbench: step 1 kills the web-based VS Code and the AI chat that would be
  hosting your shell.

.PARAMETER OldData
  Data directory of the old installation.

.PARAMETER Port
  Port to start the new workbench on.  Default 8768, the same entry as before.

.EXAMPLE
  .\restart-to-latest.ps1
  .\restart-to-latest.ps1 -Port 8790 -NoBrowser
#>
param(
    [string]$OldData = 'D:\Apps\Tools\agent-hub\data',
    [int]$Port = 8768,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'

function Stop-OldStack {
    $targets = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Name -eq 'pythonw.exe' -and $_.CommandLine -like '*-m agenthub*') -or
            ($_.Name -eq 'python.exe'  -and $_.CommandLine -like '*-m agenthub*') -or
            ($_.Name -eq 'node.exe'    -and $_.CommandLine -like '*@deepseek-ai\dsh*bin.js*') -or
            ($_.Name -eq 'code-tunnel.exe' -and $_.CommandLine -like '*serve-web*')
        }
    if (-not $targets) {
        Write-Host '旧服务没有在运行，跳过停止。'
        return
    }
    foreach ($process in $targets) {
        Write-Host "  停止 PID $($process.ProcessId) $($process.Name)"
        Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

function Wait-FreePort([int[]]$Ports, [int]$Seconds = 20) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        $busy = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $Ports -contains $_.LocalPort }
        if (-not $busy) { return $true }
        Start-Sleep -Milliseconds 400
    }
    return $false
}

Write-Host '== 1/4 停止旧工作台 =='
Stop-OldStack

Write-Host '== 2/4 等待端口释放（8768 / 3080 / 8799）=='
if (-not (Wait-FreePort @(8768, 3080, 8799))) {
    $busy = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { @(8768, 3080, 8799) -contains $_.LocalPort } |
        Select-Object LocalPort, OwningProcess
    Write-Warning '端口仍被占用，可能是别的程序，不是旧工作台：'
    $busy | Format-Table -AutoSize
    throw '请先处理上面的端口占用，或换个 -Port 重跑。'
}
Write-Host '  已释放'

Write-Host '== 3/4 搬运对话与运行环境 =='
& (Join-Path $PSScriptRoot 'sync-data.ps1') -Source $OldData

Write-Host '== 4/4 启动新工作台 =='
& (Join-Path $PSScriptRoot 'start.ps1') -Port $Port -NoBrowser:$NoBrowser
