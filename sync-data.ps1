<#
.SYNOPSIS
  Bring the DSH conversations (and the runtime they need) into this checkout.

.DESCRIPTION
  The AI 对话 plugin reads its sessions from $WORKBENCH_HOME/dsh-home, so moving
  the workbench means moving that directory -- otherwise the new shell opens with
  an empty chat list.

  The copy is not a plain file copy: dsh-home contains ~500 directory junctions
  (profiles/*/node_modules pointing into dsh-runtime).  Following them explodes
  690 MB into gigabytes, so they are skipped during the copy and then recreated
  here with their targets rewritten to the new runtime location.

  Re-runnable and safe to run repeatedly: it refreshes changed files and rebuilds
  the junctions.  Run it right before switching over, because the conversations
  keep changing while the old workbench is still serving.

.PARAMETER Source
  Data directory of the old installation.

.PARAMETER SkipRuntime
  Do not copy dsh-runtime (~937 MB).  Use when the new checkout already has it
  and only the conversations are stale.

.EXAMPLE
  .\sync-data.ps1
  .\sync-data.ps1 -SkipRuntime
#>
param(
    [string]$Source = 'D:\Apps\Tools\agent-hub\data',
    [switch]$SkipRuntime,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$newRoot = Join-Path $PSScriptRoot 'data'
$oldRoot = (Resolve-Path -LiteralPath $Source -ErrorAction Stop).Path

if ($oldRoot -eq (Resolve-Path -LiteralPath $newRoot -ErrorAction SilentlyContinue).Path) {
    throw '源目录就是目标目录，不需要同步。'
}

# Warn when the old stack is still writing: the copy may catch a half-written
# session file.  It is not fatal, and running it again after stopping is cheap.
$running = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*@deepseek-ai*dsh*' }
if ($running -and -not $Force) {
    Write-Warning "检测到旧的 DSH 进程仍在运行（PID $($running.ProcessId -join ', ')）。"
    Write-Warning '对话文件正在被写入，拷到的可能是写到一半的状态。建议先停旧服务，或加 -Force 继续。'
}

New-Item -ItemType Directory -Force -Path $newRoot | Out-Null

$dirs = @('dsh-home')
if (-not $SkipRuntime) { $dirs = @('dsh-runtime') + $dirs }

foreach ($name in $dirs) {
    $src = Join-Path $oldRoot $name
    $dst = Join-Path $newRoot $name
    if (-not (Test-Path -LiteralPath $src)) {
        Write-Warning "跳过 $name：$src 不存在"
        continue
    }
    Write-Host "同步 $name ..."
    # /XJ: never follow junctions (they are rebuilt below).
    # /MIR: mirror, so deletions and edits both come across.
    robocopy $src $dst /MIR /XJ /NFL /NDL /NJH /NJS /MT:16 /R:1 /W:1 | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $name 失败（exit $LASTEXITCODE）" }
}

# Recreate the junctions with their targets repointed at this checkout.
Write-Host '重建目录联接 ...'
$made = 0
$skipped = 0
Get-ChildItem -Recurse -Directory -Force (Join-Path $oldRoot 'dsh-home') -ErrorAction SilentlyContinue |
    Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
    ForEach-Object {
        $relative = $_.FullName.Substring((Join-Path $oldRoot 'dsh-home').Length).TrimStart('\')
        $target = [string]$_.Target
        if ([string]::IsNullOrWhiteSpace($target)) { $skipped++; return }
        if (-not [IO.Path]::IsPathRooted($target)) {
            $target = [IO.Path]::GetFullPath((Join-Path (Split-Path $_.FullName -Parent) $target))
        }
        if ($target.StartsWith($oldRoot, [StringComparison]::OrdinalIgnoreCase)) {
            $target = $newRoot + $target.Substring($oldRoot.Length)
        }
        if (-not (Test-Path -LiteralPath $target)) { $skipped++; return }
        $dest = Join-Path (Join-Path $newRoot 'dsh-home') $relative
        if (Test-Path -LiteralPath $dest) {
            $item = Get-Item -LiteralPath $dest -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                [IO.Directory]::Delete($dest, $false)
            } else {
                Remove-Item -LiteralPath $dest -Recurse -Force
            }
        }
        New-Item -ItemType Junction -Path $dest -Target $target -ErrorAction Stop | Out-Null
        $made++
    }

$sessions = Join-Path $newRoot 'dsh-home\sessions'
$count = if (Test-Path $sessions) {
    (Get-ChildItem -Recurse -File $sessions -ErrorAction SilentlyContinue | Measure-Object).Count
} else { 0 }
$latest = if (Test-Path $sessions) {
    (Get-ChildItem -Recurse -File $sessions -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
} else { $null }

Write-Host ''
Write-Host "联接：新建 $made，跳过 $skipped"
Write-Host "会话：$count 个文件，最新一次写入 $latest"
Write-Host "数据目录：$newRoot"
Write-Host '旧目录里还有 data\hub.sqlite3（旧版对话与任务历史，新工作台不读取它）。'
Write-Host '如果要留档：Copy-Item "$oldRoot\hub.sqlite3" "$env:USERPROFILE\Desktop\hub.sqlite3.bak"'
