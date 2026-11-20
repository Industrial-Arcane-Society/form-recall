param(
    [Parameter(Mandatory)][string]$BundleDirectory,
    [Parameter(Mandatory)][string]$ExpectedVersion,
    [Parameter(Mandatory)][string]$ExpectedCommit,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [switch]$UseSoftwareOpenGL,
    [switch]$AllowDowngrade,
    [switch]$ContinueFromActive,
    [switch]$ExpectCrossChannelDelta
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$bundle = (Resolve-Path -LiteralPath $BundleDirectory).Path
$launcher = Join-Path $bundle 'FormRecall.exe'
$storage = Join-Path $bundle '.formrecall-updates'
$output = [IO.Path]::GetFullPath($OutputDirectory)
if ((-not $ContinueFromActive -and (Test-Path -LiteralPath $storage)) -or -not (Test-Path -LiteralPath $launcher -PathType Leaf)) {
    throw 'OTA verification requires a fresh isolated bundle.'
}
$sourceBundle = $bundle
if ($ContinueFromActive) {
    $current = Get-Content -LiteralPath (Join-Path $storage 'active.json') -Raw | ConvertFrom-Json -AsHashtable
    if ($current.current -notmatch '^release-\d+$') { throw 'Invalid active OTA slot.' }
    $sourceBundle = Join-Path $storage "versions/$($current.current)/payload"
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
$data = Join-Path $output 'session-data'
New-Item -ItemType Directory -Path $data | Out-Null
$sentinel = Join-Path $data 'retained.txt'
Set-Content -LiteralPath $sentinel -Value 'FormRecall OTA must preserve existing user files.'
$before = (Get-FileHash -LiteralPath $sentinel).Hash

function Invoke-Desktop([string[]]$Arguments, [int]$TimeoutSeconds = 150, [switch]$TraceNetwork) {
    $info = [Diagnostics.ProcessStartInfo]::new($launcher)
    $info.WorkingDirectory = $bundle
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    foreach ($name in @($info.Environment.Keys)) {
        if ($name -match '^(AIMODELER_|FORMRECALL_|MYGO_|NATIVE_VIEWPORT_|OCCT_|CSF_|PYTHON)' -or $name -eq 'CASROOT') {
            $null = $info.Environment.Remove($name)
        }
    }
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    if ($TraceNetwork) { $info.Environment['GODEBUG'] = 'http2debug=2' }
    $process = [Diagnostics.Process]::Start($info)
    try {
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { throw 'FormRecall OTA process timed out.' }
        $network = $stderr.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $line = @($network -split '\r?\n' | Where-Object { $_ -notmatch 'http2:' }) -join ' '
            throw "FormRecall exited with $($process.ExitCode). $line"
        }
        return @{ stdout = $stdout.GetAwaiter().GetResult(); network = $network }
    } finally {
        if (-not $process.HasExited) { $process.Kill($true); $null = $process.WaitForExit(10000) }
        $process.Dispose()
    }
}

$original = (Invoke-Desktop @('--version-json')).stdout | ConvertFrom-Json -AsHashtable
function Get-UpdateChannel([string]$Version) {
    if ($Version -cmatch '-(alpha|beta|rc)\.[1-9]\d*$') { return $Matches[1] }
    return 'stable'
}
$targetChannel = Get-UpdateChannel $ExpectedVersion
$crossChannel = (Get-UpdateChannel $original.version) -cne $targetChannel
if ($crossChannel) {
    $env:FORMRECALL_OTA_TEST_BUNDLE = $sourceBundle
    $env:FORMRECALL_OTA_TEST_VERSION = $original.version
    $env:FORMRECALL_OTA_TEST_CHANNEL = $targetChannel
    $env:FORMRECALL_OTA_TEST_RESULT = Join-Path $output 'stage.json'
    $env:FORMRECALL_OTA_TEST_DELTA = if ($ExpectCrossChannelDelta) { 'true' } else { 'false' }
    . "$PSScriptRoot/native-common.ps1"
    try {
        Invoke-NativeGo @('test', '-mod=readonly', './internal/update', '-run', '^TestStagePublishedChannel$', '-count=1', '-timeout=10m')
        $plan = Get-Content -LiteralPath $env:FORMRECALL_OTA_TEST_RESULT -Raw | ConvertFrom-Json -AsHashtable
    } finally {
        Remove-Item Env:FORMRECALL_OTA_TEST_BUNDLE,Env:FORMRECALL_OTA_TEST_VERSION,Env:FORMRECALL_OTA_TEST_CHANNEL,Env:FORMRECALL_OTA_TEST_RESULT,Env:FORMRECALL_OTA_TEST_DELTA
    }
    $archive = "FormRecall-$ExpectedVersion-windows-x64-portable.zip"
    $deltaRequested = $plan.delta
    $fullRequested = -not $plan.delta
    $actualDelta = $plan.delta
    if ($actualDelta -ne [bool]$ExpectCrossChannelDelta) { throw 'Unexpected cross-channel update package type.' }
} else {
    $stage = Invoke-Desktop @('--stage-update') -TimeoutSeconds 240 -TraceNetwork
    $plan = $stage.stdout | ConvertFrom-Json -AsHashtable
    $archive = "FormRecall-$($original.version)-to-$ExpectedVersion-windows-x64-ota.zip"
    $deltaRequested = $stage.network.Contains($archive)
    $fullRequested = $stage.network.Contains("FormRecall-$ExpectedVersion-windows-x64-portable.zip")
    $actualDelta = if ($plan.ContainsKey('delta')) { $plan.delta -eq $true } else { $deltaRequested -and -not $fullRequested }
    if (-not $actualDelta) { throw "Expected a real OTA download without full fallback. OTA requested=$deltaRequested, full requested=$fullRequested." }
}
if ($plan.version -cne $ExpectedVersion -or $plan.slot -notmatch '^release-\d+$') { throw 'Unexpected staged OTA target.' }
$candidate = Join-Path $storage "versions/$($plan.slot)/payload"
if ((Invoke-Desktop @('--version-json')).stdout | ConvertFrom-Json | Where-Object version -ne $original.version) {
    throw 'Staging changed the active version before restart.'
}
$driver = $null
if ($UseSoftwareOpenGL) {
    $driver = & "$PSScriptRoot/install-ci-opengl.ps1" -DestinationDirectory $candidate
}
$previousDriver = $env:GALLIUM_DRIVER
if ($UseSoftwareOpenGL) { $env:GALLIUM_DRIVER = 'llvmpipe' }
try {
    $screenshot = Join-Path $output 'updated-desktop.png'
    $config = Join-Path $data 'config.json'
    Set-Content -LiteralPath $config -Value '{"llm":{"enabled":false},"online":{"enabled":false}}' -Encoding utf8NoBOM
    $applyArgs = @("--apply-update=$($plan.slot)", "--data=$data", "--config=$config", "--screenshot=$screenshot", '--exit-after=700ms',
        '--width=1040', '--height=780', '--demo-prompt=底板 长100 宽80 厚10，四角通孔 直径8 边距10')
    if ($AllowDowngrade) { $applyArgs += '--channel-downgrade' }
    $null = Invoke-Desktop $applyArgs
    $deadline = [DateTime]::UtcNow.AddSeconds(80)
    while ((-not (Test-Path -LiteralPath $screenshot)) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 150 }
    if (-not (Test-Path -LiteralPath $screenshot)) { throw 'Updated desktop did not finish the modelling test.' }
    $smoke = Get-Content -LiteralPath "$screenshot.json" -Raw | ConvertFrom-Json -AsHashtable
    if (-not $smoke.ready -or $smoke.failure -or $smoke.viewport_error -or @($smoke.document.features).Count -ne 2) {
        throw 'Updated desktop did not render the four-hole plate.'
    }
    $identity = (Invoke-Desktop @('--version-json')).stdout | ConvertFrom-Json -AsHashtable
    if ($identity.version -cne $ExpectedVersion -or $identity.commit -cne $ExpectedCommit) {
        throw 'The fixed launcher does not select the expected updated version.'
    }
    if ((Get-FileHash -LiteralPath $sentinel).Hash -cne $before) { throw 'OTA changed isolated user data.' }
    $active = Get-Content -LiteralPath (Join-Path $storage 'active.json') -Raw | ConvertFrom-Json -AsHashtable
    if ($active.current -cne $plan.slot) { throw 'Launcher did not commit the staged slot.' }
    # Let the capture instance shut down before an optional uninstall.
    $targetExe = Join-Path $candidate 'FormRecall.exe'
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while ((Get-Process -Name FormRecall -ErrorAction SilentlyContinue | Where-Object Path -EQ $targetExe) -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Milliseconds 150
    }
    if (Get-Process -Name FormRecall -ErrorAction SilentlyContinue | Where-Object Path -EQ $targetExe) {
        throw 'Updated capture process remained open.'
    }
    [ordered]@{
        passed = $true
        before = $original
        after = $identity
        ota = $archive
        ota_bytes = $plan.bytes
        requested_ota = $deltaRequested
        requested_full_zip = $fullRequested
        actual_ota = $actualDelta
        cross_channel = $crossChannel
        data_preserved = $true
        features = @($smoke.document.features).Count
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $output 'ota-verification.json') -Encoding utf8NoBOM
} finally {
    $env:GALLIUM_DRIVER = $previousDriver
    if ($driver) {
        foreach ($file in $driver.files) {
            $testDriver = Join-Path $candidate $file.name
            if ((Get-FileHash -LiteralPath $testDriver).Hash -ine $file.sha256) { throw 'Verification OpenGL file changed.' }
            Remove-Item -LiteralPath $testDriver
        }
    }
}
