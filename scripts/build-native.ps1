param([switch]$DebugBuild, [switch]$Offline)
. "$PSScriptRoot/native-common.ps1"
$output = Get-NativeOutput -DebugBuild:$DebugBuild
$target = Get-NativeTarget
New-Item -ItemType Directory -Path $output -Force | Out-Null
$profile = if ($DebugBuild) { 'debug' } else { 'release' }
$cargoArguments = @('build', '--locked', '--manifest-path', $script:EngineManifest, '--target-dir', $target, '--bin', 'formrecall-engine-host')
if (-not $DebugBuild) { $cargoArguments += '--release' }
if ($Offline) { $cargoArguments += '--offline' }
Invoke-NativeTool (Get-NativeCargo) $cargoArguments
$goArguments = @('build', '-mod=readonly', '-trimpath', '-o', (Join-Path $output 'FormRecall.exe'))
if (-not $DebugBuild) { $goArguments += @('-ldflags', '-s -w -H windowsgui') }
$goArguments += './cmd/formrecall'
Invoke-NativeGo $goArguments -Offline:$Offline
Copy-Item -LiteralPath (Join-Path $target "$profile/formrecall-engine-host.exe") -Destination $output -Force
Write-Host "Built $output"
