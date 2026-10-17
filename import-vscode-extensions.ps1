<#
.SYNOPSIS
    Import the extensions installed in the desktop VS Code into the workbench's
    VS Code (the ``code serve-web`` instance the vscode plugin supervises).

.DESCRIPTION
    The workbench runs ``code serve-web`` with its own server data directory, so it
    starts with its own extension set.  This script mirrors the desktop's
    ``~/.vscode/extensions`` into ``<workbench home>/vscode-server/extensions``.

    Two names are deliberately not copied:

    * ``extensions.json`` -- that is the *desktop's* manifest and its entries point
      at ``%USERPROFILE%\.vscode\extensions``.  Letting the server inherit it makes
      the import depend on the desktop folder, which breaks as soon as an extension
      is updated or removed there.  The server rescans its own directory on the
      next window and writes a correct manifest itself.
    * ``.obsolete`` plus every directory it names -- stale versions the desktop no
      longer loads either.

    Extensions with native binaries work because the extension host runs in Node on
    this same machine (``--type=extensionHost``); they are Windows builds, so this
    is a Windows-to-Windows import.  An extension that insists on a desktop-only API
    reports its own errors in the extension host log; the rest keep working.

.PARAMETER Source
    The desktop extension directory.  Defaults to ``%USERPROFILE%\.vscode\extensions``.

.PARAMETER DataHome
    The workbench data home.  Defaults to ``WORKBENCH_HOME`` or ``<repo>\data``.

.PARAMETER Restart
    Restart the workbench afterwards so the new set is picked up immediately.
    Without it the server picks the extensions up when it next starts.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File import-vscode-extensions.ps1 -Restart
#>
[CmdletBinding()]
param(
    [string] $Source = (Join-Path $env:USERPROFILE '.vscode\extensions'),
    [string] $DataHome = '',
    [switch] $Restart
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $Source)) {
    throw "Desktop extension directory not found: $Source"
}

if (-not $DataHome) {
    $DataHome = $env:WORKBENCH_HOME
    if (-not $DataHome) { $DataHome = Join-Path $PSScriptRoot 'data' }
}
$target = Join-Path $DataHome 'vscode-server\extensions'
New-Item -ItemType Directory -Force -Path $target | Out-Null

# Stale versions the desktop has already superseded; it does not load them either.
$obsolete = @()
$marker = Join-Path $Source '.obsolete'
if (Test-Path -LiteralPath $marker) {
    try {
        $parsed = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        $obsolete = @($parsed.PSObject.Properties | Where-Object { $_.Value } | ForEach-Object { $_.Name })
    } catch {
        Write-Warning "Could not parse $marker; treating every directory as current."
    }
}

$directories = Get-ChildItem -LiteralPath $Source -Directory |
    Where-Object { $obsolete -notcontains $_.Name }
Write-Host ("Source: {0} ({1} extensions, skipping {2} obsolete)" -f $Source, $directories.Count, $obsolete.Count)
Write-Host ("Target: {0}" -f $target)

# /E copies subdirectories including empty ones.  The excluded names keep the
# server's own manifest and the desktop's bookkeeping file out of the import.
$robocopyArgs = @($Source, $target, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/R:1', '/W:1',
                  '/XF', 'extensions.json', '.obsolete')
foreach ($name in $obsolete) { $robocopyArgs += @('/XD', (Join-Path $Source $name)) }

$output = & robocopy @robocopyArgs
$code = $LASTEXITCODE

# robocopy exit codes are a bitmask: 0 nothing to do, 1 copied, 2 extras seen,
# 4 mismatches, 8 failures, 16 fatal.  Anything below 8 is a success.
if ($code -ge 16) {
    $output | Select-Object -Last 15 | ForEach-Object { Write-Host $_ }
    throw "robocopy failed with exit code $code"
}

$errors = @($output | Where-Object { $_ -match 'ERROR \d+' })
$locked = @($errors | Where-Object { $_ -match 'ERROR (32|33) ' })
if ($errors.Count -and $locked.Count -eq $errors.Count) {
    # A running desktop VS Code keeps its own binaries open.  The target already
    # holds a copy from an earlier run, so this only needs mentioning.
    Write-Warning ("{0} file(s) are in use and were left as they are (close the desktop VS Code and re-run to refresh them)." -f $errors.Count)
} elseif ($code -ge 8) {
    $output | Select-Object -Last 15 | ForEach-Object { Write-Host $_ }
    throw "robocopy reported problems with exit code $code"
}

$installed = (Get-ChildItem -LiteralPath $target -Directory).Count
$sizeMb = [math]::Round(((Get-ChildItem -LiteralPath $target -Recurse -File -ErrorAction SilentlyContinue |
    Measure-Object -Sum Length).Sum / 1MB), 0)
Write-Host ("Done: {0} extensions in the target, about {1} MB" -f $installed, $sizeMb)

if ($Restart) {
    $restarter = Join-Path $PSScriptRoot 'restart-workbench.ps1'
    if (Test-Path -LiteralPath $restarter) {
        Write-Host 'Restarting the workbench...'
        & powershell -ExecutionPolicy Bypass -File $restarter
    } else {
        Write-Warning "No $restarter found; restart the workbench yourself."
    }
} else {
    Write-Host 'Reopen the workbench VS Code tab to see them, or pass -Restart to do it now.'
}
