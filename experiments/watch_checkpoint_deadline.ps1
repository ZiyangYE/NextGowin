param(
    [Parameter(Mandatory=$true)][string]$CheckpointRoot,
    [Parameter(Mandatory=$true)][int]$WorkerProcessId
)
$ErrorActionPreference = 'Stop'
$workspace = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$checkpoint = (Resolve-Path -LiteralPath $CheckpointRoot).Path
if (-not $checkpoint.StartsWith($workspace + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Checkpoint must be inside this workspace'
}
$expectedWorker = Join-Path $workspace 'binary_audit/flow/runtime/bin/python.exe'
$tracked = Get-Process -Id $WorkerProcessId
if (-not [StringComparer]::OrdinalIgnoreCase.Equals($tracked.Path, $expectedWorker)) {
    throw 'Refuse to track a process outside the checked native worker runtime'
}
$workerStart = $tracked.StartTime.ToUniversalTime()
$started = (Get-Item -LiteralPath $checkpoint).CreationTimeUtc
$deadline = $started.AddSeconds(3595)
$record = [ordered]@{
    status = 'running'
    scope = 'Prepared checkpoint only; wall time includes preparation and waiting, not original RTL synthesis.'
    started_utc = $started.ToString('o')
    deadline_utc = $deadline.ToString('o')
    worker_pid = $WorkerProcessId
    worker_started_utc = $workerStart.ToString('o')
    worker_path = $expectedWorker
}
$report = Join-Path $checkpoint 'checkpoint_budget.json'
$record | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding utf8
while ([DateTime]::UtcNow -lt $deadline) {
    $native = $null
    try { $native = Get-Content -LiteralPath (Join-Path $checkpoint '06_pnr/manifest.json') -Raw | ConvertFrom-Json } catch {}
    if ($native -and $native.status -in @('complete','failed','timeout')) {
        $record.status = $native.status
        break
    }
    $current = Get-Process -Id $WorkerProcessId -ErrorAction SilentlyContinue
    if (-not $current -or $current.StartTime.ToUniversalTime() -ne $workerStart) {
        $record.status = 'worker_exited_awaiting_report'
        break
    }
    Start-Sleep -Seconds 1
}
if ($record.status -eq 'running') {
    $current = Get-Process -Id $WorkerProcessId -ErrorAction SilentlyContinue
    if ($current -and $current.StartTime.ToUniversalTime() -eq $workerStart -and
        [StringComparer]::OrdinalIgnoreCase.Equals($current.Path, $expectedWorker)) {
        Stop-Process -InputObject $current -Force
    }
    $record.status = 'timeout'
    $record.reason = 'One-hour checkpoint wall budget exhausted, including preparation and waiting.'
}
$record.wall_seconds = ([DateTime]::UtcNow - $started).TotalSeconds
$record.within_hour = $record.wall_seconds -le 3600
$record | ConvertTo-Json | Set-Content -LiteralPath $report -Encoding utf8
$record | ConvertTo-Json
