param([switch]$SkipBuild, [switch]$DebugBuild, [switch]$Offline, [string]$OcctManifest)
. "$PSScriptRoot/native-common.ps1"
if (-not $SkipBuild) { & "$PSScriptRoot/build-native.ps1" -DebugBuild:$DebugBuild -Offline:$Offline }
$node = (Get-Command node -ErrorAction Stop).Source
$resourceArguments = @((Join-Path $script:RepositoryRoot 'src/legacy/scripts/build_desktop_resources.mjs'), '--native-resources')
if ($Offline) { $resourceArguments += '--offline' }
Invoke-NativeTool $node $resourceArguments
$dshResources = Join-Path $script:RepositoryRoot 'temp/build-cache/dsh-runtime-package/package-resources/app/packaging/dsh-runtime'
$preparedResources = Join-Path $script:RepositoryRoot 'temp/build-cache/dsh-runtime-package/package-resources'
$output = Get-NativeOutput -DebugBuild:$DebugBuild
$package = Join-Path $script:RepositoryRoot ('temp/outputs/native-package/FormRecall-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $package -Force | Out-Null
foreach ($binary in @('FormRecall.exe', 'formrecall-engine-host.exe')) {
    Copy-Item -LiteralPath (Join-Path $output $binary) -Destination $package
}
Copy-NativeResources (Join-Path $package 'app')
Copy-NativeCatiaResources $preparedResources $package
foreach ($resource in @('runtime-bundle-manifest.json', 'dsh-runtime-win-x64.zip')) {
    Copy-Item -LiteralPath (Join-Path $dshResources $resource) -Destination (Join-Path $package 'app/packaging/dsh-runtime')
}
Copy-NativeOcctRuntime (Get-NativeOcctManifest $OcctManifest) (Join-Path $package 'occt-runtime')
Copy-Item -LiteralPath (Join-Path $script:RepositoryRoot 'LICENSE') -Destination $package
Copy-Item -LiteralPath (Join-Path $script:NativeDesktopRoot 'THIRD_PARTY_NOTICES.md') -Destination $package
Copy-NativeGoLicenses (Join-Path $package 'licenses/go') -Offline:$Offline
$iconLicenseDirectory = Join-Path $package 'licenses/icons'
New-Item -ItemType Directory -Path $iconLicenseDirectory -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $script:NativeDesktopRoot 'internal/workbench/icons/LICENSE') -Destination (Join-Path $iconLicenseDirectory 'Lucide-LICENSE')
$start = @'
@echo off
setlocal DisableDelayedExpansion
set "FORMRECALL_ENGINE=%~dp0formrecall-engine-host.exe"
set "AIMODELER_RESOURCE_ROOT=%~dp0app"
set "AIMODELER_OCCT_MANIFEST=%~dp0occt-runtime\runtime-manifest.json"
start "" "%~dp0FormRecall.exe"
'@
$start.Replace("`r`n", "`n").Replace("`n", "`r`n") | Set-Content -LiteralPath (Join-Path $package 'Start-FormRecall.cmd') -Encoding ascii -NoNewline
$readme = @'
FormRecall 原生桌面版

双击 FormRecall.exe 或 Start-FormRecall.cmd 启动。适用于 Windows x64。
界面使用 MyGo，建模由同目录的 Rust 核心进程及 OCCT 完成。
默认用户资料保存在当前用户的本地应用数据目录。可通过 AIMODELER_DESKTOP_DATA_DIR 指定独立目录。
Quick 本地建模使用包内资源。Agent 需在设置中安装其私有 DSH 运行时并配置模型。
DSH 运行时已随包提供，安装过程从本地解压并校验 Node.js 和 DSH/ACP 依赖。
CATIA 的 Python 与 pywin32 运行时随包提供，首次导出时安装到用户私有目录。CATIA 操作还需要本机已注册的 CATIA COM。
软件及依赖许可见 LICENSE、THIRD_PARTY_NOTICES.md 和 occt-runtime/install。
'@
$readme | Set-Content -LiteralPath (Join-Path $package '使用说明.txt') -Encoding utf8NoBOM
$sourceCommit = & git -C $script:RepositoryRoot rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot record the source commit.' }
$sourceStatus = @(& git -C $script:RepositoryRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) { throw 'Cannot record the source tree status.' }
$record = @{
    schema = 1
    source_commit = $sourceCommit
    source_dirty = $sourceStatus.Count -gt 0
    source_tracked_dirty = @($sourceStatus | Where-Object { -not $_.StartsWith('?? ') }).Count -gt 0
    source_untracked_count = @($sourceStatus | Where-Object { $_.StartsWith('?? ') }).Count
    built_at = [DateTime]::UtcNow.ToString('o')
    debug = [bool]$DebugBuild
    files = @(Get-ChildItem -LiteralPath $package -File -Recurse | ForEach-Object {
        @{ path = [IO.Path]::GetRelativePath($package, $_.FullName).Replace('\', '/'); bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
    })
}
$record | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $package 'package-manifest.json') -Encoding utf8NoBOM
$archive = "$package-windows-x64.zip"
Compress-Archive -LiteralPath $package -DestinationPath $archive -CompressionLevel Optimal
& "$PSScriptRoot/verify-native-package.ps1" -PackageDirectory $package -ArchivePath $archive -InstallDshRuntime -InstallCatiaRuntime
Write-Host "Packaged $archive"
