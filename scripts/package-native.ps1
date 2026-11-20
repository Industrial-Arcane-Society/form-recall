param(
    [switch]$SkipBuild,
    [switch]$DebugBuild,
    [switch]$Offline,
    [string]$OcctManifest,
    [string]$Version,
    [string]$OutputDirectory,
    [string]$OutputManifest,
    [string]$InstallerCompiler
)
. "$PSScriptRoot/native-common.ps1"

if ($Version) {
    $Version = $Version -creplace '^v', ''
    if ($Version -cnotmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$') {
        throw 'Version must be a semantic version such as 0.2.0 or 0.2.0-rc.1.'
    }
} elseif ($OutputManifest) {
    throw 'OutputManifest requires Version.'
}
$destination = if ($OutputDirectory) {
    [IO.Path]::GetFullPath($OutputDirectory, $script:RepositoryRoot)
} else {
    Join-Path $script:RepositoryRoot 'temp/outputs/native-package'
}
$packageName = if ($Version) { "FormRecall-$Version-windows-x64" } else { 'FormRecall-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff') }
$package = Join-Path $destination $packageName
$archive = if ($Version) { "$package-portable.zip" } else { "$package-windows-x64.zip" }
$plannedOutputs = @($package, $archive)
if ($Version) {
    $installer = "$package-setup.exe"
    $checksums = "$package.sha256"
    $releaseManifest = if ($OutputManifest) { [IO.Path]::GetFullPath($OutputManifest, $script:RepositoryRoot) } else { "$package.release.json" }
    $manifestRelative = [IO.Path]::GetRelativePath($package, $releaseManifest)
    if (-not [IO.Path]::IsPathRooted($manifestRelative) -and $manifestRelative -ne '..' -and -not $manifestRelative.StartsWith("..$([IO.Path]::DirectorySeparatorChar)")) {
        throw 'The release manifest must be outside the portable package directory.'
    }
    $plannedOutputs += @($installer, $checksums, $releaseManifest)
    if (@($plannedOutputs | Sort-Object -Unique).Count -ne $plannedOutputs.Count) {
        throw 'Release output paths must be distinct.'
    }
}
foreach ($path in $plannedOutputs) {
    if (Test-Path -LiteralPath $path) { throw "Output already exists. Choose a new output directory: $path" }
}

$runtimeManifest = Get-NativeOcctManifest $OcctManifest
if (-not $SkipBuild) { & "$PSScriptRoot/build-native.ps1" -DebugBuild:$DebugBuild -Offline:$Offline -Version $Version }
$output = Get-NativeOutput -DebugBuild:$DebugBuild
if ($Version) {
    $startInfo = [Diagnostics.ProcessStartInfo]::new((Join-Path $output 'FormRecall.exe'))
    $startInfo.ArgumentList.Add('--version-json')
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [Diagnostics.Process]::Start($startInfo)
    try {
        $standardOutput = $process.StandardOutput.ReadToEndAsync()
        $standardError = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(10000)) {
            $process.Kill($true)
            $process.WaitForExit()
            throw 'The desktop executable did not respond to --version-json within 10 seconds.'
        }
        if ($process.ExitCode -ne 0) { throw "Desktop version probe failed: $($standardError.GetAwaiter().GetResult())" }
        $binaryVersion = $standardOutput.GetAwaiter().GetResult() | ConvertFrom-Json
        if ($binaryVersion.version -cne $Version) {
            throw "Desktop version mismatch. Expected $Version, got $($binaryVersion.version)."
        }
    } finally { $process.Dispose() }
}
$node = (Get-Command node -ErrorAction Stop).Source
$resourceArguments = @((Join-Path $script:RepositoryRoot 'src/legacy/scripts/build_desktop_resources.mjs'), '--native-resources')
if ($Offline) { $resourceArguments += '--offline' }
Invoke-NativeTool $node $resourceArguments
$dshResources = Join-Path $script:RepositoryRoot 'temp/build-cache/dsh-runtime-package/package-resources/app/packaging/dsh-runtime'
$preparedResources = Join-Path $script:RepositoryRoot 'temp/build-cache/dsh-runtime-package/package-resources'
New-Item -ItemType Directory -Path $package -Force | Out-Null
# The visible program root holds the entry point and one runtime directory. The
# bundled version keeps the established flat layout so every relative path used
# by the desktop and the CAD engine stays valid.
$base = Join-Path $package 'runtime/base'
New-Item -ItemType Directory -Path $base -Force | Out-Null
foreach ($binary in @('FormRecall.exe', 'formrecall-engine-host.exe')) {
    Copy-Item -LiteralPath (Join-Path $output $binary) -Destination $base
}
Copy-NativeResources (Join-Path $base 'app')
Copy-NativeCatiaResources $preparedResources $base
foreach ($resource in @('runtime-bundle-manifest.json', 'dsh-runtime-win-x64.zip')) {
    Copy-Item -LiteralPath (Join-Path $dshResources $resource) -Destination (Join-Path $base 'app/packaging/dsh-runtime')
}
Copy-NativeOcctRuntime $runtimeManifest (Join-Path $base 'occt-runtime')
Copy-Item -LiteralPath (Join-Path $script:RepositoryRoot 'LICENSE') -Destination $base
Copy-Item -LiteralPath (Join-Path $script:NativeDesktopRoot 'THIRD_PARTY_NOTICES.md') -Destination $base
Copy-NativeGoLicenses (Join-Path $base 'licenses/go') -Offline:$Offline
$iconLicenseDirectory = Join-Path $base 'licenses/icons'
New-Item -ItemType Directory -Path $iconLicenseDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $script:NativeDesktopRoot 'internal/workbench/icons/LICENSE') -Destination (Join-Path $iconLicenseDirectory 'Lucide-LICENSE')
$sourceCommit = & git -C $script:RepositoryRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot record the source commit.' }
$sourceStatus = @(& git -C $script:RepositoryRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw 'Cannot record the source tree status.' }
$record = @{
    schema = 1
    source_commit = $sourceCommit
    source_dirty = $sourceStatus.Count -gt 0
    source_tracked_dirty = @($sourceStatus | Where-Object { -not $_.StartsWith('?? ') }).Count -gt 0
    source_untracked_count = @($sourceStatus | Where-Object { $_.StartsWith('?? ') }).Count
    built_at = [DateTime]::UtcNow.ToString('o')
    debug = [bool]$DebugBuild
    files = @(Get-ChildItem -LiteralPath $base -File -Recurse | ForEach-Object {
        @{ path = [IO.Path]::GetRelativePath($base, $_.FullName).Replace('\', '/'); bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
if ($Version) { $record.version = $Version }
$record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $base 'package-manifest.json') -Encoding utf8NoBOM
# The entry point is a byte-identical copy of the bundled desktop binary, so the
# program root never carries a separately built launcher.
Copy-Item -LiteralPath (Join-Path $base 'FormRecall.exe') -Destination $package
Compress-Archive -LiteralPath $package -DestinationPath $archive -CompressionLevel Optimal
& "$PSScriptRoot/verify-native-package.ps1" -PackageDirectory $package -ArchivePath $archive -InstallDshRuntime -InstallCatiaRuntime
if ($Version) {
    & "$PSScriptRoot/build-native-installer.ps1" -PackageDirectory $package -Version $Version `
        -OutputDirectory $destination -CompilerPath $InstallerCompiler
    if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'The complete Windows installer was not created.' }
    $assets = @($installer, $archive) | ForEach-Object {
        $file = Get-Item -LiteralPath $_
        [ordered]@{
            name = $file.Name
            path = $file.FullName
            bytes = $file.Length
            sha256 = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $assets | ForEach-Object { "$($_.sha256)  $($_.name)" } | Set-Content -LiteralPath $checksums -Encoding ascii
    $release = [ordered]@{
        schema = 2
        version = $Version
        source_commit = $sourceCommit
        source_dirty = $record.source_dirty
        source_tracked_dirty = $record.source_tracked_dirty
        built_at = $record.built_at
        package_directory = $package
        installer = $installer
        archive = $archive
        checksums = $checksums
        assets = @($assets)
    }
    New-Item -ItemType Directory -Path (Split-Path $releaseManifest -Parent) -Force | Out-Null
    $release | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $releaseManifest -Encoding utf8NoBOM
    Write-Host "Release manifest $releaseManifest"
}
Write-Host "Packaged $archive"
