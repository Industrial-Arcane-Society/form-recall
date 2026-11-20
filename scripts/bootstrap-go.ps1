# Uses the same pinned Go release as the requested MyGo architecture template.
. "$PSScriptRoot/native-common.ps1"
$version = '1.27.4'
$sha256 = '178ab98950ac69ab25069307dc6ade79f3816e414cce95b4da166a115cde1e7e'
$destination = Join-Path $script:RepositoryRoot '.tools'
$executable = Join-Path $destination 'go/bin/go.exe'
if (Test-Path -LiteralPath $executable) {
    $actual = & $executable version
    if ($LASTEXITCODE -ne 0 -or $actual -notmatch 'go1\.27\.4 windows/amd64') {
        throw 'The project SDK differs from Go 1.27.4 Windows amd64. Inspect .tools/go before replacing it.'
    }
    Write-Host $actual
    return
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
$archive = Join-Path $destination "go-sdk-$version.zip"
if (-not (Test-Path -LiteralPath $archive)) {
    $download = "$archive.download"
    Invoke-NativeTool curl.exe @('--fail', '--location', '--retry', '3', "https://go.dev/dl/go$version.windows-amd64.zip", '--output', $download)
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha256) {
        throw 'Downloaded Go SDK checksum mismatch. Retry bootstrap after checking the download source.'
    }
    Move-Item -LiteralPath $download -Destination $archive -Force
}
if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha256) {
    throw "Go SDK checksum mismatch. Inspect or remove $archive before retrying."
}
Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
Invoke-NativeTool $executable @('version')
