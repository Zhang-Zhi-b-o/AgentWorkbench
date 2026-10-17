<#
.SYNOPSIS
  Bring an existing installation's DSH conversations (and runtime) into this checkout.

.DESCRIPTION
  The AI 对话 plugin reads its sessions from $WORKBENCH_HOME/dsh-home, so moving the
  workbench means moving that directory -- otherwise the new shell opens with an
  empty chat list.

  The copy is not a plain file copy: dsh-home contains several hundred directory
  junctions (profiles/*/node_modules pointing into dsh-runtime).  Following them
  inflates 690 MB into gigabytes, so they are skipped during the copy and then
  recreated here with their targets rewritten to this checkout.

  Re-runnable: it mirrors changes and rebuilds the junctions.  Run it after
  stopping the old host, because the session files keep changing while it serves.

.PARAMETER Source
  Data directory of the old installation, for example:  -Source D:\old\data

.PARAMETER SkipRuntime
  Only refresh the conversations; do not mirror dsh-runtime.

.EXAMPLE
  .\sync-data.ps1 -Source 'D:\old\data'
  .\sync-data.ps1 -Source 'D:\old\data' -SkipRuntime
#>
param(
    [string]$Source = '',
    [switch]$SkipRuntime,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

if (-not $Source) {
    throw '请用 -Source 指定旧安装的数据目录，例如：.\sync-data.ps1 -Source D:\旧目录\data'
}
if (-not (Test-Path -LiteralPath $Source)) {
    throw "找不到源目录：$Source"
}

$newRoot = Join-Path $PSScriptRoot 'data'
$oldRoot = (Resolve-Path -LiteralPath $Source).Path
if ($oldRoot -eq (Resolve-Path -LiteralPath $newRoot -ErrorAction SilentlyContinue).Path) {
    throw '源目录就是目标目录，不需要同步。'
}

$running = Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*@deepseek-ai*dsh*' }
if ($running -and -not $Force) {
    Write-Warning "检测到 DSH 进程仍在运行（PID $($running.ProcessId -join ', ')）"
    Write-Warning '会话文件正在被写入，拷到的可能是写到一半的状态。建议先停旧服务，或加 -Force 继续。'
}

New-Item -ItemType Directory -Force -Path $newRoot | Out-Null

$names = @('dsh-home')
if (-not $SkipRuntime) { $names = @('dsh-runtime') + $names }

foreach ($name in $names) {
    $from = Join-Path $oldRoot $name
    $to = Join-Path $newRoot $name
    if (-not (Test-Path -LiteralPath $from)) {
        Write-Warning "跳过 $name：$from 不存在"
        continue
    }
    Write-Host "同步 $name ..."
    robocopy $from $to /MIR /XJ /NFL /NDL /NJH /NJS /MT:16 /R:1 /W:1 | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy $name 失败（exit $LASTEXITCODE）" }
}

Write-Host '重建目录联接 ...'
$oldHome = Join-Path $oldRoot 'dsh-home'
$newHome = Join-Path $newRoot 'dsh-home'
$made = 0
$skipped = 0

if (Test-Path -LiteralPath $oldHome) {
    Get-ChildItem -Recurse -Directory -Force $oldHome -ErrorAction SilentlyContinue |
        Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint } |
        ForEach-Object {
            $relative = $_.FullName.Substring($oldHome.Length).TrimStart('\')
            $target = [string]$_.Target
            if ([string]::IsNullOrWhiteSpace($target)) { $skipped++; return }
            if (-not [IO.Path]::IsPathRooted($target)) {
                $target = [IO.Path]::GetFullPath((Join-Path (Split-Path $_.FullName -Parent) $target))
            }
            if ($target.StartsWith($oldRoot, [StringComparison]::OrdinalIgnoreCase)) {
                $target = $newRoot + $target.Substring($oldRoot.Length)
            }
            if (-not (Test-Path -LiteralPath $target)) { $skipped++; return }
            $dest = Join-Path $newHome $relative
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
}

$sessions = Join-Path $newHome 'sessions'
$count = 0
$latest = $null
if (Test-Path -LiteralPath $sessions) {
    $files = Get-ChildItem -Recurse -File $sessions -ErrorAction SilentlyContinue
    $count = ($files | Measure-Object).Count
    $latest = ($files | Sort-Object LastWriteTime -Descending | Select-Object -First 1).LastWriteTime
}

Write-Host ''
Write-Host "联接：新建 $made，跳过 $skipped"
Write-Host "会话：$count 个文件，最新一次写入 $latest"
Write-Host "数据目录：$newRoot"
Write-Host ''
Write-Host '旧目录里的 hub.sqlite3 是旧版对话与任务历史，新工作台不读取它。'
Write-Host '要留档就先复制出去：Copy-Item "<旧目录>\hub.sqlite3" "$env:USERPROFILE\Desktop\hub.sqlite3.bak"'
