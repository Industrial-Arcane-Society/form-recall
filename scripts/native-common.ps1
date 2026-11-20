$ErrorActionPreference = 'Stop'
$script:RepositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$script:NativeDesktopRoot = Join-Path $script:RepositoryRoot 'src/apps/native-desktop'
$script:EngineManifest = Join-Path $script:RepositoryRoot 'src/apps/engine-host/Cargo.toml'
$script:CoreManifest = Join-Path $script:RepositoryRoot 'src/core/cad-engine/Cargo.toml'

function Get-NativeGo {
    $local = Join-Path $script:RepositoryRoot '.tools/go/bin/go.exe'
    if (Test-Path -LiteralPath $local) { return $local }
    $installed = Get-Command go -ErrorAction SilentlyContinue
    if ($installed) { return $installed.Source }
    throw 'Go SDK is missing. Run scripts/bootstrap-go.ps1.'
}

function Get-NativeCargo {
    $installed = Get-Command cargo -ErrorAction SilentlyContinue
    if ($installed) { return $installed.Source }
    $local = Join-Path $env:USERPROFILE '.cargo/bin/cargo.exe'
    if (Test-Path -LiteralPath $local) { return $local }
    throw 'Rust MSVC is missing. Install Rust and Visual Studio C++ Build Tools.'
}

function Invoke-NativeTool {
    param([string]$Executable, [string[]]$Arguments, [string]$WorkingDirectory = $script:RepositoryRoot)
    Push-Location $WorkingDirectory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "$Executable failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
}

function Invoke-NativeGo {
    param([string[]]$Arguments, [switch]$Offline)
    $previousProxy = $env:GOPROXY
    $previousToolchain = $env:GOTOOLCHAIN
    try {
        if ($Offline) {
            $env:GOPROXY = 'off'
            $env:GOTOOLCHAIN = 'local'
        }
        Invoke-NativeTool (Get-NativeGo) $Arguments $script:NativeDesktopRoot
    } finally {
        $env:GOPROXY = $previousProxy
        $env:GOTOOLCHAIN = $previousToolchain
    }
}

function Get-NativeTarget {
    if ($env:CARGO_TARGET_DIR) { return [IO.Path]::GetFullPath($env:CARGO_TARGET_DIR, $script:RepositoryRoot) }
    return Join-Path $script:RepositoryRoot 'temp/build-cache/rust-native'
}

function Get-NativeOutput {
    param([switch]$DebugBuild)
    $profile = if ($DebugBuild) { 'debug' } else { 'release' }
    return Join-Path $script:RepositoryRoot "temp/outputs/native-desktop/$profile"
}

function Get-NativeOcctManifest {
    param([string]$Explicit)
    $override = if ($Explicit) { $Explicit } else { $env:AIMODELER_OCCT_MANIFEST }
    if ($override) {
        if (-not (Test-Path -LiteralPath $override -PathType Leaf)) { throw "OCCT manifest does not exist: $override" }
        return [IO.Path]::GetFullPath($override)
    }
    $candidates = @(
        (Join-Path $script:RepositoryRoot 'temp/build-cache/occt-runtime/runtime-manifest.json'),
        (Join-Path $script:RepositoryRoot 'src/apps/desktop/src-tauri/target/f4-occt/runtime-manifest.json'),
        (Join-Path $script:RepositoryRoot 'src/apps/desktop/src-tauri/target/package-resources/occt-runtime/runtime-manifest.json')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return [IO.Path]::GetFullPath($candidate) }
    }
    throw 'A verified OCCT runtime is required. Pass -OcctManifest or set AIMODELER_OCCT_MANIFEST.'
}

function Get-NativeResourceFiles {
    return @(
        'ai_modeler/agent/dsh_native_tools.mjs', 'ai_modeler/agent/dsh_attachment_store.mjs',
        'ai_modeler/agent/dsh_stream_bridge.mjs', 'ai_modeler/catia_com_adapter.py',
        'prompts/dsh_agent/system.md', 'packaging/dsh-runtime/runtime-manifest.json',
        'packaging/dsh-runtime/package.json', 'packaging/dsh-runtime/package-lock.json',
        'packaging/desktop-catia-requirements-win-x64.txt', 'kb/bolt_std.json',
        'kb/modeling_recipes.json', 'kb/native_knowledge_enrichments.v1.json',
        'datasets/训练提示词/全部提示词.jsonl'
    )
}

function Copy-NativeResources {
    param([string]$Destination)
    $source = Join-Path $script:RepositoryRoot 'src/legacy'
    foreach ($relative in (Get-NativeResourceFiles)) {
        $inputFile = Join-Path $source $relative
        if (-not (Test-Path -LiteralPath $inputFile -PathType Leaf)) { throw "Missing resource $inputFile" }
        $outputFile = Join-Path $Destination $relative
        New-Item -ItemType Directory -Path (Split-Path $outputFile -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $inputFile -Destination $outputFile -Force
    }
}

function Assert-NativeCatiaResources {
    param([Parameter(Mandatory)][string]$BundleDirectory)
    $bootstrap = Join-Path $BundleDirectory 'catia-bootstrap'
    $manifest = Get-Content -LiteralPath (Join-Path $bootstrap 'catia-runtime-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.manifestVersion -ne 1 -or $manifest.platform -cne 'windows-x86_64' -or
        $manifest.pythonVersion -cne '3.13.16' -or $manifest.pywin32Version -cne '312' -or
        $manifest.pywin32Wheel -cne 'pywin32-312-cp313-cp313-win_amd64.whl' -or
        $manifest.pywin32WheelSha256 -cne 'c53e878d15a1c44788082bfe712a905433473aa38f86375b7cf8b45e3acbaaf9' -or
        $manifest.requirementsFile -cne 'desktop-catia-requirements-win-x64.txt') {
        throw 'CATIA runtime manifest does not match the supported Windows x64 runtime.'
    }
    $requirements = Join-Path $BundleDirectory ('app/packaging/' + $manifest.requirementsFile)
    if ((Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash -ne $manifest.requirementsSha256 -or
        (Get-FileHash -LiteralPath (Join-Path $bootstrap $manifest.pywin32Wheel) -Algorithm SHA256).Hash -ne $manifest.pywin32WheelSha256) {
        throw 'CATIA requirements or pywin32 wheel checksum mismatch.'
    }
    $pythonRoot = [IO.Path]::GetFullPath((Join-Path $BundleDirectory 'python'))
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifest.pythonFiles) {
        if (-not $entry.path -or [IO.Path]::IsPathRooted($entry.path) -or $entry.path.Contains(':') -or
            $entry.sha256 -notmatch '^[0-9a-f]{64}$') { throw 'Invalid CATIA Python manifest entry.' }
        $path = [IO.Path]::GetFullPath((Join-Path $pythonRoot $entry.path))
        $relative = [IO.Path]::GetRelativePath($pythonRoot, $path).Replace('\', '/')
        if ($relative -eq '..' -or $relative.StartsWith('../') -or -not $names.Add($relative)) {
            throw 'CATIA Python manifest contains an escaped or duplicate path.'
        }
        $file = Get-Item -LiteralPath $path -Force
        if ($file.PSIsContainer -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            $file.Length -ne $entry.size -or (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
            throw "CATIA Python file verification failed: $relative"
        }
    }
    foreach ($required in @('python.exe', 'python313.dll', 'python313.zip', 'python313._pth', 'LICENSE.txt')) {
        if (-not $names.Contains($required)) { throw "Required CATIA Python file is missing: $required" }
    }
    $pythonEntries = @(Get-ChildItem -LiteralPath $pythonRoot -Recurse -Force)
    if (@($pythonEntries | Where-Object { -not $_.PSIsContainer }).Count -ne $names.Count -or
        @($pythonEntries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -gt 0) {
        throw 'CATIA Python directory contains unlisted files or links.'
    }
}

function Copy-NativeCatiaResources {
    param([Parameter(Mandatory)][string]$Source, [Parameter(Mandatory)][string]$Destination)
    Assert-NativeCatiaResources $Source
    try {
        Assert-NativeCatiaResources $Destination
        if ((Get-FileHash -LiteralPath (Join-Path $Source 'catia-bootstrap/catia-runtime-manifest.json')).Hash -eq
            (Get-FileHash -LiteralPath (Join-Path $Destination 'catia-bootstrap/catia-runtime-manifest.json')).Hash) { return }
    } catch { }
    $destinationRoot = [IO.Path]::GetFullPath($Destination)
    foreach ($directory in @('python', 'catia-bootstrap')) {
        $target = [IO.Path]::GetFullPath((Join-Path $destinationRoot $directory))
        if ([IO.Path]::GetDirectoryName($target) -ne $destinationRoot) { throw 'Runtime copy target escaped the bundle directory.' }
        if (Test-Path -LiteralPath $target) {
            if ((Get-Item -LiteralPath $target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Runtime copy target is a link: $target"
            }
            Remove-Item -LiteralPath $target -Recurse -Force
        }
        Copy-Item -LiteralPath (Join-Path $Source $directory) -Destination $target -Recurse
    }
    Assert-NativeCatiaResources $Destination
}

function Initialize-NativeRunResources {
    param([Parameter(Mandatory)][string]$BundleDirectory, [switch]$Offline)
    $prepared = Join-Path $script:RepositoryRoot 'temp/build-cache/dsh-runtime-package/package-resources'
    $resources = Join-Path $script:RepositoryRoot 'temp/build-cache/dsh-runtime-package/package-resources/app'
    $runtimeDirectory = Join-Path $resources 'packaging/dsh-runtime'
    $sourceDirectory = Join-Path $script:RepositoryRoot 'src/legacy/packaging/dsh-runtime'
    $runtimePath = Join-Path $sourceDirectory 'runtime-manifest.json'
    $bundlePath = Join-Path $runtimeDirectory 'dsh-runtime-win-x64.zip'
    $bundleManifestPath = Join-Path $runtimeDirectory 'runtime-bundle-manifest.json'
    $verified = $false
    if ((Test-Path -LiteralPath $bundlePath -PathType Leaf) -and (Test-Path -LiteralPath $bundleManifestPath -PathType Leaf)) {
        try {
            $runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
            $bundle = Get-Content -LiteralPath $bundleManifestPath -Raw | ConvertFrom-Json
            $expected = @{
                bundleManifestVersion = 1
                platform = $runtime.platform
                bundleFile = 'dsh-runtime-win-x64.zip'
                nodeVersion = $runtime.node.version
                npmVersion = $runtime.node.npmVersion
                dshVersion = $runtime.dsh.version
                acpVersion = $runtime.dsh.acpVersion
                acpSdkVersion = $runtime.dsh.acpSdkVersion
                packageJsonSha256 = (Get-FileHash -LiteralPath (Join-Path $sourceDirectory 'package.json') -Algorithm SHA256).Hash.ToLowerInvariant()
                packageLockSha256 = (Get-FileHash -LiteralPath (Join-Path $sourceDirectory 'package-lock.json') -Algorithm SHA256).Hash.ToLowerInvariant()
            }
            $mismatch = @($expected.Keys | Where-Object { $bundle.$_ -cne $expected[$_] })
            $verified = $mismatch.Count -eq 0 -and
                (Get-FileHash -LiteralPath $runtimePath -Algorithm SHA256).Hash -eq
                    (Get-FileHash -LiteralPath (Join-Path $runtimeDirectory 'runtime-manifest.json') -Algorithm SHA256).Hash -and
                $expected.packageJsonSha256 -ceq $runtime.npm.packageJsonSha256 -and
                $expected.packageLockSha256 -ceq $runtime.npm.packageLockSha256 -and
                $bundle.bundleSha256 -cmatch '^[a-f0-9]{64}$' -and
                $bundle.bundleSize -gt 0 -and $bundle.fileCount -gt 0 -and $bundle.unpackedSize -gt 0 -and
                (Get-Item -LiteralPath $bundlePath).Length -eq $bundle.bundleSize -and
                (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash -eq $bundle.bundleSha256
        } catch {
            $verified = $false
        }
    }
    try { Assert-NativeCatiaResources $prepared }
    catch { $verified = $false }
    if (-not $verified) {
        $node = (Get-Command node -ErrorAction Stop).Source
        $arguments = @((Join-Path $script:RepositoryRoot 'src/legacy/scripts/build_desktop_resources.mjs'), '--native-resources')
        if ($Offline) { $arguments += '--offline' }
        Invoke-NativeTool $node $arguments | Out-Host
    }
    Copy-NativeResources $resources
    $bundleResources = Join-Path $BundleDirectory 'app'
    Copy-NativeResources $bundleResources
    foreach ($relative in @('runtime-bundle-manifest.json', 'dsh-runtime-win-x64.zip')) {
        $source = Join-Path $runtimeDirectory $relative
        $target = Join-Path $bundleResources ('packaging/dsh-runtime/' + $relative)
        if ((Test-Path -LiteralPath $target -PathType Leaf) -and
            (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) { continue }
        Copy-Item -LiteralPath $source -Destination $target -Force
    }
    Copy-NativeCatiaResources $prepared $BundleDirectory
    return $bundleResources
}

function Copy-NativeGoLicenses {
    param([string]$Destination, [switch]$Offline)
    $modules = Invoke-NativeGo @('list', '-mod=readonly', '-deps', '-f', '{{if .Module}}{{if not .Module.Main}}{{.Module.Path}}|{{.Module.Dir}}{{end}}{{end}}', './cmd/formrecall') -Offline:$Offline
    foreach ($line in @($modules | Where-Object { $_ } | Sort-Object -Unique)) {
        $moduleName, $moduleRoot = $line -split '\|', 2
        if (-not $moduleRoot) { throw "Cannot locate dependency license files for $moduleName" }
        $licenses = @(Get-ChildItem -LiteralPath $moduleRoot -Recurse -File | Where-Object { $_.Name -match '^(LICENSE|COPYING|NOTICE)([-.]|$)' })
        if ($licenses.Count -eq 0) { throw "No dependency license file found for $moduleName" }
        foreach ($license in $licenses) {
            $relative = [IO.Path]::GetRelativePath($moduleRoot, $license.FullName)
            $outputFile = Join-Path (Join-Path $Destination $moduleName) $relative
            New-Item -ItemType Directory -Path (Split-Path $outputFile -Parent) -Force | Out-Null
            Copy-Item -LiteralPath $license.FullName -Destination $outputFile -Force
        }
    }
    $goRoot = Invoke-NativeGo @('env', 'GOROOT') -Offline:$Offline
    Copy-Item -LiteralPath (Join-Path $goRoot 'LICENSE') -Destination (Join-Path $Destination 'Go-LICENSE') -Force
}

function Copy-NativeOcctRuntime {
    param([string]$Manifest, [string]$Destination)
    $manifestPath = [IO.Path]::GetFullPath($Manifest)
    $source = Split-Path $manifestPath -Parent
    $record = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($record.occt_version -ne '7.9.3' -or $record.abi_version -ne 1 -or $record.bridge_abi_version -ne 1) {
        throw 'OCCT runtime must use version 7.9.3 and bridge ABI 1.'
    }
    $entries = @($record.bridge_artifacts | Where-Object { [IO.Path]::GetFileName($_.path) -eq 'occt_bridge.dll' })
    $entries += @($record.occt_runtime_dlls)
    $entries += @($record.msvc_runtime_dlls)
    if ($entries.Count -ne 19) { throw 'OCCT runtime manifest must contain 19 runtime DLLs.' }
    $expectedNames = @(
        'occt_bridge.dll', 'TKBO.dll', 'TKBool.dll', 'TKBRep.dll', 'TKernel.dll',
        'TKFillet.dll', 'TKG2d.dll', 'TKG3d.dll', 'TKGeomAlgo.dll', 'TKGeomBase.dll',
        'TKMath.dll', 'TKMesh.dll', 'TKOffset.dll', 'TKPrim.dll', 'TKShHealing.dll',
        'TKTopAlgo.dll', 'msvcp140.dll', 'vcruntime140.dll', 'vcruntime140_1.dll'
    )
    $actualNames = @($entries | ForEach-Object { [IO.Path]::GetFileName($_.path) })
    if (Compare-Object $expectedNames $actualNames) { throw 'OCCT manifest DLL names do not match the required runtime.' }
    $bridgeDirectory = [IO.Path]::GetDirectoryName($entries[0].path)
    $names = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $entries) {
        if ([IO.Path]::GetDirectoryName($entry.path) -ne $bridgeDirectory) {
            throw 'OCCT runtime DLLs must share the bridge directory.'
        }
        $inputFile = [IO.Path]::GetFullPath([IO.Path]::Combine($source, $entry.path))
        $relative = [IO.Path]::GetRelativePath($source, $inputFile)
        if ($relative.StartsWith('..') -or [IO.Path]::IsPathRooted($relative) -or -not $names.Add($relative)) {
            throw 'OCCT manifest contains an invalid or duplicate relative path.'
        }
        if ((Get-FileHash -LiteralPath $inputFile -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.sha256.ToLowerInvariant()) {
            throw "OCCT checksum failed for $relative"
        }
        $outputFile = Join-Path $Destination $relative
        New-Item -ItemType Directory -Path (Split-Path $outputFile -Parent) -Force | Out-Null
        Copy-Item -LiteralPath $inputFile -Destination $outputFile -Force
    }
    foreach ($license in @('LICENSE_LGPL_21.txt', 'OCCT_LGPL_EXCEPTION.txt')) {
        $inputFile = Join-Path $source "install/$license"
        if (-not (Test-Path -LiteralPath $inputFile)) { $inputFile = Join-Path $source "licenses/$license" }
        if (-not (Test-Path -LiteralPath $inputFile)) { $inputFile = Join-Path $source $license }
        if (-not (Test-Path -LiteralPath $inputFile)) { throw "OCCT license is missing: $license" }
        New-Item -ItemType Directory -Path (Join-Path $Destination 'install') -Force | Out-Null
        Copy-Item -LiteralPath $inputFile -Destination (Join-Path $Destination "install/$license") -Force
    }
    Copy-Item -LiteralPath $manifestPath -Destination (Join-Path $Destination 'runtime-manifest.json') -Force
}
