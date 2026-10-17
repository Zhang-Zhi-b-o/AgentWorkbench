<#
.SYNOPSIS
  Publish the workbench on the tailnet, once.

.DESCRIPTION
  Runs the two `tailscale serve` mappings the workbench needs and writes the
  resulting origin into remote-access.json, so the host accepts requests that
  arrive through the tailnet instead of only from loopback.

    8769 -> 127.0.0.1:8768   the workbench itself (shell, zotero, editor)
    8770 -> 127.0.0.1:3080   the agents plugin (DSH has no base-path option, so
                             it needs an origin of its own)

  Needs an elevated shell: `tailscale serve` changes machine-wide settings.
  Re-running it is safe; it replaces the same two mappings.

.EXAMPLE
  .\setup-remote.ps1
  .\setup-remote.ps1 -HostPort 9443 -AgentsPort 9444
  .\setup-remote.ps1 -SkipAgents        # if you do not use the agents plugin
#>
param(
    [int]$HostPort = 8769,
    [int]$AgentsPort = 8770,
    [int]$WorkbenchPort = 8768,
    [int]$DshPort = 3080,
    [switch]$SkipAgents
)

$ErrorActionPreference = 'Stop'

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    throw '需要管理员权限：tailscale serve 改的是全机设置。请用“以管理员身份运行”的 PowerShell 重跑本脚本。'
}

$tail = (Get-Command tailscale -ErrorAction SilentlyContinue)
if (-not $tail) { throw '找不到 tailscale 命令。请先安装并登录 Tailscale，再重跑本脚本。' }

Write-Host "publishing 127.0.0.1:$WorkbenchPort as https/$HostPort ..."
& $tail.Source serve --bg --https=$HostPort "http://127.0.0.1:$WorkbenchPort"
if ($LASTEXITCODE -ne 0) { throw "tailscale serve 失败（exit $LASTEXITCODE）" }

if (-not $SkipAgents) {
    Write-Host "publishing 127.0.0.1:$DshPort as https/$AgentsPort ..."
    & $tail.Source serve --bg --https=$AgentsPort "http://127.0.0.1:$DshPort"
    if ($LASTEXITCODE -ne 0) { throw "tailscale serve 失败（exit $LASTEXITCODE）" }
}

& $tail.Source serve status

# Record the origin the host must accept, so the page works from a phone.
$state = & $tail.Source status --json | ConvertFrom-Json
$dns = $state.Self.DNSName
if (-not $dns) { throw 'tailscale status 没有返回本机 DNS 名；请确认已经 tailscale up。' }
$origin = "https://$($dns.TrimEnd('.')):$HostPort"

$home = if ($env:WORKBENCH_HOME) { $env:WORKBENCH_HOME } else { Join-Path $PSScriptRoot 'data' }
New-Item -ItemType Directory -Force -Path $home | Out-Null
$file = Join-Path $home 'remote-access.json'
@{ origin = $origin } | ConvertTo-Json -Compress | Set-Content -Path $file -Encoding utf8

Write-Host ''
Write-Host "remote-access.json -> $file"
Write-Host "手机打开： $origin/"
Write-Host '提示：修改后需要重启工作台（python -m workbench）才会重新读取该文件。'
