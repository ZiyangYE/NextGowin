$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path $PSScriptRoot -Parent
$sourceRoot = Join-Path $taskRoot 'third_party\abc'
$buildRoot = Join-Path $taskRoot 'build\abc'
New-Item -ItemType Directory -Force $buildRoot | Out-Null
$dsp = Get-Content (Join-Path $sourceRoot 'abclib.dsp') -Raw
$sources = [regex]::Matches($dsp, 'SOURCE=\.\\([^\r\n]+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
# Current upstream split command registration into abcc files not all listed in
# its legacy DSP. Include that directory's translation units as well.
$commandSources = Get-ChildItem (Join-Path $sourceRoot 'src\base\abcc') -Filter '*.c' | ForEach-Object { 'src\base\abcc\' + $_.Name }
$sources = @($sources) + @($commandSources) | Sort-Object -Unique
$items = foreach ($src in $sources) {
    $absolute = Join-Path $sourceRoot $src
    if ($src -match '\.(c|cpp|cc)$') {
        if ($src -like 'src\base\abcc\*') {
            $objectName = ($src -replace '[\\/.]', '_') + '.obj'
            '    <ClCompile Include="' + $absolute + '"><ObjectFileName>$(IntDir)' + $objectName + '</ObjectFileName></ClCompile>'
        } else { '    <ClCompile Include="' + $absolute + '" />' }
    }
    elseif ($src -match '\.h$') { '    <ClInclude Include="' + $absolute + '" />' }
}
$template = Get-Content (Join-Path $sourceRoot '.github\scripts\abclib.vcxproj.template') -Raw
$library = $template.Replace('{{SOURCE_FILES}}', ($items -join "`r`n"))
$library = $library.Replace('<AdditionalIncludeDirectories>src</AdditionalIncludeDirectories>', '<AdditionalIncludeDirectories>' + (Join-Path $sourceRoot 'src') + '</AdditionalIncludeDirectories>')
# Match upstream CI's Win32 build; keep generated projects and objects outside its checkout.
$library = $library.Replace('<WholeProgramOptimization>true</WholeProgramOptimization>', '<WholeProgramOptimization>false</WholeProgramOptimization>')
$library = $library.Replace('<ClCompile>', '<ClCompile><Optimization>MaxSpeed</Optimization><MultiProcessorCompilation>true</MultiProcessorCompilation>')
$library | Set-Content -Encoding utf8 (Join-Path $buildRoot 'abclib.vcxproj')
$exe = Get-Content (Join-Path $sourceRoot '.github\scripts\abcexe.vcxproj') -Raw
$exe = $exe.Replace('src\base\main\main.c', (Join-Path $sourceRoot 'src\base\main\main.c'))
$exe = $exe.Replace('lib\x86\pthreadVC2.lib', (Join-Path $sourceRoot 'lib\x86\pthreadVC2.lib'))
$exe = $exe.Replace('<AdditionalIncludeDirectories>src</AdditionalIncludeDirectories>', '<AdditionalIncludeDirectories>' + (Join-Path $sourceRoot 'src') + '</AdditionalIncludeDirectories>')
$exe = $exe.Replace('<WholeProgramOptimization>true</WholeProgramOptimization>', '<WholeProgramOptimization>false</WholeProgramOptimization>')
$exe | Set-Content -Encoding utf8 (Join-Path $buildRoot 'abcexe.vcxproj')
Copy-Item -LiteralPath (Join-Path $sourceRoot '.github\scripts\abcspace.sln') -Destination $buildRoot
$msbuild = 'C:\Program Files\Microsoft Visual Studio\2022\Community\MSBuild\Current\Bin\MSBuild.exe'
& $msbuild (Join-Path $buildRoot 'abcspace.sln') /m:2 /nologo /v:minimal /p:Configuration=Release /p:Platform=Win32 /p:UseMultiToolTask=true /p:CL_MPCount=4 *> (Join-Path $buildRoot 'build.log')
if ($LASTEXITCODE -ne 0) { Get-Content (Join-Path $buildRoot 'build.log') -Tail 40; throw 'ABC build failed' }
Copy-Item -LiteralPath (Join-Path $sourceRoot 'lib\x86\pthreadVC2.dll') -Destination (Join-Path $buildRoot '_TEST')
Write-Output (Join-Path $buildRoot '_TEST\abc.exe')
