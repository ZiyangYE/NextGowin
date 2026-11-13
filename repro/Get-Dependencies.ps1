param([switch]$KeepDownloads)
$ErrorActionPreference='Stop'
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$root=[IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$manifest=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'dependencies.json') -Raw | ConvertFrom-Json
$cache=Join-Path $PSScriptRoot '.downloads'
function Get-TreeBytes([string]$directory) {
    [long]$total=0
    foreach ($entry in [IO.Directory]::EnumerateFileSystemEntries($directory)) {
        $attr=[IO.File]::GetAttributes($entry)
        if (($attr -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
        if (($attr -band [IO.FileAttributes]::Directory) -ne 0) { $total+=Get-TreeBytes $entry }
        else { $total+=[IO.FileInfo]::new($entry).Length }
    }
    return $total
}
function Assert-NoLink([string]$path) {
    $probe=$path
    while ($probe -and $probe.Length -ge $root.Length) {
        if ([IO.File]::Exists($probe) -or [IO.Directory]::Exists($probe)) {
            if (([IO.File]::GetAttributes($probe) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw "Reparse path refused: $probe" }
        }
        $probe=[IO.Path]::GetDirectoryName($probe)
    }
}
function Safe-Path([string]$relative) {
    if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains(':')) { throw 'Invalid archive path' }
    $target=[IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $target.StartsWith($root+[IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Archive path escapes checkout' }
    Assert-NoLink $target
    return $target
}
[long]$reserve=2*[long]$manifest.archive_bytes+[long]$manifest.unpacked_bytes+10000000
if ((Get-TreeBytes $root)+$reserve -gt 40000000000) { throw 'The checkout would exceed the 40 GB budget.' }
$drive=[IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
if ($drive.AvailableFreeSpace -lt $reserve) { throw 'Insufficient free disk space.' }
Assert-NoLink $cache
[IO.Directory]::CreateDirectory($cache) | Out-Null
$partPaths=@()
foreach ($part in $manifest.parts) {
    if ($part.name -notmatch '^NextGowin-small-dependencies\.zip\.part[0-9]{4}$' -or $part.bytes -gt 8000000) { throw 'Invalid part manifest' }
    $path=Safe-Path ('repro/.downloads/'+$part.name)
    if (-not (Test-Path -LiteralPath $path)) {
        Write-Output ('Downloading '+$part.name)
        Invoke-WebRequest -UseBasicParsing -Uri ($manifest.release+'/'+$part.name) -OutFile ($path+'.partial')
        if ((Get-Item -LiteralPath ($path+'.partial')).Length -ne $part.bytes -or (Get-FileHash -LiteralPath ($path+'.partial') -Algorithm SHA256).Hash -ne $part.sha256) { throw ('Downloaded part failed verification: '+$part.name) }
        Move-Item -LiteralPath ($path+'.partial') -Destination $path
    }
    if ((Get-Item -LiteralPath $path).Length -ne $part.bytes -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $part.sha256) { throw ('Cached part failed verification: '+$part.name) }
    $partPaths+=,$path
}
$zip=Safe-Path 'repro/.downloads/dependencies.zip'
$destination=[IO.File]::Open($zip,[IO.FileMode]::Create,[IO.FileAccess]::Write)
try { foreach ($path in $partPaths) { $source=[IO.File]::OpenRead($path);try { $source.CopyTo($destination) } finally { $source.Dispose() } } } finally { $destination.Dispose() }
if ((Get-Item -LiteralPath $zip).Length -ne $manifest.archive_bytes -or (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ne $manifest.archive_sha256) { throw 'Merged archive verification failed.' }
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive=[IO.Compression.ZipFile]::OpenRead($zip)
try {
    [long]$total=0
    foreach ($entry in $archive.Entries) { $null=Safe-Path $entry.FullName; $total+=$entry.Length }
    if ($total -gt $manifest.unpacked_bytes) { throw 'Archive exceeds declared unpacked size.' }
    foreach ($entry in $archive.Entries) {
        $target=Safe-Path $entry.FullName
        if ($entry.FullName.EndsWith('/')) { [IO.Directory]::CreateDirectory($target) | Out-Null; continue }
        [IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
        if (Test-Path -LiteralPath $target) {
            $stream=$entry.Open();$algorithm=[Security.Cryptography.SHA256]::Create()
            try { $hash=[BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','') } finally { $stream.Dispose();$algorithm.Dispose() }
            if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $hash) { throw "Existing file differs; refusing overwrite: $target" }
            continue
        }
        $source=$entry.Open();$destination=[IO.File]::Open($target,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
        try { $source.CopyTo($destination) } finally { $source.Dispose();$destination.Dispose() }
    }
} finally { $archive.Dispose() }
foreach ($relative in @('third_party/abc','runtime_bundle/python/Library/bin')) { [IO.Directory]::CreateDirectory((Safe-Path $relative)) | Out-Null }
& (Join-Path $PSScriptRoot 'Run-Small.ps1') -Mode check
if (-not $KeepDownloads) {
    foreach ($path in @($zip)+$partPaths) { Assert-NoLink $path;Remove-Item -LiteralPath $path }
}
Write-Output 'Dependencies installed and verified. Run: ./repro/Run-Small.ps1'
