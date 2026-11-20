param(
    [Parameter(Mandatory)][string]$PortableZip,
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][string]$Commit,
    [Parameter(Mandatory)][string]$OutputDirectory,
    [string]$PreviousZip,
    [string]$PreviousVersion
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$versionPattern = '^(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})(-alpha\.[1-9][0-9]{0,8})?$'
if ($Version -cnotmatch $versionPattern -or $Commit -cnotmatch '^[a-f0-9]{40}$' -or
    [bool]$PreviousZip -ne [bool]$PreviousVersion -or
    ($PreviousVersion -and $PreviousVersion -cnotmatch $versionPattern)) {
    throw 'OTA input must identify the exact version, source commit and optional previous ZIP.'
}
$out = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $out -Force | Out-Null
$targetArchive = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $PortableZip).Path)

function Read-Inventory($Archive, [string]$ReleaseVersion) {
    $prefix = "FormRecall-$ReleaseVersion-windows-x64/"
    $manifestEntry = $Archive.GetEntry($prefix + 'package-manifest.json')
    if (-not $manifestEntry -or $manifestEntry.Length -gt 1MB) { throw 'Portable package has no valid inventory.' }
    $reader = [IO.StreamReader]::new($manifestEntry.Open())
    try { $manifest = $reader.ReadToEnd() | ConvertFrom-Json -AsHashtable }
    finally { $reader.Dispose() }
    if ($manifest.schema -ne 1 -or $manifest.version -cne $ReleaseVersion -or $manifest.source_dirty -or
        $manifest.source_commit -cnotmatch '^[a-f0-9]{40}$') { throw 'Portable package inventory identity mismatch.' }

    $entries = @{}
    foreach ($entry in $Archive.Entries) {
        if (-not $entry.FullName.StartsWith($prefix, [StringComparison]::Ordinal) -or $entry.Name -eq '') { continue }
        $name = $entry.FullName.Substring($prefix.Length)
        if ($name -match '[\\:\x00-\x1f]|(^|/)\.{1,2}(/|$)' -or $name.StartsWith('/') -or $entries.ContainsKey($name)) {
            throw "Invalid or duplicate portable entry: $name"
        }
        $entries.Add($name, $entry)
    }
    $files = @{}
    foreach ($file in $manifest.files) {
        $entry = $entries[$file.path]
        if (-not $entry -or $files.ContainsKey($file.path) -or $file.sha256 -cnotmatch '^[a-f0-9]{64}$' -or $entry.Length -ne $file.bytes) {
            throw "Portable inventory mismatch: $($file.path)"
        }
        $stream = $entry.Open()
        try { $hash = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant() }
        finally { $stream.Dispose() }
        if ($hash -cne $file.sha256) { throw "Portable file digest mismatch: $($file.path)" }
        $files.Add($file.path, [ordered]@{path=$file.path; sha256=$hash; bytes=$entry.Length})
    }
    if ($entries.Count -ne $files.Count + 1 -or $files.ContainsKey('package-manifest.json')) {
        throw 'Portable inventory does not match its archive.'
    }
    $stream = $manifestEntry.Open()
    try { $manifestHash = (Get-FileHash -InputStream $stream -Algorithm SHA256).Hash.ToLowerInvariant() }
    finally { $stream.Dispose() }
    $files.Add('package-manifest.json', [ordered]@{path='package-manifest.json';sha256=$manifestHash;bytes=$manifestEntry.Length})
    return @{files=$files; entries=$entries; commit=$manifest.source_commit}
}

try {
    $target = Read-Inventory $targetArchive $Version
    if ($target.commit -cne $Commit) { throw 'Portable ZIP was built from a different commit.' }
    $deltas = @()
    if ($PreviousZip) {
        $oldArchive = [IO.Compression.ZipFile]::OpenRead((Resolve-Path -LiteralPath $PreviousZip).Path)
        try { $previous = Read-Inventory $oldArchive $PreviousVersion }
        finally { $oldArchive.Dispose() }
        $changed = @($target.files.Values | Sort-Object path | Where-Object {
            -not $previous.files.ContainsKey($_.path) -or $previous.files[$_.path].sha256 -cne $_.sha256
        })
        $name = "FormRecall-$PreviousVersion-to-$Version-windows-x64-ota.zip"
        $targetPath = Join-Path $out $name
        $deltaArchive = [IO.Compression.ZipFile]::Open($targetPath, [IO.Compression.ZipArchiveMode]::Create)
        try {
            foreach ($file in $changed) {
                $entry = $target.entries[$file.path]
                $new = $deltaArchive.CreateEntry($file.path, [IO.Compression.CompressionLevel]::Optimal)
                $new.LastWriteTime = $entry.LastWriteTime
                $source = $entry.Open()
                try {
                    $dest = $new.Open()
                    try { $source.CopyTo($dest) }
                    finally { $dest.Dispose() }
                } finally { $source.Dispose() }
            }
        } finally { $deltaArchive.Dispose() }
        # The target inventory is exhaustive. Removed old files are never
        # copied into the new directory, so no delete instructions are needed.
        $deltas = @([ordered]@{
            from = $PreviousVersion
            url = "https://github.com/Industrial-Arcane-Society/form-recall-release/releases/download/v$Version/$name"
            sha256 = (Get-FileHash -LiteralPath $targetPath -Algorithm SHA256).Hash.ToLowerInvariant()
            bytes = (Get-Item -LiteralPath $targetPath).Length
        })
        Write-Host "OTA contains $($changed.Count) changed files, $($deltas[0].bytes) bytes."
    }
    $portable = Get-Item -LiteralPath $PortableZip
    $channel = if ($Version.Contains('-alpha.')) { 'alpha' } else { 'stable' }
    $payload = [ordered]@{
        schema = 1
        platform = 'windows-x64'
        channel = $channel
        version = $Version
        commit = $Commit
        files = @($target.files.Values | Sort-Object path)
        full = [ordered]@{
            url = "https://github.com/Industrial-Arcane-Society/form-recall/releases/download/v$Version/FormRecall-$Version-windows-x64-portable.zip"
            sha256 = (Get-FileHash -LiteralPath $portable.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            bytes = $portable.Length
        }
        deltas = $deltas
    }
    $payload | ConvertTo-Json -Depth 8 -Compress | Set-Content -LiteralPath (Join-Path $out 'payload.json') -Encoding utf8NoBOM
} finally { $targetArchive.Dispose() }
