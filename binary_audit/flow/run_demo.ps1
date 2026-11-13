$ErrorActionPreference = 'Stop'
$demoRoot = $PSScriptRoot
$auditRoot = Split-Path $demoRoot -Parent
$gowinBin = 'C:\Gowin\Gowin_V1.9.12.04_x64\IDE\bin'
$expectedHash = '254EAC1ED05C7D1168889473DCC85203032FA4CD641C83BD5BBE4EA61A55B6B0'
if ((Get-FileHash "$gowinBin\gwsyn.dll").Hash -ne $expectedHash) { throw 'Different Gowin build: revalidate DLL interfaces first.' }
$env:PYTHONHOME = 'D:\Conda3'
$demoPython = Join-Path $demoRoot 'runtime\bin\python.exe'
function Invoke-DemoStep([string]$stepName, [string[]]$stepArgs, [string]$logPath) {
    Write-Host "Running $stepName"
    & $demoPython @stepArgs *> $logPath
    if ($LASTEXITCODE -ne 0) { throw "$stepName failed: see $logPath" }
    if (Select-String -Path $logPath -Pattern '^ERROR\s*\(' -Quiet) { throw "$stepName reported an error: see $logPath" }
}
Invoke-DemoStep 'GAO frontend export and staged mapping' @("$auditRoot\dll_steps.py", "$demoRoot\gao\gao_steps.tcl") "$demoRoot\gao\gao_steps.log"
Invoke-DemoStep 'Reload exported intermediate in a new process' @("$auditRoot\dll_steps.py", "$demoRoot\gao\resume_from_export.tcl") "$demoRoot\gao\resume_from_export.log"
Invoke-DemoStep 'Our minimal mapper' @("$demoRoot\minimal_mapper.py") "$demoRoot\minimal_mapper.log"
Invoke-DemoStep 'Vendor P&R only' @("$demoRoot\direct_pnr.py") "$demoRoot\direct_pnr.log"
Invoke-DemoStep 'Validate outputs' @("$demoRoot\verify_demo.py") "$demoRoot\verification.log"
Get-Content "$demoRoot\verification.log"
