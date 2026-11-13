param([ValidateSet('check','audit','tests','projection','reproduce')][string]$Mode='audit')
$ErrorActionPreference='Stop'
$checkpointRoot=Split-Path -Parent $PSScriptRoot
$saved=@{}
foreach($name in @('PYTHONHOME','PYTHONNOUSERSITE','PYTHONPATH','PYTHONHASHSEED','NEXTGOWIN_HANDOFF_ROOT','NEXTGOWIN_LEGACY_ROOT','NEXTGOWIN_BLOCK_LEGACY_READS','NEXTGOWIN_SDK','PATH')) {$saved[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
try {
    $env:PYTHONHOME=Join-Path $checkpointRoot 'runtime_bundle/python'
    $env:PYTHONNOUSERSITE='1'
    $env:PYTHONHASHSEED='0'
    $env:PYTHONPATH=Join-Path $checkpointRoot 'repro/bootstrap'
    $env:NEXTGOWIN_HANDOFF_ROOT=$checkpointRoot
    $env:NEXTGOWIN_LEGACY_ROOT='C:/Users/JackC/Documents/ChatGPT/NextGowin'
    $env:NEXTGOWIN_BLOCK_LEGACY_READS='1'
    $env:NEXTGOWIN_SDK=Join-Path $checkpointRoot 'runtime_bundle/gowin/IDE'
    $env:PATH="$env:PYTHONHOME;$env:PYTHONHOME\DLLs;$env:PYTHONHOME\Library\bin;$env:NEXTGOWIN_SDK\bin;$env:SystemRoot\System32;$env:SystemRoot"
    & (Join-Path $env:PYTHONHOME 'python.exe') (Join-Path $PSScriptRoot 'run_checkpoint.py') $Mode
    if($LASTEXITCODE -ne 0){throw "Checkpoint operation failed: $Mode"}
} finally {foreach($name in $saved.Keys){[Environment]::SetEnvironmentVariable($name,$saved[$name],'Process')}}
