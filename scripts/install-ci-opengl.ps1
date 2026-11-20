param([Parameter(Mandatory)][string]$DestinationDirectory)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or -not [Environment]::Is64BitProcess) {
    throw 'The CI OpenGL runtime requires x64 Windows PowerShell.'
}
$destination = (Resolve-Path -LiteralPath $DestinationDirectory).Path
if (-not (Test-Path -LiteralPath $destination -PathType Container)) {
    throw 'DestinationDirectory must be an existing application directory.'
}
$version = '26.0.3'
$archiveHash = 'de1ec45c906164de2c046288a8969e83cf2d51d031dc8dfd1f9371bb31cd6755'
$archiveUrl = "https://github.com/pal1000/mesa-dist-win/releases/download/$version/mesa3d-$version-release-msvc.7z"
$requiredNames = @('opengl32.dll', 'libgallium_wgl.dll')
# Mesa removed the separate libglapi DLL in version 25.0.0.
foreach ($name in $requiredNames) {
    if (Test-Path -LiteralPath (Join-Path $destination $name)) {
        throw "CI OpenGL deployment would overwrite an existing file: $name"
    }
}
$sevenZipCommand = Get-Command 7z.exe -ErrorAction SilentlyContinue
$sevenZip = if ($sevenZipCommand) { $sevenZipCommand.Source } else { Join-Path $env:ProgramFiles '7-Zip/7z.exe' }
if (-not (Test-Path -LiteralPath $sevenZip -PathType Leaf)) { throw '7-Zip is required to extract the pinned Mesa runtime.' }
$curl = (Get-Command curl.exe -ErrorAction Stop).Source
$temporaryRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd('\', '/')
$scratch = [IO.Path]::GetFullPath((Join-Path $temporaryRoot ('FormRecall-ci-opengl-' + [Guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $scratch | Out-Null
$copiedFiles = [Collections.Generic.List[string]]::new()
$files = [Collections.Generic.List[object]]::new()
try {
    $archive = Join-Path $scratch 'mesa.7z'
    & $curl --fail --silent --show-error --location --retry 3 $archiveUrl --output $archive
    if ($LASTEXITCODE -ne 0) { throw 'The pinned Mesa runtime download failed.' }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -cne $archiveHash) {
        throw 'The downloaded Mesa archive failed its pinned SHA-256 check.'
    }
    $extracted = Join-Path $scratch 'extracted'
    & $sevenZip x $archive "-o$extracted" -y -bso0 -bsp0
    if ($LASTEXITCODE -ne 0) { throw 'Mesa archive extraction failed.' }
    $sourceDirectory = Join-Path $extracted 'x64'
    foreach ($name in $requiredNames) {
        if (-not (Test-Path -LiteralPath (Join-Path $sourceDirectory $name) -PathType Leaf)) {
            throw "The pinned Mesa archive is missing x64/$name."
        }
    }
    foreach ($name in $requiredNames) {
        $source = Join-Path $sourceDirectory $name
        $target = Join-Path $destination $name
        $inputStream = [IO.File]::OpenRead($source)
        try {
            # CreateNew also rejects a collision that appears after preflight.
            $outputStream = [IO.File]::Open($target, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $copiedFiles.Add($target)
            try { $inputStream.CopyTo($outputStream) }
            finally { $outputStream.Dispose() }
        } finally { $inputStream.Dispose() }
        $sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        $targetHash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($sourceHash -cne $targetHash) { throw "The deployed Mesa DLL failed verification: $name" }
        $files.Add([ordered]@{ name = $name; bytes = (Get-Item -LiteralPath $target).Length; sha256 = $targetHash })
    }
} catch {
    foreach ($path in $copiedFiles) { Remove-Item -LiteralPath $path -Force }
    throw
} finally {
    $resolvedScratch = (Resolve-Path -LiteralPath $scratch).Path
    if ([IO.Path]::GetFullPath($resolvedScratch) -ne $scratch -or
        [IO.Path]::GetDirectoryName($scratch) -ne $temporaryRoot -or
        (Get-Item -LiteralPath $scratch).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
        throw 'Refusing to remove a CI OpenGL temporary directory outside its validated path.'
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
}
[pscustomobject]@{
    version = $version
    archive_sha256 = $archiveHash
    destination = $destination
    files = $files.ToArray()
}
