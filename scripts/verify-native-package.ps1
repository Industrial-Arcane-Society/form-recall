param([Parameter(Mandatory)][string]$PackageDirectory, [string]$ArchivePath, [switch]$InstallDshRuntime, [switch]$InstallCatiaRuntime)
. "$PSScriptRoot/native-common.ps1"
$package = (Resolve-Path -LiteralPath $PackageDirectory).Path
$manifestPath = Join-Path $package 'package-manifest.json'
$record = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($record.schema -ne 1 -or $record.source_commit -notmatch '^[0-9a-f]{40}$') {
    throw 'Invalid package manifest schema or source revision.'
}

function Assert-NativeExecutable {
    param([string]$Path, [bool]$Gui)
    $stream = [IO.File]::OpenRead($Path)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) { throw "Invalid Windows executable: $Path" }
        $stream.Position = 0x3C
        $header = $reader.ReadUInt32()
        $stream.Position = $header
        if ($reader.ReadUInt32() -ne 0x00004550 -or $reader.ReadUInt16() -ne 0x8664) {
            throw "Expected a Windows x64 executable: $Path"
        }
        $stream.Position = $header + 24
        if ($reader.ReadUInt16() -ne 0x20B) { throw "Expected PE32+ executable: $Path" }
        $stream.Position = $header + 24 + 68
        $expectedSubsystem = if ($Gui) { 2 } else { 3 }
        if ($reader.ReadUInt16() -ne $expectedSubsystem) { throw "Unexpected Windows subsystem: $Path" }
    } finally {
        $reader.Dispose()
        $stream.Dispose()
    }
}

$entries = [Collections.Generic.Dictionary[string, object]]::new([StringComparer]::OrdinalIgnoreCase)
foreach ($entry in $record.files) {
    if (-not $entry.path -or [IO.Path]::IsPathRooted($entry.path) -or $entry.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'Package manifest contains an invalid file entry.'
    }
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($package, $entry.path))
    $relative = [IO.Path]::GetRelativePath($package, $path).Replace('\', '/')
    if ($relative.StartsWith('../') -or $relative -eq '..' -or -not $entries.TryAdd($relative, $entry)) {
        throw 'Package manifest contains an escaped or duplicate file path.'
    }
    $file = Get-Item -LiteralPath $path
    if ($file.PSIsContainer -or $file.Length -ne $entry.bytes -or
        (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash -ne $entry.sha256) {
        throw "Package file verification failed: $relative"
    }
}

$required = @(
    'FormRecall.exe', 'formrecall-engine-host.exe', 'Start-FormRecall.cmd',
    'LICENSE', 'THIRD_PARTY_NOTICES.md', 'licenses/go/Go-LICENSE', 'licenses/icons/Lucide-LICENSE',
    'occt-runtime/runtime-manifest.json', 'occt-runtime/install/LICENSE_LGPL_21.txt',
    'occt-runtime/install/OCCT_LGPL_EXCEPTION.txt',
    'app/packaging/dsh-runtime/runtime-bundle-manifest.json',
    'app/packaging/dsh-runtime/dsh-runtime-win-x64.zip',
    'python/python.exe', 'python/python313.dll', 'python/python313.zip', 'python/python313._pth',
    'python/LICENSE.txt', 'catia-bootstrap/catia-runtime-manifest.json',
    'catia-bootstrap/pywin32-312-cp313-cp313-win_amd64.whl'
)
$required += @(Get-NativeResourceFiles | ForEach-Object { "app/$_" })
foreach ($relative in $required) {
    if (-not $entries.ContainsKey($relative)) { throw "Required package file is missing: $relative" }
}
$files = @(Get-ChildItem -LiteralPath $package -Recurse -File)
if ($files.Count -ne $entries.Count + 1) { throw 'Package contains unlisted files.' }
Assert-NativeExecutable (Join-Path $package 'FormRecall.exe') (-not $record.debug)
Assert-NativeExecutable (Join-Path $package 'formrecall-engine-host.exe') $false
Assert-NativeCatiaResources $package

$dshRoot = Join-Path $package 'app/packaging/dsh-runtime'
$runtime = Get-Content -LiteralPath (Join-Path $dshRoot 'runtime-manifest.json') -Raw | ConvertFrom-Json
$bundle = Get-Content -LiteralPath (Join-Path $dshRoot 'runtime-bundle-manifest.json') -Raw | ConvertFrom-Json
$expectedBundleFields = @{
    bundleManifestVersion = 1
    platform = 'windows-x86_64'
    bundleFile = 'dsh-runtime-win-x64.zip'
    nodeVersion = $runtime.node.version
    npmVersion = $runtime.node.npmVersion
    dshVersion = $runtime.dsh.version
    acpVersion = $runtime.dsh.acpVersion
    acpSdkVersion = $runtime.dsh.acpSdkVersion
    packageJsonSha256 = $runtime.npm.packageJsonSha256
    packageLockSha256 = $runtime.npm.packageLockSha256
}
foreach ($field in $expectedBundleFields.Keys) {
    if ($bundle.$field -cne $expectedBundleFields[$field]) { throw "DSH bundle manifest mismatch: $field" }
}
foreach ($pair in @(@('package.json', $bundle.packageJsonSha256), @('package-lock.json', $bundle.packageLockSha256))) {
    if ((Get-FileHash -LiteralPath (Join-Path $dshRoot $pair[0]) -Algorithm SHA256).Hash -ne $pair[1]) {
        throw "DSH package input checksum mismatch: $($pair[0])"
    }
}
$bundlePath = Join-Path $dshRoot $bundle.bundleFile
if ($bundle.bundleSha256 -notmatch '^[0-9a-f]{64}$' -or $bundle.bundleSize -le 0 -or
    $bundle.fileCount -le 0 -or $bundle.unpackedSize -le 0 -or
    (Get-Item -LiteralPath $bundlePath).Length -ne $bundle.bundleSize -or
    (Get-FileHash -LiteralPath $bundlePath -Algorithm SHA256).Hash -ne $bundle.bundleSha256) {
    throw 'DSH bundle size or SHA-256 verification failed.'
}
$runtimeArchive = [IO.Compression.ZipFile]::OpenRead($bundlePath)
try {
    $runtimeFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    $runtimeBytes = [long]0
    foreach ($entry in $runtimeArchive.Entries) {
        $relative = $entry.FullName.Replace('\', '/')
        if ($relative -notmatch '^(node|dsh)/' -or $relative -match '(^|/)\.\.(/|$)|:' -or
            (($entry.ExternalAttributes -shr 16) -band 0xF000) -eq 0xA000) {
            throw "Unsafe DSH archive entry: $relative"
        }
        if ($relative.EndsWith('/')) { continue }
        if (-not $runtimeFiles.Add($relative)) { throw "Duplicate DSH archive entry: $relative" }
        $runtimeBytes += $entry.Length
    }
    foreach ($relative in @(
        'node/node.exe', 'node/node_modules/npm/bin/npm-cli.js',
        ('dsh/' + $runtime.dsh.cliPath), 'dsh/package.json', 'dsh/package-lock.json',
        'dsh/node_modules/@deepseek-ai/dsh-acp/package.json',
        'dsh/node_modules/@deepseek-ai/dsh-acp-app/package.json',
        'dsh/node_modules/@agentclientprotocol/sdk/package.json'
    )) {
        if (-not $runtimeFiles.Contains($relative)) { throw "Required DSH runtime file is missing: $relative" }
    }
    if ($runtimeFiles.Count -ne $bundle.fileCount -or $runtimeBytes -ne $bundle.unpackedSize) {
        throw 'DSH archive contents do not match the bundle manifest.'
    }
} finally { $runtimeArchive.Dispose() }

if ($ArchivePath) {
    $archive = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $ArchivePath).Path)
    try {
        $root = (Split-Path $package -Leaf) + '/'
        $zipped = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        foreach ($entry in $archive.Entries) {
            if ($entry.FullName.EndsWith('/')) { continue }
            if (-not $entry.FullName.StartsWith($root, [StringComparison]::Ordinal)) { throw 'Archive root does not match the package.' }
            $relative = $entry.FullName.Substring($root.Length)
            if (-not $zipped.Add($relative)) { throw "Duplicate archive entry: $relative" }
            $expected = $entries[$relative]
            if ($relative -eq 'package-manifest.json') {
                $expected = @{ bytes = (Get-Item -LiteralPath $manifestPath).Length; sha256 = (Get-FileHash -LiteralPath $manifestPath).Hash }
            }
            if (-not $expected -or $entry.Length -ne $expected.bytes) { throw "Unexpected archive entry: $relative" }
            $stream = $entry.Open()
            try { $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream)) }
            finally { $stream.Dispose() }
            if ($hash -ne $expected.sha256) { throw "Archive checksum failed: $relative" }
        }
        if ($zipped.Count -ne $entries.Count + 1) { throw 'Archive files do not match the package manifest.' }
    } finally { $archive.Dispose() }
}

if ($InstallDshRuntime) {
    $scratch = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('formrecall-dsh-check-' + [Guid]::NewGuid().ToString('N'))))
    New-Item -ItemType Directory -Path $scratch | Out-Null
    $startInfo = [Diagnostics.ProcessStartInfo]::new((Join-Path $package 'formrecall-engine-host.exe'))
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    foreach ($argument in @('--resources', (Join-Path $package 'app'), '--data', $scratch, '--config', (Join-Path $scratch 'config.json'))) {
        $startInfo.ArgumentList.Add($argument)
    }
    $startInfo.Environment['AIMODELER_OCCT_MANIFEST'] = Join-Path $package 'occt-runtime/runtime-manifest.json'
    $process = [Diagnostics.Process]::Start($startInfo)
    $errors = $process.StandardError.ReadToEndAsync()
    $installEvents = [Collections.Generic.List[object]]::new()
    function Invoke-PackageEngine {
        param([long]$Id, [string]$Method)
        $process.StandardInput.WriteLine((@{ id = $Id; method = $Method; params = @{} } | ConvertTo-Json -Compress))
        $deadline = [DateTime]::UtcNow.AddMinutes(5)
        while ($true) {
            $line = $process.StandardOutput.ReadLineAsync()
            $remaining = [int][Math]::Max(1, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
            if (-not $line.Wait($remaining)) { throw "Packaged engine timed out during $Method" }
            if ($null -eq $line.Result) { throw "Packaged engine closed during $Method" }
            $reply = $line.Result | ConvertFrom-Json
            if ($reply.event) { $installEvents.Add($reply.data); continue }
            if ($reply.id -ne $Id) { throw "Unexpected packaged engine reply during $Method" }
            if ($reply.error) { throw "Packaged engine $Method failed: $($reply.error)" }
            return $reply.result
        }
    }
    try {
        $before = Invoke-PackageEngine 1 'native_runtime_status'
        if ($before.dsh.installed -or -not $before.dsh.bundled -or $before.dsh.state -eq 'invalid') {
            throw 'Clean packaged DSH runtime is unavailable for installation.'
        }
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $installed = Invoke-PackageEngine 2 'install_dsh_runtime'
        $timer.Stop()
        if (-not $installed.dsh.installed -or -not $installed.dsh.verified -or -not $installed.dsh.bundled) {
            throw 'Packaged DSH runtime did not install successfully.'
        }
        $repeated = Invoke-PackageEngine 3 'install_dsh_runtime'
        if (-not $repeated.dsh.installed) { throw 'Repeated DSH installation lost the installed runtime.' }
        $marker = Get-Content -LiteralPath (Join-Path $scratch 'dsh-runtime/current/install-manifest.json') -Raw | ConvertFrom-Json
        if ($marker.runtimeSource -ne 'bundled' -or $marker.bundleSha256 -ne $bundle.bundleSha256) {
            throw 'DSH runtime installation did not use the verified packaged archive.'
        }
        $null = Invoke-PackageEngine 4 'shutdown'
        if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0) { throw 'Packaged engine did not shut down cleanly.' }
        $proof = @{
            schema = 1; verified_at = [DateTime]::UtcNow.ToString('o'); package = $package
            bundle_sha256 = $bundle.bundleSha256; bundle_bytes = $bundle.bundleSize
            unpacked_bytes = $bundle.unpackedSize; file_count = $bundle.fileCount
            installation_ms = $timer.ElapsedMilliseconds; before = $before.dsh
            installed = $installed.dsh; repeated_install = $repeated.dsh
            install_marker = $marker; events = @($installEvents.ToArray())
        }
        $proofPath = Join-Path $script:RepositoryRoot 'temp/outputs/native-dsh-verification.json'
        New-Item -ItemType Directory -Path (Split-Path $proofPath -Parent) -Force | Out-Null
        $proof | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $proofPath -Encoding utf8NoBOM
        Write-Host "Verified packaged DSH installation and repeat installation in $($timer.ElapsedMilliseconds) ms."
    } finally {
        if (-not $process.HasExited) { $process.Kill($true); $process.WaitForExit() }
        $process.Dispose()
        $resolvedScratch = (Resolve-Path -LiteralPath $scratch).Path
        if ([IO.Path]::GetFullPath($resolvedScratch) -ne $scratch -or
            [IO.Path]::GetDirectoryName($scratch) -ne [IO.Path]::GetTempPath().TrimEnd('\', '/')) {
            throw 'Refusing to remove a runtime verification directory outside the temporary directory.'
        }
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    }
}
if ($InstallCatiaRuntime) {
    & "$PSScriptRoot/verify-native-catia.ps1" -BundleDirectory $package -OcctManifest (Join-Path $package 'occt-runtime/runtime-manifest.json')
}
Write-Host "Verified $($entries.Count) package files and Windows x64 executables."
