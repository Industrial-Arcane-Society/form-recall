param(
    [Parameter(Mandatory)][string]$PackageDirectory,
    [Parameter(Mandatory)][string]$Version,
    [string]$OutputDirectory
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows) { throw 'Native release startup verification requires Windows.' }
$repository = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$package = (Resolve-Path -LiteralPath $PackageDirectory).Path
# The packaged entry point is the launcher; it forwards to the bundled version
# under runtime/base, which keeps the established flat bundle layout.
$payload = Join-Path $package 'runtime/base'
$executable = Join-Path $package 'FormRecall.exe'
$engine = Join-Path $payload 'formrecall-engine-host.exe'
foreach ($required in @($executable, $payload, $engine, (Join-Path $payload 'app'), (Join-Path $payload 'occt-runtime/runtime-manifest.json'))) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required package input is missing: $required" }
}
# CI provisions the software OpenGL driver beside the package entry point, while the
# desktop runs from runtime/base. Relocate those test-only files next to the process
# that loads them, and remove them again when the check finishes.
$startupArtifactCopies = [Collections.Generic.List[string]]::new()
foreach ($artifact in @(Get-ChildItem -LiteralPath $package -File -Force)) {
    if ($artifact.Name -ieq 'FormRecall.exe') { continue }
    $target = Join-Path $payload $artifact.Name
    if (Test-Path -LiteralPath $target) {
        throw "A bundled version file collides with a startup test artifact: $($artifact.Name)"
    }
    Copy-Item -LiteralPath $artifact.FullName -Destination $target
    $startupArtifactCopies.Add($target)
}
if ($Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$') {
    throw 'Version must be a semantic version without a leading v.'
}
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repository ('temp/outputs/native-release-startup/' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $output -Force | Out-Null
$temporaryRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd('\', '/')
$scratch = [IO.Path]::GetFullPath((Join-Path $temporaryRoot ('FormRecall 发布启动 ' + [Guid]::NewGuid().ToString('N'))))
$relative = [IO.Path]::GetRelativePath($repository, $scratch)
if ($relative -ne '..' -and -not $relative.StartsWith('..\') -and -not [IO.Path]::IsPathRooted($relative)) {
    throw 'The isolated startup directory must be outside the repository.'
}
New-Item -ItemType Directory -Path $scratch | Out-Null
$data = Join-Path $scratch '用户资料'
$config = Join-Path $scratch '独立配置.json'
$screenshot = Join-Path $scratch '启动截图.png'
New-Item -ItemType Directory -Path $data | Out-Null
$fixture = '{"llm":{"enabled":false,"providers":[]},"online":{"enabled":false},"native_desktop":{"theme":"深色"},"release_startup_probe":true}'
[IO.File]::WriteAllText($config, $fixture, [Text.UTF8Encoding]::new($false))
$configHash = (Get-FileHash -LiteralPath $config).Hash
$tracked = [Collections.Generic.List[object]]::new()
$removedEnvironment = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
$proof = [ordered]@{
    schema = 1
    verified_at = [DateTime]::UtcNow.ToString('o')
    package = $package
    payload_root = $payload
    expected_version = $Version
    executable_sha256 = (Get-FileHash -LiteralPath $executable).Hash.ToLowerInvariant()
    engine_sha256 = (Get-FileHash -LiteralPath $engine).Hash.ToLowerInvariant()
    working_directory = $scratch
    data_directory = $data
    config_path = $config
    screenshot_method = 'Application initialization screenshot, which may composite the native framebuffer; this check does not verify desktop presentation.'
    resource_resolution = 'Program-root launcher with only isolated --data and --config; it forwards to runtime/base, whose engine, resources and OCCT use bundle defaults.'
    passed = $false
}
$failure = $null

function New-StartupProcessInfo {
    param([string[]]$Arguments)
    $info = [Diagnostics.ProcessStartInfo]::new($executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $scratch
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    foreach ($name in @($info.Environment.Keys)) {
        if ($name -match '^(AIMODELER_|FORMRECALL_|NATIVE_VIEWPORT_|MYGO_|OPENAI_|ANTHROPIC_|DEEPSEEK_|DSH_|ACP_|OCCT_|CSF_|PYTHON)' -or
            $name -in @('CASROOT', 'NODE_OPTIONS')) {
            $null = $removedEnvironment.Add($name)
            $null = $info.Environment.Remove($name)
        }
    }
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    return $info
}

function Test-OwnedStartupExecutable {
    param([string]$Path)
    # Only processes from this package's own runtime tree are owned by the test.
    $relative = [IO.Path]::GetRelativePath($package, [IO.Path]::GetFullPath($Path))
    if ([IO.Path]::IsPathRooted($relative) -or $relative -eq '..') { return $false }
    return -not $relative.StartsWith('..' + [IO.Path]::DirectorySeparatorChar, [StringComparison]::Ordinal)
}

function Add-OwnedStartupChildren {
    param([int]$ParentId, [DateTime]$StartedAfter, [string]$ExecutableName, [string]$Kind, [switch]$RequireQuery)
    # Query only direct children started by this script: the program-root
    # launcher starts the selected version executable and that version starts its
    # packaged Rust host. The Quick fixture uses the in-process OCCT bridge.
    # Sampling a short-lived child is optional. The required query after frontend
    # exit checks for leftovers even when no running child was sampled earlier.
    try {
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentId" -OperationTimeoutSec 5)
    } catch {
        $proof['host_sampling_warning'] = $_.Exception.Message
        if ($RequireQuery) { throw 'Unable to check the test frontend for remaining child processes.' }
        return
    }
    foreach ($child in $children) {
        if ($tracked | Where-Object { $_.Id -eq [int]$child.ProcessId }) { continue }
        if (-not $child.ExecutablePath -or [IO.Path]::GetFileName($child.ExecutablePath) -ine $ExecutableName -or
            -not (Test-OwnedStartupExecutable $child.ExecutablePath)) { continue }
        $executable = [IO.Path]::GetFullPath($child.ExecutablePath)
        $process = Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        try {
            # Hold a real process handle while the child is alive so Windows can
            # still supply its exit code after the parent has reaped it.
            $null = $process.Handle
            $startedAt = $process.StartTime.ToUniversalTime()
            if ($startedAt -lt $StartedAfter) { $process.Dispose(); continue }
            $tracked.Add([pscustomobject]@{
                Id = $process.Id; StartedAt = $startedAt; Process = $process
                Executable = $executable; CommandLine = $child.CommandLine; Kind = $Kind
            })
        } catch { $process.Dispose() }
    }
}

function Assert-StartupSnapshot {
    param([System.Collections.IDictionary]$State)
    if ($State['ready'] -ne $true -or $State['busy'] -ne $false -or $State['failure'] -ne '' -or
        $State['viewport_loading'] -ne $false -or $State['viewport_error'] -ne '') {
        throw 'Frontend initialization, modelling or viewport loading failed. Inspect startup.png.json and host.log.'
    }
    $document = $State['document']
    if ($null -eq $document -or @($document['features']).Count -ne 2 -or $document['stats']['triangles'] -ne 604) {
        throw 'Expected the four-hole plate with two features and 604 triangles.'
    }
    $sizes = @($document['stats']['size'])
    if ($sizes.Count -ne 3) { throw 'The generated model has no three-dimensional bounds.' }
    $expected = @(100, 80, 10)
    for ($i = 0; $i -lt 3; $i++) {
        if ([Math]::Abs([double]$sizes[$i] - $expected[$i]) -gt 0.000001) {
            throw "Unexpected model dimension on axis $i. Expected $($expected[$i]), got $($sizes[$i])."
        }
    }
    $features = @($document['features'])
    if ($features[0]['kind'] -ne 'box' -or $features[1]['kind'] -ne 'hole') {
        throw 'Expected a plate box feature followed by its hole feature.'
    }
}

function Write-StartupStreams {
    param([System.Threading.Tasks.Task[string]]$Stdout, [System.Threading.Tasks.Task[string]]$Stderr, [string]$Prefix)
    if (-not $Stdout.Wait(5000) -or -not $Stderr.Wait(5000)) {
        throw "The $Prefix process left an output pipe open after exit."
    }
    [IO.File]::WriteAllText((Join-Path $output "$Prefix.stdout.log"), $Stdout.Result)
    [IO.File]::WriteAllText((Join-Path $output "$Prefix.stderr.log"), $Stderr.Result)
}

$versionProcess = $null
$frontend = $null
$versionOutput = $null
$versionError = $null
$stdout = $null
$stderr = $null
try {
    $versionProcess = [Diagnostics.Process]::Start((New-StartupProcessInfo @('--version-json')))
    $versionOutput = $versionProcess.StandardOutput.ReadToEndAsync()
    $versionError = $versionProcess.StandardError.ReadToEndAsync()
    if (-not $versionProcess.WaitForExit(15000)) { throw 'Packaged --version-json did not exit within 15 seconds.' }
    Write-StartupStreams $versionOutput $versionError 'version'
    if ($versionProcess.ExitCode -ne 0) { throw "Packaged --version-json exited with code $($versionProcess.ExitCode)." }
    $identity = $versionOutput.Result | ConvertFrom-Json -AsHashtable
    if ($identity['version'] -cne $Version -or $identity['commit'] -notmatch '^[0-9a-f]{40}$') {
        throw 'Packaged version identity does not match the requested release or has no full source commit.'
    }
    $proof['identity'] = $identity
    $manifestPath = Join-Path $payload 'package-manifest.json'
    if (Test-Path -LiteralPath $manifestPath) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable
        if ($manifest['source_commit'] -cne $identity['commit']) {
            throw 'Frontend embedded commit differs from the package manifest.'
        }
    }
    $arguments = @('--data', $data, '--config', $config, '--demo-prompt', '底板 长100 宽80 厚10，四角通孔 直径8 边距10',
        '--screenshot', $screenshot, '--exit-after', '500ms')
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $frontend = [Diagnostics.Process]::Start((New-StartupProcessInfo $arguments))
    $frontendStartedAt = $frontend.StartTime.ToUniversalTime()
    $stdout = $frontend.StandardOutput.ReadToEndAsync()
    $stderr = $frontend.StandardError.ReadToEndAsync()
    $proof['frontend_pid'] = $frontend.Id
    $deadline = [DateTime]::UtcNow.AddSeconds(150)
    do {
        Add-OwnedStartupChildren $frontend.Id $frontendStartedAt 'FormRecall.exe' 'version'
        foreach ($versionRecord in @($tracked | Where-Object { $_.Kind -ceq 'version' })) {
            Add-OwnedStartupChildren $versionRecord.Id $versionRecord.StartedAt 'formrecall-engine-host.exe' 'host'
        }
        if ($frontend.WaitForExit(100)) { break }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Packaged frontend startup did not finish within 150 seconds.' }
    } while ($true)
    $timer.Stop()
    Add-OwnedStartupChildren $frontend.Id $frontendStartedAt 'FormRecall.exe' 'version' -RequireQuery
    Write-StartupStreams $stdout $stderr 'frontend'
    $proof['elapsed_ms'] = $timer.ElapsedMilliseconds
    $proof['exit_code'] = $frontend.ExitCode
    if ($frontend.ExitCode -ne 0) { throw "Packaged frontend exited with code $($frontend.ExitCode)." }
    $hostObservations = @()
    $versionObservations = @()
    foreach ($hostRecord in $tracked) {
        if (-not $hostRecord.Process.WaitForExit(5000)) {
            throw "A packaged process remained after the launcher exited: $($hostRecord.Executable)"
        }
        if ($hostRecord.Kind -ceq 'version') {
            $versionObservations += @{
                pid = $hostRecord.Id; executable = $hostRecord.Executable; command_line = $hostRecord.CommandLine
                exited = $true
            }
            continue
        }
        if ($hostRecord.CommandLine) {
            # The bundled version resolves its own resources and passes them to
            # the Rust host together with the forwarded isolated paths.
            $resources = Join-Path ([IO.Path]::GetDirectoryName($hostRecord.Executable)) 'app'
            foreach ($expectedPath in @($resources, $data, $config)) {
                if (-not $hostRecord.CommandLine.Contains($expectedPath, [StringComparison]::OrdinalIgnoreCase)) {
                    throw "The packaged version did not forward its isolated or bundled path to the host: $expectedPath"
                }
            }
        }
        $hostExitCode = $hostRecord.Process.ExitCode
        if ($null -ne $hostExitCode -and $hostExitCode -ne 0) { throw "The packaged Rust host exited with code $hostExitCode." }
        $hostObservations += @{
            pid = $hostRecord.Id; executable = $hostRecord.Executable; command_line = $hostRecord.CommandLine
            exited = $true; exit_code = $hostExitCode
        }
    }
    $proof['version_observations'] = $versionObservations
    $proof['host_observations'] = $hostObservations
    $proof['host_observation_method'] = 'Optional running-child samples of the forwarded version and its Rust host plus a required child query after frontend exit. A missed short-lived child or unavailable exit code does not fail the functional startup check.'
    $proof['post_exit_child_query_succeeded'] = $true
    if (-not (Test-Path -LiteralPath $screenshot -PathType Leaf) -or -not (Test-Path -LiteralPath "$screenshot.json" -PathType Leaf)) {
        throw 'Frontend exited without both its startup screenshot and diagnostic JSON.'
    }
    Copy-Item -LiteralPath $screenshot -Destination (Join-Path $output 'startup.png')
    Copy-Item -LiteralPath "$screenshot.json" -Destination (Join-Path $output 'startup.png.json')
    $state = Get-Content -LiteralPath "$screenshot.json" -Raw | ConvertFrom-Json -AsHashtable
    [IO.File]::WriteAllLines((Join-Path $output 'host.log'), [string[]]@($state['logs']))
    Assert-StartupSnapshot $state
    $mesh = [IO.Path]::GetFullPath($state['document']['mesh_path'])
    $meshRelative = [IO.Path]::GetRelativePath($data, $mesh)
    if ($meshRelative.StartsWith('..') -or [IO.Path]::IsPathRooted($meshRelative) -or -not (Test-Path -LiteralPath $mesh -PathType Leaf)) {
        throw 'Generated mesh was not written inside the isolated data directory.'
    }
    $png = [IO.File]::ReadAllBytes($screenshot)
    if ($png.Length -lt 24 -or [Convert]::ToHexString($png[0..7]) -ne '89504E470D0A1A0A') {
        throw 'The startup screenshot is not a valid PNG header.'
    }
    if ((Get-FileHash -LiteralPath $config).Hash -cne $configHash) { throw 'Startup unexpectedly modified the supplied isolated settings.' }
    Copy-Item -LiteralPath $config -Destination (Join-Path $output 'isolated-config.json')
    $proof['model'] = @{ features = @($state['document']['features']).Count; triangles = $state['document']['stats']['triangles']; size = $state['document']['stats']['size']; mesh_sha256 = (Get-FileHash -LiteralPath $mesh).Hash.ToLowerInvariant() }
    $proof['ready'] = $state['ready']
    $proof['viewport_error'] = $state['viewport_error']
    $proof['config_unchanged'] = $true
    $proof['screenshot_sha256'] = (Get-FileHash -LiteralPath $screenshot).Hash.ToLowerInvariant()
    $proof['passed'] = $true
} catch {
    $failure = $_
    $proof['error'] = $_.Exception.Message
} finally {
    foreach ($process in @($frontend, $versionProcess)) {
        if ($process -and -not $process.HasExited) { $process.Kill($true); $null = $process.WaitForExit(10000) }
    }
    $residuals = @()
    foreach ($record in $tracked) {
        if (-not $record.Process.HasExited) {
            $residuals += $record.Id
            # Revalidate identity before a cleanup kill, preventing PID reuse
            # from affecting a different application.
            if ($record.Process.StartTime.ToUniversalTime() -eq $record.StartedAt -and
                $record.Process.MainModule.FileName -ieq $record.Executable) {
                $record.Process.Kill($true)
                $null = $record.Process.WaitForExit(10000)
            }
        }
        $record.Process.Dispose()
    }
    foreach ($process in @($frontend, $versionProcess)) { if ($process) { $process.Dispose() } }
    foreach ($streams in @(@($versionOutput, $versionError, 'version'), @($stdout, $stderr, 'frontend'))) {
        if ($null -ne $streams[0] -and $null -ne $streams[1]) {
            try { Write-StartupStreams $streams[0] $streams[1] $streams[2] }
            catch { $proof["$($streams[2])_log_error"] = $_.Exception.Message }
        }
    }
    $proof['residual_host_pids_before_cleanup'] = $residuals
    foreach ($copy in $startupArtifactCopies) {
        if (Test-Path -LiteralPath $copy) { Remove-Item -LiteralPath $copy -Force }
    }
    $proof['startup_artifacts_relocated'] = @($startupArtifactCopies | ForEach-Object { Split-Path -Leaf $_ })
    $proof['startup_artifacts_removed'] = @($startupArtifactCopies | Where-Object { Test-Path -LiteralPath $_ }).Count -eq 0
    $proof['removed_environment_names'] = @($removedEnvironment | Sort-Object)
    foreach ($item in @(@($screenshot, 'startup.png'), @("$screenshot.json", 'startup.png.json'), @($config, 'isolated-config.json'))) {
        if (Test-Path -LiteralPath $item[0] -PathType Leaf) { Copy-Item -LiteralPath $item[0] -Destination (Join-Path $output $item[1]) -Force }
    }
    $resolvedScratch = (Resolve-Path -LiteralPath $scratch).Path
    if ([IO.Path]::GetFullPath($resolvedScratch) -ne $scratch -or
        [IO.Path]::GetDirectoryName($scratch) -ne $temporaryRoot -or
        (Get-Item -LiteralPath $scratch).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
        throw 'Refusing to remove a startup verification directory outside its validated temporary path.'
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
    $proof['scratch_removed'] = -not (Test-Path -LiteralPath $scratch)
    $proof | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $output 'verification.json') -Encoding utf8NoBOM
}
if ($failure) { throw $failure }
Write-Host "Verified packaged FormRecall $Version startup, two features and 604 triangles. Evidence: $output"
