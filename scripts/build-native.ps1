param([switch]$DebugBuild, [switch]$Offline, [string]$Version)
. "$PSScriptRoot/native-common.ps1"
if ($Version) {
    $Version = $Version -creplace '^v', ''
    if ($Version -cnotmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$') {
        throw 'Version must be a semantic version such as 0.2.0 or 0.2.0-rc.1.'
    }
}
$output = Get-NativeOutput -DebugBuild:$DebugBuild
$target = Get-NativeTarget
New-Item -ItemType Directory -Path $output -Force | Out-Null
$profile = if ($DebugBuild) { 'debug' } else { 'release' }
$cargoArguments = @('build', '--locked', '--manifest-path', $script:EngineManifest, '--target-dir', $target, '--bin', 'formrecall-engine-host')
if (-not $DebugBuild) { $cargoArguments += '--release' }
if ($Offline) { $cargoArguments += '--offline' }
Invoke-NativeTool (Get-NativeCargo) $cargoArguments
$goArguments = @('build', '-mod=readonly', '-trimpath', '-o', (Join-Path $output 'FormRecall.exe'))
$linkerFlags = @()
if (-not $DebugBuild) { $linkerFlags += '-s -w -H windowsgui' }
if ($Version) { $linkerFlags += "-X main.version=$Version" }
if ($linkerFlags.Count -gt 0) { $goArguments += @('-ldflags', ($linkerFlags -join ' ')) }
$goArguments += './cmd/formrecall'
Invoke-NativeGo $goArguments -Offline:$Offline
Copy-Item -LiteralPath (Join-Path $target "$profile/formrecall-engine-host.exe") -Destination $output -Force
Write-Host "Built $output"
