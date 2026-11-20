param([switch]$Offline, [switch]$SkipRust, [switch]$SkipGo, [string]$RustFilter = '')
. "$PSScriptRoot/native-common.ps1"
if ($SkipRust -and $SkipGo) { throw 'At least one test suite must run.' }
if (-not $SkipGo) {
    Invoke-NativeGo @('mod', 'verify') -Offline:$Offline
    Invoke-NativeGo @('vet', '-mod=readonly', './...') -Offline:$Offline
    Invoke-NativeGo @('test', '-mod=readonly', './...') -Offline:$Offline
}
if (-not $SkipRust) {
    $arguments = @('test', '--locked', '--manifest-path', $script:CoreManifest, '--target-dir', (Get-NativeTarget), '--lib')
    if ($Offline) { $arguments += '--offline' }
    if ($RustFilter) { $arguments += $RustFilter }
    Invoke-NativeTool (Get-NativeCargo) $arguments
    $arguments = @('test', '--locked', '--manifest-path', $script:EngineManifest, '--target-dir', (Get-NativeTarget))
    if ($Offline) { $arguments += '--offline' }
    Invoke-NativeTool (Get-NativeCargo) $arguments
}
