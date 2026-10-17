<#
.SYNOPSIS
  Install dsh-workbench-open into this workbench's DSH profile.

.DESCRIPTION
  Three idempotent steps, all inside the (git-ignored) data directory:

    1. link the plugin package into <DSH_HOME>/profiles/<profile>/node_modules;
    2. add it to that profile's package.json dependencies and bundle list;
    3. disable the built-in desktop "Open In..." action in the profile patch.

  Nothing outside the workbench data directory is touched, and running it twice
  changes nothing.  Restart DSH afterwards (`-Restart` does it through
  restart-workbench.ps1).

  The script is ASCII-only on purpose: Windows PowerShell 5.1 reads a script
  without a BOM as ANSI, and non-ASCII text would break parsing there.

.EXAMPLE
  .\install-dsh-workbench-open.ps1
  .\install-dsh-workbench-open.ps1 -Restart
#>
param(
    [string]$DshHome = "",
    [string]$Profile = "web",
    [switch]$Restart
)

$ErrorActionPreference = 'Stop'
$pluginDir = Join-Path $PSScriptRoot 'dsh-workbench-open'
$repo = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $pluginDir 'package.json'))) {
    throw "expected the plugin package at $pluginDir"
}

if (-not $DshHome) {
    if ($env:DSH_HOME) { $DshHome = $env:DSH_HOME }
    elseif (Test-Path -LiteralPath (Join-Path $repo 'data\dsh-home')) {
        $DshHome = Join-Path $repo 'data\dsh-home'
    }
}
if (-not $DshHome) { throw "DSH_HOME not found; pass -DshHome <dsh data dir>." }

$profileDir = Join-Path $DshHome ("profiles\" + $Profile)
$packageFile = Join-Path $profileDir 'package.json'
if (-not (Test-Path -LiteralPath $packageFile)) {
    throw "$profileDir is not a DSH profile (no package.json)."
}

# 1. package link -------------------------------------------------------
$modules = Join-Path $profileDir 'node_modules'
New-Item -ItemType Directory -Force -Path $modules | Out-Null
$link = Join-Path $modules 'dsh-workbench-open'
if (Test-Path -LiteralPath $link) {
    $item = Get-Item -LiteralPath $link -Force
    if ($item.LinkType) { $item.Delete() } else { Remove-Item -LiteralPath $link -Recurse -Force }
}
New-Item -ItemType Junction -Path $link -Target $pluginDir | Out-Null
Write-Host "linked: $link -> $pluginDir"

# 2. package.json -------------------------------------------------------
$package = Get-Content -LiteralPath $packageFile -Raw | ConvertFrom-Json
$spec = 'link:' + ($pluginDir -replace '\\', '/')
if (-not $package.dependencies) {
    $package | Add-Member -NotePropertyName dependencies -NotePropertyValue ([pscustomobject]@{})
}
if (-not $package.dependencies.PSObject.Properties['dsh-workbench-open']) {
    $package.dependencies | Add-Member -NotePropertyName 'dsh-workbench-open' -NotePropertyValue $spec
    Write-Host "dependency added: dsh-workbench-open = $spec"
} elseif ($package.dependencies.'dsh-workbench-open' -ne $spec) {
    $package.dependencies.'dsh-workbench-open' = $spec
    Write-Host "dependency updated: dsh-workbench-open = $spec"
}
$bundles = @()
if ($package.dsh -and $package.dsh.profile -and $package.dsh.profile.bundles) {
    $bundles = @($package.dsh.profile.bundles)
}
if ($bundles -notcontains 'dsh-workbench-open') {
    $bundles += 'dsh-workbench-open'
    $package.dsh.profile.bundles = $bundles
    Write-Host "bundle added: dsh-workbench-open"
}
($package | ConvertTo-Json -Depth 12) + "`n" | Set-Content -LiteralPath $packageFile -NoNewline -Encoding utf8

# 3. profile patch ------------------------------------------------------
$patchFile = Join-Path $profileDir 'cordis.patch.yml'
$patch = if (Test-Path -LiteralPath $patchFile) { Get-Content -LiteralPath $patchFile -Raw } else { "" }
if ($patch -notmatch 'dsh-workbench-open') {
    $patch = $patch.TrimEnd() + @"

# dsh-workbench-open: the session header opens the workbench file browser and
# VS Code in a tab; the built-in local Open In... action is retired.
- id: open-in-app
  disabled: true
- id: ui-open-in-app
  disabled: true
"@ + "`n"
    Set-Content -LiteralPath $patchFile -Value $patch -NoNewline -Encoding utf8
    Write-Host "patch updated: $patchFile"
}
Write-Host "done. Restart DSH for it to take effect (or pass -Restart)."

if ($Restart) {
    & (Join-Path $repo 'restart-workbench.ps1') -Quiet
}
