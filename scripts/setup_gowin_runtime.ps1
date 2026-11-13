param(
    [string]$GowinRoot = 'C:\Gowin\Gowin_V1.9.12.04_x64',
    [string]$PythonHome = 'D:\Conda3'
)
$ErrorActionPreference = 'Stop'
$workspaceRoot = Split-Path $PSScriptRoot -Parent
$runtimeRoot = Join-Path $workspaceRoot 'binary_audit\flow\runtime'
$sdkRoot = Join-Path $GowinRoot 'IDE'
$synthesisDll = Join-Path $sdkRoot 'bin\gwsyn.dll'
$implementationDll = Join-Path $sdkRoot 'bin\gowin.dll'
if ((Get-FileHash -LiteralPath $synthesisDll).Hash -ne '254EAC1ED05C7D1168889473DCC85203032FA4CD641C83BD5BBE4EA61A55B6B0') {
    throw 'Different Gowin build; revalidate the DLL interfaces first.'
}
if ((Get-FileHash -LiteralPath $implementationDll).Hash -ne 'BC9472A5A22CF4A94E39E07439F261F3354C668F92EF9ABC735E6678BEA53B7B') {
    throw 'Different implementation DLL; revalidate the native backend first.'
}
$runtimeBin = Join-Path $runtimeRoot 'bin'
New-Item -ItemType Directory -Force -Path $runtimeBin | Out-Null
$files = @{}
foreach ($name in @('python.exe', 'python311.dll')) {
    $source = Join-Path $PythonHome $name
    $target = Join-Path $runtimeBin $name
    $digest = (Get-FileHash -LiteralPath $source).Hash
    if (Test-Path -LiteralPath $target) {
        if ((Get-FileHash -LiteralPath $target).Hash -ne $digest) {
            throw "Existing runtime differs: $target. Prepare a matching Python 3.11 runtime."
        }
    } else {
        Copy-Item -LiteralPath $source -Destination $target
    }
    $files[$name] = $digest
}
$links = @{
    'share' = (Join-Path $sdkRoot 'share')
    'data' = (Join-Path $sdkRoot 'data')
    'bin\prim_syns' = (Join-Path $sdkRoot 'bin\prim_syns')
    'bin\vhdl_packages' = (Join-Path $sdkRoot 'bin\vhdl_packages')
}
foreach ($relative in $links.Keys) {
    $link = Join-Path $runtimeRoot $relative
    $target = [System.IO.Path]::GetFullPath($links[$relative])
    if (-not (Test-Path -LiteralPath $target -PathType Container)) { throw "Missing SDK resource: $target" }
    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link
        $destinations = @($item.Target)
        if ($item.LinkType -ne 'Junction' -or $destinations.Count -ne 1 -or
            [System.IO.Path]::GetFullPath($destinations[0]) -ne $target) {
            throw "Existing runtime link differs: $link"
        }
    } else {
        New-Item -ItemType Junction -Path $link -Target $target | Out-Null
    }
}
@{python_home=$PythonHome; gowin_root=$GowinRoot; files=$files; resource_links=$links} |
    ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $runtimeRoot 'runtime_manifest.json') -Encoding utf8
Write-Output "Verified project-local Python 3.11 runtime: $runtimeBin"
