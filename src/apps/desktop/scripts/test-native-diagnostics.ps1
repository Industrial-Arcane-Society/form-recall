$ErrorActionPreference = 'Stop'

$desktopRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$coreRoot = Join-Path $desktopRoot '../../core/cad-engine'
$cargoManifest = Join-Path $coreRoot 'Cargo.toml'
$compileOutput = @(& cargo test --manifest-path $cargoManifest --lib --no-run --message-format=json --color=never 2>&1)
$compileExitCode = $LASTEXITCODE
if ($compileExitCode -ne 0) {
    $compileOutput | ForEach-Object { Write-Output $_ }
    throw "Rust core test compilation failed with exit code $compileExitCode"
}

$testArtifact = $compileOutput | ForEach-Object {
    try { $_ | ConvertFrom-Json -AsHashtable } catch { $null }
} | Where-Object {
    $_ -and $_.reason -eq 'compiler-artifact' -and
    $_.target.name -eq 'formrecall_cad_engine' -and $_.profile.test -and $_.executable
} | Select-Object -Last 1
if (-not $testArtifact) {
    throw 'Cargo completed but did not report the formrecall_cad_engine test executable.'
}
$testExecutable = $testArtifact.executable

foreach ($filter in @('native_diagnostics::tests', 'native_bootstrap::tests')) {
    $testList = @(& $testExecutable $filter --list)
    if ($LASTEXITCODE -ne 0 -or -not ($testList -match ': test$')) {
        throw "Rust core test filter matched no runnable tests: $filter"
    }
    & $testExecutable $filter --nocapture
    if ($LASTEXITCODE -ne 0) {
        throw "Rust core tests failed for '$filter' with exit code $LASTEXITCODE"
    }
}

Write-Output 'Rust core diagnostics and bootstrap tests passed headlessly.'
