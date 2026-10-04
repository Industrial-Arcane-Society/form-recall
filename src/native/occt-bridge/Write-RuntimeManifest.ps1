param(
    [Parameter(Mandatory = $true)][string]$RuntimeRoot
)
$ErrorActionPreference = 'Stop'
$runtimeDirectory = (Resolve-Path -LiteralPath $RuntimeRoot).Path
function Get-Artifact([string]$relativePath) {
    $absolutePath = Join-Path $runtimeDirectory $relativePath
    if (-not (Test-Path -LiteralPath $absolutePath -PathType Leaf)) {
        throw "Missing runtime artifact: $relativePath"
    }
    [ordered]@{
        path = $relativePath
        sha256 = (Get-FileHash -LiteralPath $absolutePath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$runtimeBins = @(Get-ChildItem -LiteralPath (Join-Path $runtimeDirectory 'install/win64') -Directory -Filter 'vc*' |
    ForEach-Object { Join-Path $_.FullName 'bin' } |
    Where-Object { Test-Path -LiteralPath (Join-Path $_ 'occt_bridge.dll') })
if ($runtimeBins.Count -ne 1) {
    throw "Expected exactly one installed OCCT bin directory containing occt_bridge.dll; found $($runtimeBins.Count)."
}
$runtimeBin = [IO.Path]::GetRelativePath($runtimeDirectory, $runtimeBins[0]).Replace('\', '/')
$occtNames = @('TKBO', 'TKBool', 'TKBRep', 'TKernel', 'TKFillet', 'TKG2d', 'TKG3d', 'TKGeomAlgo', 'TKGeomBase', 'TKMath', 'TKMesh', 'TKOffset', 'TKPrim', 'TKShHealing', 'TKTopAlgo')
$manifest = [ordered]@{
    occt_version = '7.9.3'
    abi_version = 1
    bridge_abi_version = 1
    source_tag = 'V7_9_3'
    source_commit = 'a016080bf6738d6aeae020badee4e888ad1540a5'
    source_archive_url = 'https://github.com/Open-Cascade-SAS/OCCT/archive/refs/tags/V7_9_3.zip'
    source_archive_sha256 = '566a236b5a22e778dbe1c3dce6560ba12db4504bbdae0354c51dd2f4728756ee'
    bridge_artifacts = @(Get-Artifact "$runtimeBin/occt_bridge.dll")
    occt_runtime_dlls = @($occtNames | ForEach-Object { Get-Artifact "$runtimeBin/$_.dll" })
    msvc_runtime_dlls = @(@('msvcp140', 'vcruntime140', 'vcruntime140_1') | ForEach-Object { Get-Artifact "$runtimeBin/$_.dll" })
}
# Hashes describe the build output; this script does not certify the compiler
# or replace source archive verification / signed release provenance.
$manifestPath = Join-Path $runtimeDirectory 'runtime-manifest.json'
$manifest | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $manifestPath -Encoding utf8
Write-Output $manifestPath
