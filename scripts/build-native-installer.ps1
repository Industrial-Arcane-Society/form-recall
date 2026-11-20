#Requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$CompilerPath
)

$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The Windows installer must be compiled on Windows.' }

$versionMatch = [regex]::Match($Version, '\Av?(?<major>0|[1-9][0-9]*)\.(?<minor>0|[1-9][0-9]*)\.(?<patch>0|[1-9][0-9]*)(?:-(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?\z')
if (-not $versionMatch.Success) { throw 'Version must be a valid SemVer, optionally prefixed with v.' }
$Version = $Version.TrimStart('v')
$versionParts = foreach ($name in @('major', 'minor', 'patch')) {
    $number = [uint16]0
    if (-not [uint16]::TryParse($versionMatch.Groups[$name].Value, [ref]$number)) {
        throw 'Each numeric version component must be between 0 and 65535 for Windows version metadata.'
    }
    $number
}
$windowsVersion = ($versionParts -join '.') + '.0'

$package = Get-Item -LiteralPath $PackageDirectory -Force
if (-not $package.PSIsContainer) { throw 'PackageDirectory must be a directory.' }
$packagePath = $package.FullName.TrimEnd('\', '/')
$outputPath = [IO.Path]::GetFullPath($OutputDirectory, (Get-Location).ProviderPath)
if ($outputPath.TrimEnd('\', '/') -eq $packagePath -or
    $outputPath.StartsWith($packagePath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'OutputDirectory must be outside PackageDirectory.'
}
$packageItems = @($package) + @(Get-ChildItem -LiteralPath $packagePath -Force -Recurse)
if (@($packageItems | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
    throw 'PackageDirectory must contain regular files and directories without symbolic links or junctions.'
}
$manifestPath = Join-Path $packagePath 'runtime/base/package-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.version -cne $Version) {
    throw "Package manifest version '$($manifest.version)' does not match requested version '$Version'."
}
# The program root adds the entry point and the bundled version inventory on top
# of the bundled version files themselves.
if (@($packageItems | Where-Object { -not $_.PSIsContainer }).Count -ne @($manifest.files).Count + 2) {
    throw 'The package contains files absent from its manifest.'
}
& "$PSScriptRoot/verify-native-package.ps1" -PackageDirectory $packagePath

if ($CompilerPath) {
    $compiler = (Get-Item -LiteralPath $CompilerPath -ErrorAction Stop).FullName
} else {
    $candidates = @()
    $installed = Get-Command ISCC.exe -CommandType Application -ErrorAction SilentlyContinue
    if ($installed) { $candidates += $installed.Source }
    foreach ($programFiles in @(${env:ProgramFiles(x86)}, $env:ProgramFiles, $env:LOCALAPPDATA)) {
        if (-not $programFiles) { continue }
        foreach ($relative in @('Inno Setup 6/ISCC.exe', 'Inno Setup 7/ISCC.exe', 'Programs/Inno Setup 6/ISCC.exe', 'Programs/Inno Setup 7/ISCC.exe')) {
            $candidates += Join-Path $programFiles $relative
        }
    }
    $compiler = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
}
if (-not $compiler -or -not (Test-Path -LiteralPath $compiler -PathType Leaf)) {
    throw 'Inno Setup ISCC.exe was not found. Install Inno Setup 6 or supply -CompilerPath.'
}

$installer = Join-Path $outputPath "FormRecall-$Version-windows-x64-setup.exe"
if (Test-Path -LiteralPath $installer) { throw "Installer output already exists: $installer" }
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null
$source = Join-Path $PSScriptRoot '../src/packaging/windows/FormRecall.iss'
$arguments = @(
    '/Qp',
    "/DPackageDirectory=$packagePath",
    "/DReleaseVersion=$Version",
    "/DWindowsVersion=$windowsVersion",
    "/DInstallerOutputDirectory=$outputPath",
    $source
)
& $compiler @arguments
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed with exit code $LASTEXITCODE." }
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Inno Setup did not produce the expected installer.' }
$metadata = (Get-Item -LiteralPath $installer).VersionInfo
if ($metadata.ProductVersion.Trim() -cne $Version -or $metadata.FileVersion.Trim() -ne $windowsVersion) {
    throw 'Installer version metadata does not match the requested release.'
}
Write-Host "Built complete Windows installer $installer"
