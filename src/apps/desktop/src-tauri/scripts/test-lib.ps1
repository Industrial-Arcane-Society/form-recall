param(
    [string]$Filter,
    [switch]$Exact,
    [switch]$Ignored,
    [switch]$NoCapture,
    [switch]$Offline
)

$ErrorActionPreference = 'Stop'
$crateRoot = Split-Path -Parent $PSScriptRoot
$previousTauriConfig = $env:TAURI_CONFIG
Push-Location $crateRoot

try {
    # Domain tests moved with the window-independent core. Keep this historical
    # command as a compatibility entry point for the complete Rust suite.
    if (-not $Filter -or $Filter -notlike 'agent_ipc_integration_tests*') {
        $coreManifest = Join-Path $crateRoot '../../../core/cad-engine/Cargo.toml'
        $coreArgs = @('test', '--manifest-path', $coreManifest, '--lib')
        if ($Offline) { $coreArgs += '--offline' }
        if ($Filter) { $coreArgs += $Filter }
        $coreArgs += '--'
        if ($Exact) { $coreArgs += '--exact' }
        if ($Ignored) { $coreArgs += '--ignored' }
        if ($NoCapture) { $coreArgs += '--nocapture' }
        & cargo @coreArgs
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    # Unit-test executables need Common Controls v6 for TaskDialogIndirect, but
    # Cargo's generated harness does not embed the application's activation manifest.
    # Keep the override local to this CLI test build; it skips package-only resources.
    $env:TAURI_CONFIG = '{"bundle":{"resources":[]}}'
    $cargoArgs = @('--manifest-path', 'Cargo.toml', '--lib', '--no-run')
    if ($Offline) {
        $cargoArgs += '--offline'
    }

    & cargo test @cargoArgs
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    $testDirectory = Join-Path $crateRoot 'target\debug'
    $testBinary = Get-ChildItem -LiteralPath $testDirectory -Filter 'ai_modeler_ui_lib-*.exe' -File -Recurse |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if (-not $testBinary) {
        throw "Cargo did not produce the Rust library test runner in $testDirectory."
    }

    $manifestPath = "$($testBinary.FullName).manifest"
    $createdManifest = $false
    if (-not (Test-Path -LiteralPath $manifestPath)) {
        $manifest = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0">
  <dependency>
    <dependentAssembly>
      <assemblyIdentity type="win32" name="Microsoft.Windows.Common-Controls" version="6.0.0.0" processorArchitecture="*" publicKeyToken="6595b64144ccf1df" language="*" />
    </dependentAssembly>
  </dependency>
</assembly>
"@
        [System.IO.File]::WriteAllText(
            $manifestPath,
            $manifest,
            [System.Text.UTF8Encoding]::new($false)
        )
        $createdManifest = $true
    }

    try {
        $runnerArgs = @()
        if ($Filter) {
            $runnerArgs += $Filter
        }
        if ($Exact) {
            $runnerArgs += '--exact'
        }
        if ($Ignored) {
            $runnerArgs += '--ignored'
        }
        if ($NoCapture) {
            $runnerArgs += '--nocapture'
        }
        & $testBinary.FullName @runnerArgs
        $testExitCode = $LASTEXITCODE
    }
    finally {
        if ($createdManifest) {
            Remove-Item -LiteralPath $manifestPath -ErrorAction SilentlyContinue
        }
    }

    exit $testExitCode
}
finally {
    if ($null -eq $previousTauriConfig) {
        Remove-Item Env:\TAURI_CONFIG -ErrorAction SilentlyContinue
    }
    else {
        $env:TAURI_CONFIG = $previousTauriConfig
    }
    Pop-Location
}
