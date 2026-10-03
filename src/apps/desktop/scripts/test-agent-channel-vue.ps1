$ErrorActionPreference = 'Stop'

# Tauri's MockRuntime test executable needs the v6 Common-Controls activation
# context so Windows resolves TaskDialogIndirect from the WinSxS v6 assembly.

$desktopRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tauriRoot = Join-Path $desktopRoot 'src-tauri'
$cargoManifest = Join-Path $tauriRoot 'Cargo.toml'
$commonControlsManifest = Join-Path $tauriRoot 'tests\common-controls-v6.manifest'
$testName = 'agent_ipc_integration_tests::agent_send_routes_fake_acp_updates_through_tauri_channel'

if (-not (Test-Path -LiteralPath $commonControlsManifest)) {
    throw "Missing Common-Controls v6 manifest: $commonControlsManifest"
}

$compileOutput = @(& cargo test --manifest-path $cargoManifest --lib $testName --no-run --message-format=json --color=never 2>&1)
$compileExitCode = $LASTEXITCODE
if ($compileExitCode -ne 0) {
    $compileOutput | ForEach-Object { Write-Output $_ }
    throw "Rust test compilation failed with exit code $compileExitCode"
}

$testArtifact = $compileOutput | ForEach-Object {
    try { $_ | ConvertFrom-Json -AsHashtable } catch { $null }
} | Where-Object {
    $_ -and $_.reason -eq 'compiler-artifact' -and
    $_.target.name -eq 'ai_modeler_ui_lib' -and $_.profile.test -and $_.executable
} | Select-Object -Last 1

if (-not $testArtifact) {
    throw 'Cargo completed but did not report the ai_modeler_ui_lib test executable.'
}
$testExecutable = $testArtifact.executable

$manifestTool = (Get-Command mt.exe -ErrorAction SilentlyContinue)?.Source
if (-not $manifestTool) {
    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $manifestTool = Get-ChildItem -LiteralPath $kitsRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'x64\mt.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Select-Object -First 1
}
if (-not $manifestTool) {
    throw 'Windows SDK mt.exe was not found; it is required to embed the Common-Controls v6 test manifest.'
}

& $manifestTool -manifest $commonControlsManifest "-outputresource:$testExecutable;#1"
if ($LASTEXITCODE -ne 0) {
    throw "Embedding the Common-Controls v6 manifest failed with exit code $LASTEXITCODE"
}

$rustOutput = @(& $testExecutable $testName --exact --nocapture 2>&1)
$rustExitCode = $LASTEXITCODE
$rustOutput | ForEach-Object {
    $line = [string]$_
    if (-not $line.StartsWith('AIMODELER_AGENT_IPC_CAPTURE=')) {
        Write-Output $line
    }
}
if ($rustExitCode -ne 0) {
    throw "MockRuntime Rust event test failed with exit code $rustExitCode"
}

$captureLine = $rustOutput | ForEach-Object { [string]$_ } |
    Where-Object { $_.StartsWith('AIMODELER_AGENT_IPC_CAPTURE=') } |
    Select-Object -Last 1
if (-not $captureLine) {
    throw 'Rust test passed but did not print its Channel event capture.'
}
$captureJson = $captureLine.Substring('AIMODELER_AGENT_IPC_CAPTURE='.Length)

$previousCapture = [Environment]::GetEnvironmentVariable('AIMODELER_AGENT_IPC_CAPTURE', 'Process')
try {
    [Environment]::SetEnvironmentVariable('AIMODELER_AGENT_IPC_CAPTURE', $captureJson, 'Process')
    Push-Location $desktopRoot
    try {
        & node 'src/App.agentEvents.integration.test.mjs'
        if ($LASTEXITCODE -ne 0) {
            throw "Production Vue headless integration failed with exit code $LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}
finally {
    [Environment]::SetEnvironmentVariable('AIMODELER_AGENT_IPC_CAPTURE', $previousCapture, 'Process')
}

Write-Output 'BOUNDARY: MockRuntime command + Channel JSON capture → production Vue handler tested; WebView JS delivery and live DSH ACP are not tested.'
