param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$sourceArchiveUrl = 'https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V7_9_3.zip'
$sourceArchiveSha256 = '566a236b5a22e778dbe1c3dce6560ba12db4504bbdae0354c51dd2f4728756ee'
$sourceCommit = 'a016080bf6738d6aeae020badee4e888ad1540a5'
$sourceTag = 'V7_9_3'

function Get-Sha256([string]$Path) {
    (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Remove-OwnedDirectory([string]$Path, [string]$Root) {
    $fullRoot = [IO.Path]::GetFullPath($Root).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $fullPath = [IO.Path]::GetFullPath($Path)
    $relative = [IO.Path]::GetRelativePath($fullRoot, $fullPath)
    if ([IO.Path]::IsPathRooted($relative) -or $relative -eq '.' -or $relative -eq '..' -or $relative.StartsWith("..$([IO.Path]::DirectorySeparatorChar)", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to replace path outside the generated OCCT tree: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        Remove-Item -LiteralPath $fullPath -Recurse -Force
    }
}

function Invoke-CMake([string[]]$Arguments) {
    & $script:CMakePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "CMake failed with exit code $LASTEXITCODE."
    }
}

if (-not $IsWindows) {
    throw 'The OCCT runtime builder is Windows-only; build the desktop package on x64 Windows.'
}
if ([Environment]::Is64BitOperatingSystem -ne $true) {
    throw 'The OCCT runtime builder requires x64 Windows.'
}

$runtimeRootFull = [IO.Path]::GetFullPath($RuntimeRoot)
New-Item -ItemType Directory -Force -Path $runtimeRootFull | Out-Null
$archivePath = Join-Path $runtimeRootFull 'occt-V7_9_3-source.zip'
$downloadPath = "$archivePath.download-$([guid]::NewGuid().ToString('N'))"
$sourceRoot = Join-Path $runtimeRootFull 'source/OCCT-7_9_3'
$occtBuildRoot = Join-Path $runtimeRootFull 'occt-build'
$installRoot = Join-Path $runtimeRootFull 'install'
$bridgeBuildRoot = Join-Path $runtimeRootFull 'bridge-build'
$bridgeInstallRoot = Join-Path $runtimeRootFull 'bridge-install'

$programFilesX86 = [Environment]::GetFolderPath([Environment+SpecialFolder]::ProgramFilesX86)
$vswherePath = Join-Path $programFilesX86 'Microsoft Visual Studio/Installer/vswhere.exe'
if (-not (Test-Path -LiteralPath $vswherePath)) {
    throw "Visual Studio Installer's vswhere.exe is missing: $vswherePath. Install Visual Studio Build Tools with the x64 C++ workload."
}
$vsPath = & $vswherePath -products '*' -latest -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if ($LASTEXITCODE -ne 0 -or -not $vsPath) {
    throw 'No Visual Studio installation with the x64 C++ toolchain was found. Install Visual Studio Build Tools (C++ x64).'
}
$vcvarsPath = Join-Path $vsPath 'VC/Auxiliary/Build/vcvarsall.bat'
if (-not (Test-Path -LiteralPath $vcvarsPath)) {
    throw "Visual Studio x64 environment setup script is missing: $vcvarsPath."
}
$ninjaCandidates = @(
    (Join-Path $vsPath 'Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe'),
    (Get-Command ninja.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -ErrorAction SilentlyContinue)
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
if (-not $ninjaCandidates) {
    throw 'Ninja is missing. Install the CMake/Ninja component for Visual Studio Build Tools or add ninja.exe to PATH.'
}
$ninjaPath = $ninjaCandidates[0]
$cmakeCommand = Get-Command cmake.exe -ErrorAction SilentlyContinue
if (-not $cmakeCommand) {
    throw 'CMake 3.24 or newer is required. Install CMake and add cmake.exe to PATH.'
}
$script:CMakePath = $cmakeCommand.Source

Write-Output "Preparing verified OCCT $sourceTag source for the desktop package."
if ((Test-Path -LiteralPath $archivePath) -and (Get-Sha256 $archivePath) -ne $sourceArchiveSha256) {
    Remove-Item -LiteralPath $archivePath -Force
}
if (-not (Test-Path -LiteralPath $archivePath)) {
    try {
        Invoke-WebRequest -Uri $sourceArchiveUrl -OutFile $downloadPath
        $downloadHash = Get-Sha256 $downloadPath
        if ($downloadHash -ne $sourceArchiveSha256) {
            throw "Official OCCT source archive SHA-256 mismatch. Expected $sourceArchiveSha256, got $downloadHash."
        }
        Move-Item -LiteralPath $downloadPath -Destination $archivePath
    }
    finally {
        if (Test-Path -LiteralPath $downloadPath) { Remove-Item -LiteralPath $downloadPath -Force }
    }
}
if ((Get-Sha256 $archivePath) -ne $sourceArchiveSha256) {
    throw 'The cached OCCT source archive failed its pinned SHA-256 check.'
}

# A valid archive is the only authority for the extracted source and its licenses.
Remove-OwnedDirectory (Join-Path $runtimeRootFull 'source') $runtimeRootFull
Expand-Archive -LiteralPath $archivePath -DestinationPath (Join-Path $runtimeRootFull 'source') -Force
if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot 'CMakeLists.txt'))) {
    throw 'The verified OCCT archive did not contain the expected V7_9_3 source directory.'
}
foreach ($license in @('LICENSE_LGPL_21.txt', 'OCCT_LGPL_EXCEPTION.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $sourceRoot $license) -PathType Leaf)) {
        throw "Official OCCT source archive is missing required license file: $license"
    }
}

# Import the Visual Studio x64 compiler environment into this PowerShell process.
$environmentLines = & $env:ComSpec /d /c "`"$vcvarsPath`" x64 >nul && set"
if ($LASTEXITCODE -ne 0) {
    throw "Visual Studio x64 environment initialization failed ($LASTEXITCODE): $vcvarsPath"
}
foreach ($line in $environmentLines) {
    $separator = $line.IndexOf('=')
    if ($separator -gt 0) {
        [Environment]::SetEnvironmentVariable($line.Substring(0, $separator), $line.Substring($separator + 1), 'Process')
    }
}
$env:Path = "$(Split-Path -Parent $ninjaPath);$env:Path"
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw 'Visual Studio initialization completed without an x64 cl.exe compiler.'
}

$cmakeVersionLine = (& $script:CMakePath --version | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $cmakeVersionLine -notmatch 'cmake version ([0-9]+\.[0-9]+)') {
    throw 'Could not determine the installed CMake version.'
}
if ([version]$Matches[1] -lt [version]'3.24') {
    throw "CMake 3.24 or newer is required; found $cmakeVersionLine."
}
$clPath = (Get-Command cl.exe).Source
$clFileVersion = (Get-Item -LiteralPath $clPath).VersionInfo.FileVersion
Write-Output "Toolchain: $cmakeVersionLine; MSVC $clFileVersion; Ninja $ninjaPath"

foreach ($owned in @($occtBuildRoot, $installRoot, $bridgeBuildRoot, $bridgeInstallRoot)) {
    Remove-OwnedDirectory $owned $runtimeRootFull
}

$toolkits = 'TKernel;TKMath;TKG2d;TKG3d;TKGeomBase;TKBRep;TKGeomAlgo;TKTopAlgo;TKPrim;TKBO;TKBool;TKMesh;TKShHealing'
$occtArguments = @(
    '-S', $sourceRoot,
    '-B', $occtBuildRoot,
    '-G', 'Ninja',
    '-DCMAKE_BUILD_TYPE=Release',
    "-DCMAKE_INSTALL_PREFIX=$installRoot",
    '-DBUILD_LIBRARY_TYPE=Shared',
    '-DINSTALL_DIR_LAYOUT=Windows',
    "-DBUILD_ADDITIONAL_TOOLKITS=$toolkits",
    '-DBUILD_MODULE_ApplicationFramework=OFF',
    '-DBUILD_MODULE_DataExchange=OFF',
    '-DBUILD_MODULE_DETools=OFF',
    '-DBUILD_MODULE_Draw=OFF',
    '-DBUILD_MODULE_FoundationClasses=OFF',
    '-DBUILD_MODULE_ModelingAlgorithms=OFF',
    '-DBUILD_MODULE_ModelingData=OFF',
    '-DBUILD_MODULE_Visualization=OFF',
    '-DBUILD_SAMPLES_MFC=OFF',
    '-DBUILD_SAMPLES_QT=OFF',
    '-DBUILD_Inspector=OFF',
    '-DBUILD_DOC_Overview=OFF',
    '-DUSE_TK=OFF',
    '-DUSE_FREETYPE=OFF',
    '-DUSE_FREEIMAGE=OFF',
    '-DUSE_FFMPEG=OFF',
    '-DUSE_OPENVR=OFF',
    '-DUSE_RAPIDJSON=OFF',
    '-DUSE_DRACO=OFF',
    '-DUSE_TBB=OFF',
    '-DUSE_EIGEN=OFF'
)
Invoke-CMake $occtArguments
$parallelism = [Math]::Max(2, [Math]::Min(8, [Environment]::ProcessorCount - 1))
Invoke-CMake @('--build', $occtBuildRoot, '--target', 'install', '--parallel', "$parallelism")

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
foreach ($license in @('LICENSE_LGPL_21.txt', 'OCCT_LGPL_EXCEPTION.txt')) {
    Copy-Item -LiteralPath (Join-Path $sourceRoot $license) -Destination (Join-Path $installRoot $license) -Force
}

$bridgeArguments = @(
    '-S', (Join-Path $PSScriptRoot '.'),
    '-B', $bridgeBuildRoot,
    '-G', 'Ninja',
    "-DOCCT_ROOT=$installRoot",
    "-DCMAKE_INSTALL_PREFIX=$bridgeInstallRoot",
    '-DCMAKE_BUILD_TYPE=Release',
    '-DOCCT_BRIDGE_BUILD_SMOKE=ON'
)
Invoke-CMake $bridgeArguments
Invoke-CMake @('--build', $bridgeBuildRoot, '--target', 'install', '--parallel', "$parallelism")

$runtimeBins = @(Get-ChildItem -LiteralPath (Join-Path $installRoot 'win64') -Directory -Filter 'vc*' |
    ForEach-Object { Join-Path $_.FullName 'bin' } |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'occt_bridge.dll') })
if ($runtimeBins.Count -ne 1) {
    throw "OCCT build did not install exactly one bridge runtime directory (found $($runtimeBins.Count))."
}
$env:Path = "$($runtimeBins[0]);$env:Path"
Invoke-CMake @('--build', $bridgeBuildRoot, '--target', 'occt_bridge_smoke', '--parallel', "$parallelism")
$ctestCommand = Get-Command ctest.exe -ErrorAction Stop
& $ctestCommand.Source '--test-dir' $bridgeBuildRoot '--output-on-failure'
if ($LASTEXITCODE -ne 0) {
    throw "OCCT C ABI smoke test failed with exit code $LASTEXITCODE."
}

& (Join-Path $PSScriptRoot 'Write-RuntimeManifest.ps1') -RuntimeRoot $runtimeRootFull
if ($LASTEXITCODE -ne 0) {
    throw "Runtime manifest generation failed with exit code $LASTEXITCODE."
}
Write-Output "Built OCCT $sourceTag ($sourceCommit) from verified source $sourceArchiveSha256."
