param([switch]$SkipBuild, [switch]$DebugBuild, [switch]$Offline, [string]$OcctManifest, [string]$DataDirectory)
. "$PSScriptRoot/native-common.ps1"
if (-not $SkipBuild) { & "$PSScriptRoot/build-native.ps1" -DebugBuild:$DebugBuild -Offline:$Offline }
$output = Get-NativeOutput -DebugBuild:$DebugBuild
$executable = Join-Path $output 'FormRecall.exe'
if (-not (Test-Path -LiteralPath $executable)) { throw "Build output does not exist: $executable" }
$engine = Join-Path $output 'formrecall-engine-host.exe'
if (-not (Test-Path -LiteralPath $engine -PathType Leaf)) { throw "Engine build output does not exist: $engine" }
$runtimeManifest = Get-NativeOcctManifest $OcctManifest
$resources = Initialize-NativeRunResources -BundleDirectory $output -Offline:$Offline
$variables = @('FORMRECALL_ENGINE', 'AIMODELER_RESOURCE_ROOT', 'AIMODELER_OCCT_MANIFEST', 'AIMODELER_DESKTOP_DATA_DIR')
$previous = @{}
foreach ($name in $variables) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
try {
    $env:FORMRECALL_ENGINE = $engine
    $env:AIMODELER_RESOURCE_ROOT = $resources
    $env:AIMODELER_OCCT_MANIFEST = $runtimeManifest
    if ($DataDirectory) { $env:AIMODELER_DESKTOP_DATA_DIR = [IO.Path]::GetFullPath($DataDirectory) }
    Start-Process -FilePath $executable -WorkingDirectory $output
} finally {
    foreach ($name in $variables) {
        if ($null -eq $previous[$name]) {
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        } else {
            [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
        }
    }
}
