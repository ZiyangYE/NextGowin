param([ValidateSet('run','check')][string]$Mode='run')
$ErrorActionPreference='Stop'
$reproRoot=Split-Path -Parent $PSScriptRoot
$inventory=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'installed-dependencies.json') -Raw | ConvertFrom-Json
$saved=@{}
foreach ($name in @('PYTHONHOME','PYTHONNOUSERSITE','PYTHONPATH','PYTHONHASHSEED','NEXTGOWIN_HANDOFF_ROOT','NEXTGOWIN_LEGACY_ROOT','NEXTGOWIN_BLOCK_LEGACY_READS','NEXTGOWIN_SDK','PATH')) { $saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process') }
try {
    $env:PYTHONHOME=Join-Path $reproRoot 'runtime_bundle/python'
    $env:PYTHONNOUSERSITE='1'
    $env:PYTHONHASHSEED='0'
    $env:PYTHONPATH=Join-Path $PSScriptRoot 'bootstrap'
    $env:NEXTGOWIN_HANDOFF_ROOT=$reproRoot
    $env:NEXTGOWIN_LEGACY_ROOT=$inventory.legacy_root
    $env:NEXTGOWIN_BLOCK_LEGACY_READS='1'
    $env:NEXTGOWIN_SDK=Join-Path $reproRoot 'runtime_bundle/gowin/IDE'
    $env:PATH="$env:PYTHONHOME;$env:PYTHONHOME\DLLs;$env:PYTHONHOME\Library\bin;$env:NEXTGOWIN_SDK\bin;$env:SystemRoot\System32;$env:SystemRoot"
    $python=Join-Path $env:PYTHONHOME 'python.exe'
    & $python (Join-Path $PSScriptRoot 'check_dependencies.py')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
    if ($Mode -eq 'run') {
        $output=Join-Path $reproRoot ('repro_runs/small_'+[DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss'))
        & $python (Join-Path $reproRoot 'scripts/reproduce_small.py') --out $output
        if ($LASTEXITCODE -ne 0) { throw "Reproduction failed; retained logs: $output" }
        Write-Output "Reproduction completed: $output"
    }
} finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name,$saved[$name],'Process') }
}
