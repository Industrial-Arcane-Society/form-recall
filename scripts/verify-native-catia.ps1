param([Parameter(Mandatory)][string]$BundleDirectory, [string]$OcctManifest)
. "$PSScriptRoot/native-common.ps1"
$bundleDirectory = (Resolve-Path -LiteralPath $BundleDirectory).Path
Assert-NativeCatiaResources $bundleDirectory
$runtimeManifest = Get-NativeOcctManifest $OcctManifest
$scratch = [IO.Path]::GetFullPath((Join-Path ([IO.Path]::GetTempPath()) ('formrecall-catia-check-' + [Guid]::NewGuid().ToString('N'))))
New-Item -ItemType Directory -Path $scratch | Out-Null
$startInfo = [Diagnostics.ProcessStartInfo]::new((Join-Path $bundleDirectory 'formrecall-engine-host.exe'))
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardInput = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$startInfo.StandardInputEncoding = [Text.UTF8Encoding]::new($false)
$startInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$startInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
foreach ($argument in @('--resources', (Join-Path $bundleDirectory 'app'), '--data', $scratch, '--config', (Join-Path $scratch 'config.json'))) {
    $startInfo.ArgumentList.Add($argument)
}
$startInfo.Environment['AIMODELER_OCCT_MANIFEST'] = $runtimeManifest
$process = $null
try {
    $process = [Diagnostics.Process]::Start($startInfo)
    $errors = $process.StandardError.ReadToEndAsync()
    function Invoke-CatiaCheckEngine {
        param([long]$Id, [string]$Method, [hashtable]$Parameters = @{}, [switch]$AllowError)
        $process.StandardInput.WriteLine((@{ id = $Id; method = $Method; params = $Parameters } | ConvertTo-Json -Depth 8 -Compress))
        $deadline = [DateTime]::UtcNow.AddMinutes(2)
        while ($true) {
            $line = $process.StandardOutput.ReadLineAsync()
            $remaining = [int][Math]::Max(1, ($deadline - [DateTime]::UtcNow).TotalMilliseconds)
            if (-not $line.Wait($remaining)) { throw "CATIA runtime verification timed out during $Method" }
            if ($null -eq $line.Result) { throw "Engine closed during CATIA runtime verification of $Method" }
            $reply = $line.Result | ConvertFrom-Json
            if ($reply.event) { continue }
            if ($reply.id -ne $Id) { throw "Unexpected engine reply during CATIA runtime verification of $Method`: $($reply | ConvertTo-Json -Depth 6 -Compress)" }
            if ($AllowError) { return $reply }
            if ($reply.error) { throw "CATIA runtime verification $Method failed: $($reply.error)" }
            return $reply.result
        }
    }
    $handshake = Invoke-CatiaCheckEngine 1 'handshake'
    $null = Invoke-CatiaCheckEngine 2 'quick_build' @{ text = '长方体 20x15x8'; revision = 1 }
    $privateRuntime = Join-Path $scratch 'runtime/catia-python'
    if (Test-Path -LiteralPath $privateRuntime) { throw 'CATIA runtime was installed before a CATIA operation.' }
    # The host prepares the real private runtime before the adapter validates
    # the output suffix. This exercises installation without starting COM.
    $parameters = @{ kind = 'catia'; path = (Join-Path $scratch 'install-probe.invalid') }
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $installationReply = Invoke-CatiaCheckEngine 3 'active_model_export' $parameters -AllowError
    $timer.Stop()
    if ($installationReply.error -notlike '*CATIA 输出路径必须使用*') {
        throw "CATIA runtime installation did not reach adapter output validation: $($installationReply | ConvertTo-Json -Compress)"
    }
    $markerPath = Join-Path $privateRuntime '.catia-python-runtime.json'
    $marker = Get-Content -LiteralPath $markerPath -Raw | ConvertFrom-Json
    $manifest = Get-Content -LiteralPath (Join-Path $bundleDirectory 'catia-bootstrap/catia-runtime-manifest.json') -Raw | ConvertFrom-Json
    foreach ($field in @('pythonVersion', 'requirementsSha256', 'pywin32WheelSha256')) {
        if ($marker.$field -cne $manifest.$field) { throw "Installed CATIA runtime marker mismatch: $field" }
    }
    $markerWrittenAt = (Get-Item -LiteralPath $markerPath).LastWriteTimeUtc
    $repeated = Invoke-CatiaCheckEngine 4 'active_model_export' $parameters -AllowError
    if ($repeated.error -notlike '*CATIA 输出路径必须使用*' -or
        (Get-Item -LiteralPath $markerPath).LastWriteTimeUtc -ne $markerWrittenAt) {
        throw 'Repeated CATIA runtime preparation failed or replaced a valid installation.'
    }
    $python = Join-Path $privateRuntime 'python.exe'
    $pythonCheck = @'
import importlib.metadata, json, struct, sys, winreg
import pythoncom, win32com.client
try:
    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"CATIA.Application\CLSID") as key:
        catia_clsid = winreg.QueryValueEx(key, "")[0]
except FileNotFoundError:
    catia_clsid = None
print(json.dumps({"python_version": sys.version.split()[0], "bits": struct.calcsize("P") * 8,
                  "executable": sys.executable, "pywin32_version": importlib.metadata.version("pywin32"),
                  "pythoncom_loaded": True, "win32com_loaded": True,
                  "catia_registered": bool(catia_clsid), "catia_clsid": catia_clsid}))
'@
    $pythonInfo = [Diagnostics.ProcessStartInfo]::new($python)
    $pythonInfo.UseShellExecute = $false
    $pythonInfo.CreateNoWindow = $true
    $pythonInfo.RedirectStandardOutput = $true
    $pythonInfo.RedirectStandardError = $true
    foreach ($argument in @('-I', '-B', '-c', $pythonCheck)) { $pythonInfo.ArgumentList.Add($argument) }
    $pythonProcess = [Diagnostics.Process]::Start($pythonInfo)
    try {
        $pythonOutput = $pythonProcess.StandardOutput.ReadToEndAsync()
        $pythonErrors = $pythonProcess.StandardError.ReadToEndAsync()
        if (-not $pythonProcess.WaitForExit(30000)) { throw 'Installed CATIA Python import verification timed out.' }
        if ($pythonProcess.ExitCode -ne 0) { throw "Installed CATIA Python failed: $($pythonErrors.Result)" }
        $probe = $pythonOutput.Result | ConvertFrom-Json
    } finally {
        if (-not $pythonProcess.HasExited) { $pythonProcess.Kill($true); $pythonProcess.WaitForExit() }
        $pythonProcess.Dispose()
    }
    if ($probe.python_version -cne '3.13.16' -or $probe.bits -ne 64 -or $probe.pywin32_version -cne '312' -or
        [IO.Path]::GetFullPath($probe.executable) -ne [IO.Path]::GetFullPath($python)) {
        throw 'CATIA Python import verification used an unexpected interpreter or version.'
    }
    $null = Invoke-CatiaCheckEngine 5 'shutdown'
    if (-not $process.WaitForExit(10000) -or $process.ExitCode -ne 0) { throw 'CATIA verification host did not shut down cleanly.' }
    $proof = @{
        schema = 1; verified_at = [DateTime]::UtcNow.ToString('o'); bundle = $bundleDirectory
        engine_sha256 = (Get-FileHash -LiteralPath (Join-Path $bundleDirectory 'formrecall-engine-host.exe')).Hash.ToLowerInvariant()
        catia_manifest_sha256 = (Get-FileHash -LiteralPath (Join-Path $bundleDirectory 'catia-bootstrap/catia-runtime-manifest.json')).Hash.ToLowerInvariant()
        handshake = $handshake; installation_ms = $timer.ElapsedMilliseconds
        installation_trigger = 'active_model_export_output_validation'; installation_reply = $installationReply
        installed = $true; repeated_install_reused = $true; install_marker = $marker; python = $probe
        catia_export_verified = $false; catia_registration_checked = $true
    }
    $proofPath = Join-Path $script:RepositoryRoot 'temp/outputs/native-catia-verification.json'
    New-Item -ItemType Directory -Path (Split-Path $proofPath -Parent) -Force | Out-Null
    $proof | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $proofPath -Encoding utf8NoBOM
    Write-Host "Verified private CATIA Python installation and repeated preparation in $($timer.ElapsedMilliseconds) ms. CATIA COM registered: $($probe.catia_registered)."
} finally {
    if ($process) {
        if (-not $process.HasExited) { $process.Kill($true); $process.WaitForExit() }
        $process.Dispose()
    }
    $resolvedScratch = (Resolve-Path -LiteralPath $scratch).Path
    if ([IO.Path]::GetFullPath($resolvedScratch) -ne $scratch -or
        [IO.Path]::GetDirectoryName($scratch) -ne [IO.Path]::GetTempPath().TrimEnd('\', '/')) {
        throw 'Refusing to remove a CATIA verification directory outside the temporary directory.'
    }
    Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
}
