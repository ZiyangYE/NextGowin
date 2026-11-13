param([string]$LocalArchive='',[switch]$KeepDownloads)
$ErrorActionPreference='Stop'
$checkpointRoot=Split-Path -Parent $PSScriptRoot
$checkpointPython=Join-Path $checkpointRoot 'runtime_bundle/python/python.exe'
if(-not (Test-Path -LiteralPath $checkpointPython)){throw 'Install the existing runtime first: ./repro/Get-Dependencies.ps1'}
$arguments=@((Join-Path $PSScriptRoot 'get_checkpoint.py'))
if($LocalArchive){$arguments+=@('--archive',$LocalArchive)}
if($KeepDownloads){$arguments+='--keep-downloads'}
& $checkpointPython @arguments
if($LASTEXITCODE -ne 0){throw 'Checkpoint artifact installation failed'}
