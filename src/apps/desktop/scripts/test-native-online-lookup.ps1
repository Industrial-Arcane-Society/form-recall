$ErrorActionPreference = 'Stop'

$desktopRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$tauriRoot = Join-Path $desktopRoot 'src-tauri'
$cargoManifest = Join-Path $tauriRoot 'Cargo.toml'
$commonControlsManifest = Join-Path $tauriRoot 'tests\common-controls-v6.manifest'
if (-not (Test-Path -LiteralPath $commonControlsManifest)) {
    throw "Missing Common-Controls v6 manifest: $commonControlsManifest"
}

# The desktop test executable needs Common Controls v6 embedded on Windows.
# Avoid staging bundled runtimes for a headless unit test; the temporary Cargo
# build configuration clears only the build-time resource list.
$previousTauriConfig = [Environment]::GetEnvironmentVariable('TAURI_CONFIG', 'Process')
try {
    $testTauriConfig = if ($previousTauriConfig) {
        ConvertFrom-Json -InputObject $previousTauriConfig -AsHashtable
    }
    else {
        @{}
    }
    if (-not $testTauriConfig.Contains('bundle') -or $null -eq $testTauriConfig['bundle']) {
        $testTauriConfig['bundle'] = @{}
    }
    $testTauriConfig['bundle']['resources'] = @()
    [Environment]::SetEnvironmentVariable('TAURI_CONFIG', ($testTauriConfig | ConvertTo-Json -Compress -Depth 100), 'Process')
    $compileOutput = @(& cargo test --manifest-path $cargoManifest --lib native_online_lookup::tests --no-run --message-format=json --color=never 2>&1)
    $compileExitCode = $LASTEXITCODE
}
finally {
    [Environment]::SetEnvironmentVariable('TAURI_CONFIG', $previousTauriConfig, 'Process')
}
if ($compileExitCode -ne 0) {
    $compileOutput | ForEach-Object { Write-Output $_ }
    throw "Rust native online lookup test compilation failed with exit code $compileExitCode"
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

& $testExecutable native_online_lookup::tests --nocapture
if ($LASTEXITCODE -ne 0) {
    throw "Rust native online lookup tests failed with exit code $LASTEXITCODE"
}
& $testExecutable native_web::tests::search_filters_unrelated_rows_before_legacy_manual_fallback --nocapture
if ($LASTEXITCODE -ne 0) {
    throw "Rust query-relevance compatibility test failed with exit code $LASTEXITCODE"
}

Write-Output 'BOUNDARY: Native online lookup compatibility and query-relevance tests passed headlessly with fixed fixtures/local mock HTTP only; no external network, WebView, DSH, CATIA, or provider was launched.'
