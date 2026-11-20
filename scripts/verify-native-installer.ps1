# FormRecall 安装验收（runtime-v2 容器布局）
# 布局契约：temp/2026-11-21/container-layout-v2-spec.md
#
# 参数语义与新布局的关系（其余参数名与含义保持不变，windows-release.yml 可直接沿用）：
#   -PackageDirectory 仍是便携包根目录，但安装清单改为 runtime/base/package-manifest.json；
#                     该清单描述 runtime/base 内的扁平 payload，安装根只保留 FormRecall.exe 与 runtime/。
#   -ExpectedUpdateVersion / -ExpectedUpdateCommit 名称与含义不变；应用内更新验收针对已安装的
#   runtime-v2 容器（runtime/versions、激活后删除 runtime/base），由 verify-desktop-ota.ps1 负责。
param(
    [Parameter(Mandatory)][string]$InstallerPath,
    [Parameter(Mandatory)][string]$PackageDirectory,
    [Parameter(Mandatory)][string]$Version,
    [string]$OutputDirectory,
    [switch]$SkipStartup,
    [switch]$UseSoftwareOpenGL,
    [string]$ExpectedUpdateVersion,
    [string]$ExpectedUpdateCommit
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $IsWindows -or -not [Environment]::Is64BitProcess) {
    throw 'Installer verification requires Windows and 64-bit PowerShell 7.'
}
if ($SkipStartup -and $UseSoftwareOpenGL) {
    throw 'UseSoftwareOpenGL requires the startup verification to run.'
}
if ([bool]$ExpectedUpdateVersion -ne [bool]$ExpectedUpdateCommit -or ($ExpectedUpdateVersion -and $SkipStartup)) {
    throw 'OTA verification requires its exact target version and commit, plus a startup check.'
}
if (-not ('FormRecall.InstallerShellLink' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace FormRecall {
    public static class InstallerShellLink {
        [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
        private class ShellLink {}

        [ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        private interface IShellLinkW {
            void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int length, IntPtr data, uint flags);
            void GetIDList(out IntPtr idList);
            void SetIDList(IntPtr idList);
            void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder description, int length);
            void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string description);
            void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder directory, int length);
            void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string directory);
            void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder arguments, int length);
        }

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true, ExactSpelling = true)]
        private static extern uint GetLongPathNameW(string shortPath, StringBuilder longPath, uint length);

        public static string[] Read(string path) {
            object instance = new ShellLink();
            try {
                ((IPersistFile)instance).Load(path, 0);
                IShellLinkW link = (IShellLinkW)instance;
                StringBuilder target = new StringBuilder(32768);
                StringBuilder directory = new StringBuilder(32768);
                StringBuilder arguments = new StringBuilder(32768);
                link.GetPath(target, target.Capacity, IntPtr.Zero, 4); // SLGP_RAWPATH
                link.GetWorkingDirectory(directory, directory.Capacity);
                link.GetArguments(arguments, arguments.Capacity);
                return new[] { target.ToString(), directory.ToString(), arguments.ToString() };
            } finally { Marshal.FinalReleaseComObject(instance); }
        }

        public static string LongPath(string path) {
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path))
                throw new InvalidDataException("Shortcut paths must be absolute and nonempty.");
            StringBuilder result = new StringBuilder(32768);
            uint length = GetLongPathNameW(Path.GetFullPath(path), result, (uint)result.Capacity);
            if (length == 0) throw new Win32Exception(Marshal.GetLastWin32Error());
            if (length >= result.Capacity) throw new InvalidDataException("Shortcut path exceeds its buffer.");
            return result.ToString().TrimEnd('\\', '/');
        }
    }
}
'@
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$package = (Resolve-Path -LiteralPath $PackageDirectory).Path
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'InstallerPath must identify a file.' }
if (-not (Test-Path -LiteralPath $package -PathType Container)) { throw 'PackageDirectory must identify a directory.' }
$baseSourceDirectory = Join-Path $package 'runtime/base'
if (-not (Test-Path -LiteralPath $baseSourceDirectory -PathType Container)) {
    throw 'The package must provide runtime/base with its package-manifest.json (runtime-v2 container layout).'
}
$manifestPath = Join-Path $baseSourceDirectory 'package-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable
if ($manifest['schema'] -ne 1 -or $manifest['version'] -cne $Version -or
    $manifest['source_commit'] -notmatch '^[0-9a-f]{40}$' -or
    $Version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?(\+[0-9A-Za-z-]+(\.[0-9A-Za-z-]+)*)?$') {
    throw 'The runtime/base package manifest must identify the requested semantic version and a full source commit.'
}
& "$PSScriptRoot/verify-native-package.ps1" -PackageDirectory $package

$instance = [Guid]::NewGuid().ToString('N')
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $PSScriptRoot "../temp/outputs/installer-verification-$instance"
}
$output = [IO.Path]::GetFullPath($OutputDirectory)
if (Test-Path -LiteralPath (Join-Path $output 'verification.json')) {
    throw 'OutputDirectory already contains installer verification evidence. Choose a new directory.'
}
New-Item -ItemType Directory -Path $output -Force | Out-Null
$temporaryRoot = (Resolve-Path -LiteralPath ([IO.Path]::GetTempPath())).Path.TrimEnd('\', '/')
$scratch = Join-Path $temporaryRoot "FormRecall 安装验收 $instance"
$installDirectory = Join-Path $scratch '程序 安装'
$baseDirectory = Join-Path $installDirectory 'runtime/base'
$dataDirectory = Join-Path $scratch '用户 资料/com.aimodeler.desktop'
$programs = [Environment]::GetFolderPath([Environment+SpecialFolder]::Programs)
$shortcutPath = Join-Path $programs 'FormRecall.lnk'
$desktopShortcut = Join-Path ([Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)) 'FormRecall.lnk'
$appKey = '{A7B8F0C2-7D53-4E39-964A-16281F85C922}_is1'
$uninstallSubkey = 'Software\Microsoft\Windows\CurrentVersion\Uninstall'
$uninstaller = $null
$driver = $null
$failure = $null
$installationAttempted = $false
$uninstalled = $false
$mutex = [Threading.Mutex]::new($false, ('Local\FormRecall.InstallerVerification.' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value))
$ownsMutex = $false
$proof = [ordered]@{
    schema = 1
    layout = 'runtime-v2'
    verified_at = [DateTime]::UtcNow.ToString('o')
    version = $Version
    installer = $installer
    installer_sha256 = (Get-FileHash -LiteralPath $installer).Hash.ToLowerInvariant()
    package = $package
    base_package_manifest = $manifestPath
    base_package_manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath).Hash.ToLowerInvariant()
    install_directory = $installDirectory
    base_directory = $baseDirectory
    shortcut_path = $shortcutPath
    data_directory = $dataDirectory
    isolation = 'Unique per-user install directory using the production installer identity. Existing FormRecall registrations or shortcuts cause verification to stop before installation.'
    startup_requested = -not $SkipStartup
    passed = $false
}

function Get-FormRecallRegistrations {
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry64, [Microsoft.Win32.RegistryView]::Registry32)) {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
            try {
                $root = $base.OpenSubKey($uninstallSubkey)
                if (-not $root) { continue }
                try {
                    foreach ($name in $root.GetSubKeyNames()) {
                        $key = $root.OpenSubKey($name)
                        if (-not $key) { continue }
                        try {
                            $displayName = [string]$key.GetValue('DisplayName', '')
                            $location = [string]$key.GetValue('InstallLocation', '')
                            $icon = [string]$key.GetValue('DisplayIcon', '')
                            if ($name -ine $appKey -and $displayName -notmatch '^FormRecall(?:\s|$)' -and $name -notmatch '^FormRecall' -and $icon -notmatch 'FormRecall\.exe') {
                                continue
                            }
                            [pscustomobject]@{
                                hive = $hive.ToString(); view = $view.ToString(); key = $name
                                display_name = $displayName; display_version = [string]$key.GetValue('DisplayVersion', '')
                                location = $location; uninstall = [string]$key.GetValue('UninstallString', '')
                                quiet_uninstall = [string]$key.GetValue('QuietUninstallString', '')
                            }
                        } finally { $key.Dispose() }
                    }
                } finally { $root.Dispose() }
            } finally { $base.Dispose() }
        }
    }
}

function Invoke-OwnedInstallerProcess {
    param([string]$Executable, [string[]]$Arguments, [string]$Name, [int]$TimeoutSeconds = 300)
    $info = [Diagnostics.ProcessStartInfo]::new($Executable)
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $scratch
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $info.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    foreach ($environmentName in @($info.Environment.Keys)) {
        if ($environmentName -match '^(AIMODELER_|FORMRECALL_|MYGO_|NATIVE_VIEWPORT_|OCCT_|CSF_|PYTHON)' -or $environmentName -in @('CASROOT', 'NODE_OPTIONS')) {
            $null = $info.Environment.Remove($environmentName)
        }
    }
    $process = [Diagnostics.Process]::Start($info)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) { throw "$Name did not finish within $TimeoutSeconds seconds." }
        if (-not $stdout.Wait(5000) -or -not $stderr.Wait(5000)) { throw "$Name left an output pipe open." }
        [IO.File]::WriteAllText((Join-Path $output "$Name.stdout.log"), $stdout.Result)
        [IO.File]::WriteAllText((Join-Path $output "$Name.stderr.log"), $stderr.Result)
        $proof["${Name}_process"] = @{ pid = $process.Id; exit_code = $process.ExitCode }
        if ($process.ExitCode -ne 0) { throw "$Name exited with code $($process.ExitCode)." }
        return $stdout.Result
    } finally {
        # This is the exact process handle created above, never a name/PID search.
        if (-not $process.HasExited) { $process.Kill($true); $null = $process.WaitForExit(10000) }
        $process.Dispose()
    }
}

function Get-OwnedRegistration {
    $registrations = @(Get-FormRecallRegistrations)
    foreach ($registration in $registrations) {
        if ($registration.hive -ne 'CurrentUser' -or $registration.key -ine $appKey -or
            $registration.display_name -notmatch '^FormRecall(?:\s|$)' -or
            $registration.display_version -cne $Version -or
            [IO.Path]::GetFullPath($registration.location.TrimEnd('\', '/')) -ine $installDirectory) {
            throw 'A FormRecall registration does not belong to this isolated installation. It has been preserved.'
        }
    }
    # HKCU can be shared across registry views. Collapse only identical keys.
    $distinct = @($registrations | Group-Object hive, key | ForEach-Object { $_.Group[0] })
    if ($distinct.Count -ne 1 -or $distinct[0].hive -ne 'CurrentUser' -or $distinct[0].key -ine $appKey -or
        $distinct[0].display_name -notmatch '^FormRecall(?:\s|$)' -or
        $distinct[0].display_version -cne $Version -or
        [IO.Path]::GetFullPath($distinct[0].location.TrimEnd('\', '/')) -ine $installDirectory) {
        throw 'Expected exactly one per-user registration for this isolated installation.'
    }
    $registration = $distinct[0]
    if ($registration.uninstall -notmatch '^"([^"]+)"(?:\s|$)') { throw 'The installer registered an unexpected uninstall command.' }
    $registeredExecutable = [IO.Path]::GetFullPath($Matches[1])
    if ([IO.Path]::GetDirectoryName($registeredExecutable) -ine $installDirectory -or
        [IO.Path]::GetFileName($registeredExecutable) -notmatch '^unins[0-9]{3}\.exe$' -or
        -not (Test-Path -LiteralPath $registeredExecutable -PathType Leaf)) {
        throw 'The registered uninstaller is outside the isolated installation.'
    }
    return @{ registration = $registration; executable = $registeredExecutable }
}

function Remove-OwnedOpenGL {
    if (-not $driver) { return }
    # The software OpenGL runtime is deployed next to runtime/base/FormRecall.exe,
    # which is the executable that actually initializes the viewport.
    if ([IO.Path]::GetFullPath([string]$driver.destination) -ine [IO.Path]::GetFullPath($baseDirectory)) {
        throw 'The verification-only OpenGL runtime was deployed outside runtime/base. It has been preserved.'
    }
    foreach ($file in $driver.files) {
        if ($file.name -notin @('opengl32.dll', 'libgallium_wgl.dll')) { throw 'Unexpected verification-only OpenGL filename.' }
        $path = Join-Path $baseDirectory $file.name
        if ((Get-FileHash -LiteralPath $path).Hash -ine $file.sha256) {
            throw 'A verification-only OpenGL file changed. It has been preserved.'
        }
    }
    foreach ($file in $driver.files) { Remove-Item -LiteralPath (Join-Path $baseDirectory $file.name) }
    $proof['software_opengl_removed'] = $true
}

try {
    try { $ownsMutex = $mutex.WaitOne(0) }
    catch [Threading.AbandonedMutexException] { $ownsMutex = $true }
    if (-not $ownsMutex) { throw 'Another installer verification is already running for this Windows user.' }
    if (@(Get-FormRecallRegistrations).Count -gt 0) {
        throw 'An existing FormRecall installation is registered. Run installer verification on a clean Windows runner or separate Windows user.'
    }
    if ((Test-Path -LiteralPath $scratch) -or (Test-Path -LiteralPath $shortcutPath) -or (Test-Path -LiteralPath $desktopShortcut)) {
        throw 'An isolated verification path or an existing FormRecall shortcut already exists. It has been preserved.'
    }
    New-Item -ItemType Directory -Path $scratch, $dataDirectory | Out-Null
    $sentinel = Join-Path $dataDirectory '保留的用户资料.json'
    [IO.File]::WriteAllText($sentinel, (@{ fixture = $instance; purpose = 'uninstall data retention' } | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    $sentinelHash = (Get-FileHash -LiteralPath $sentinel).Hash
    $installationAttempted = $true
    $arguments = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', '/CURRENTUSER',
        '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS', "/DIR=$installDirectory",
        '/TASKS=', "/LOG=$(Join-Path $output 'install.log')")
    $null = Invoke-OwnedInstallerProcess $installer $arguments 'install'
    $owned = Get-OwnedRegistration
    $uninstaller = $owned.executable
    $proof['registration'] = $owned.registration
    if (-not (Test-Path -LiteralPath $installDirectory -PathType Container)) {
        throw 'The installer did not create the isolated installation directory.'
    }

    # 1) The runtime-v2 installation root contains only FormRecall.exe and runtime/.
    #    Inno Setup's own uninstaller files are the only tolerated extras.
    $allowedRootNames = @('FormRecall.exe', 'runtime')
    $rootEntries = @(Get-ChildItem -LiteralPath $installDirectory -Force)
    $proof['root_entries'] = @($rootEntries | ForEach-Object Name | Sort-Object)
    $unexpectedRoot = @($rootEntries | Where-Object {
            $_.Name -notin $allowedRootNames -and $_.Name -notmatch '^unins[0-9]{3}\.(exe|dat|msg)$'
        })
    if ($unexpectedRoot.Count -gt 0) {
        $proof['unexpected_root_entries'] = @($unexpectedRoot | ForEach-Object Name)
        throw "The runtime-v2 installation root contains unexpected entries: $(@($unexpectedRoot | ForEach-Object Name) -join ', ')"
    }
    $rootExecutable = Join-Path $installDirectory 'FormRecall.exe'
    if (-not (Test-Path -LiteralPath $rootExecutable -PathType Leaf)) {
        throw 'The installer did not place the launcher at the installation root.'
    }
    $runtimeDirectory = Join-Path $installDirectory 'runtime'
    if (-not (Test-Path -LiteralPath $runtimeDirectory -PathType Container)) {
        throw 'The installer did not create runtime/ in the installation root.'
    }

    # 2) No flat-layout residue may survive at the installation root. The
    #    payload's own app/occt-runtime/python/catia-bootstrap/licenses live
    #    under runtime/base and are verified there.
    $legacyRootNames = @(
        '使用说明.txt', '用户说明.txt', 'Start-FormRecall.cmd', 'update-bootstrap-v2', 'update-bootstrap-v3',
        '.formrecall-updates', 'app', 'occt-runtime', 'python', 'catia-bootstrap', 'licenses',
        'formrecall-engine-host.exe', 'package-manifest.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md'
    )
    $proof['removed_legacy_paths_checked'] = $legacyRootNames
    $remainingLegacy = @($legacyRootNames | Where-Object { Test-Path -LiteralPath (Join-Path $installDirectory $_) })
    if ($remainingLegacy.Count -gt 0) {
        $proof['legacy_paths_present'] = $remainingLegacy
        throw "The installation root still contains flat-layout paths: $($remainingLegacy -join ', ')"
    }

    # runtime/ carries only base/; versions/ appears after the first successful update only.
    $runtimeEntries = @(Get-ChildItem -LiteralPath $runtimeDirectory -Force)
    $proof['runtime_entries'] = @($runtimeEntries | ForEach-Object Name | Sort-Object)
    $unexpectedRuntime = @($runtimeEntries | Where-Object { $_.Name -notin @('base') })
    if ($unexpectedRuntime.Count -gt 0) {
        throw "runtime/ contains unexpected entries: $(@($unexpectedRuntime | ForEach-Object Name) -join ', ')"
    }
    if (-not (Test-Path -LiteralPath $baseDirectory -PathType Container)) {
        throw 'The installer did not provide runtime/base.'
    }

    # 3) runtime/base must be the complete flat payload.
    $baseRequired = @(
        @('FormRecall.exe'), @('formrecall-engine-host.exe'), @('app'),
        @('occt-runtime', 'runtime-manifest.json'), @('package-manifest.json'), @('LICENSE')
    )
    foreach ($parts in $baseRequired) {
        $candidate = $baseDirectory
        foreach ($part in $parts) { $candidate = Join-Path $candidate $part }
        if (-not (Test-Path -LiteralPath $candidate)) {
            throw "runtime/base is missing a required layout entry: $(@($parts) -join '/')"
        }
    }
    if (-not (Test-Path -LiteralPath (Join-Path $baseDirectory 'app') -PathType Container)) {
        throw 'runtime/base/app must be a directory.'
    }

    # 4) Every file recorded by runtime/base/package-manifest.json is checked
    #    against its recorded size and SHA-256.
    $baseEntries = @(@($manifest['files']) | Where-Object { $null -ne $_ })
    if ($baseEntries.Count -eq 0) { throw 'The runtime/base package manifest must list its payload files.' }
    $expectedBaseFiles = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $baseEntries) {
        $entryPath = [string]$entry['path']
        if (-not $entryPath -or [IO.Path]::IsPathRooted($entryPath) -or $entry['sha256'] -notmatch '^[0-9a-fA-F]{64}$') {
            throw 'The runtime/base package manifest contains an invalid file entry.'
        }
        $installedPath = [IO.Path]::GetFullPath((Join-Path $baseDirectory $entryPath))
        $relativePath = [IO.Path]::GetRelativePath($baseDirectory, $installedPath)
        if ([IO.Path]::IsPathRooted($relativePath) -or $relativePath -match '^\.\.([\\/]|$)' -or
            -not $expectedBaseFiles.Add($relativePath.Replace('\', '/'))) {
            throw 'Invalid or repeated runtime/base package file path.'
        }
        $file = Get-Item -LiteralPath $installedPath -Force
        if ($file.PSIsContainer -or $file.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) -or
            $file.Length -ne $entry['bytes'] -or (Get-FileHash -LiteralPath $installedPath).Hash -ine $entry['sha256']) {
            throw "Installed base file does not match the verified package: $entryPath"
        }
    }
    $manifestFileName = 'package-manifest.json'
    if (-not $expectedBaseFiles.Contains($manifestFileName)) { $null = $expectedBaseFiles.Add($manifestFileName) }
    if ((Get-FileHash -LiteralPath (Join-Path $baseDirectory $manifestFileName)).Hash -ine $proof['base_package_manifest_sha256']) {
        throw 'The installed runtime/base package manifest differs from the verified package.'
    }
    $baseFiles = @(Get-ChildItem -LiteralPath $baseDirectory -Recurse -File -Force)
    $proof['base_file_count'] = $baseFiles.Count
    if (@(Get-ChildItem -LiteralPath $baseDirectory -Recurse -Force |
                Where-Object { $_.Attributes.HasFlag([IO.FileAttributes]::ReparsePoint) }).Count -gt 0) {
        throw 'runtime/base contains links, which the runtime-v2 layout does not allow.'
    }
    foreach ($file in $baseFiles) {
        $relative = [IO.Path]::GetRelativePath($baseDirectory, $file.FullName).Replace('\', '/')
        if (-not $expectedBaseFiles.Contains($relative)) {
            throw "Unexpected installed base file: $relative"
        }
    }
    if ($baseFiles.Count -ne $expectedBaseFiles.Count) {
        throw "runtime/base contains $($baseFiles.Count) files but its package manifest lists $($expectedBaseFiles.Count)."
    }
    $proof['verified_package_files'] = $expectedBaseFiles.Count

    # 5) The root executable is the launcher and is byte-identical to the base payload.
    $baseExecutable = Join-Path $baseDirectory 'FormRecall.exe'
    $rootExecutableSha = (Get-FileHash -LiteralPath $rootExecutable).Hash.ToLowerInvariant()
    $baseExecutableSha = (Get-FileHash -LiteralPath $baseExecutable).Hash.ToLowerInvariant()
    if ($rootExecutableSha -cne $baseExecutableSha) {
        throw 'The root launcher is not byte-identical to runtime/base/FormRecall.exe.'
    }
    $proof['launcher_sha256'] = $rootExecutableSha
    $proof['base_executable_sha256'] = $baseExecutableSha

    # 6) Version identity comes from the installed root launcher, which forwards
    #    the arguments to runtime/base.
    $identity = (Invoke-OwnedInstallerProcess $rootExecutable @('--version-json') 'version' 15) | ConvertFrom-Json -AsHashtable
    if ($identity['version'] -cne $Version) {
        throw 'The installed launcher did not report the requested version through runtime/base.'
    }
    if ($identity['commit'] -cne $manifest['source_commit']) {
        throw 'The installed runtime/base build does not match the package manifest source commit.'
    }
    $proof['identity'] = $identity

    # 7) Registration and 8) Start Menu shortcut keep pointing at the installation root.
    if (-not (Test-Path -LiteralPath $shortcutPath -PathType Leaf)) { throw 'The installer did not create its per-user Start Menu shortcut.' }
    if (Test-Path -LiteralPath $desktopShortcut) { throw 'The installer created an unrequested desktop shortcut.' }
    # Read the shortcut through the Unicode shell interface so the check works
    # even when the installer path contains characters outside the system code page.
    $nativeLink = [FormRecall.InstallerShellLink]::Read($shortcutPath)
    $shortcutProof = [ordered]@{
        path = $shortcutPath
        expected_target = $rootExecutable
        expected_working_directory = $installDirectory
        target = $nativeLink[0]
        working_directory = $nativeLink[1]
        arguments = $nativeLink[2]
    }
    $proof['shortcuts'] = @($shortcutProof)
    $shortcutProof | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $output 'shortcut.json') -Encoding utf8NoBOM
    $target = [FormRecall.InstallerShellLink]::LongPath($nativeLink[0])
    $workingDirectory = [FormRecall.InstallerShellLink]::LongPath($nativeLink[1])
    if ($target -ine [FormRecall.InstallerShellLink]::LongPath($shortcutProof['expected_target']) -or
        $workingDirectory -ine [FormRecall.InstallerShellLink]::LongPath($installDirectory) -or $nativeLink[2]) {
        throw 'The installed Start Menu shortcut does not launch the installed launcher. Inspect shortcut.json.'
    }
    if (-not $SkipStartup) {
        if ($UseSoftwareOpenGL) {
            $driver = & "$PSScriptRoot/install-ci-opengl.ps1" -DestinationDirectory $baseDirectory
            $proof['software_opengl'] = $driver
        }
        $previousDriver = $env:GALLIUM_DRIVER
        try {
            if ($UseSoftwareOpenGL) { $env:GALLIUM_DRIVER = 'llvmpipe' }
            # 9) Desktop startup is driven by the installation root launcher; it
            #    forwards to runtime/base, which owns app/ and occt-runtime/.
            & "$PSScriptRoot/verify-native-release-startup.ps1" -PackageDirectory $installDirectory -Version $Version -OutputDirectory (Join-Path $output 'startup')
            $startup = Get-Content -LiteralPath (Join-Path $output 'startup/verification.json') -Raw | ConvertFrom-Json -AsHashtable
            if ($startup['passed'] -ne $true) { throw 'Installed program startup verification did not pass.' }
            $proof['startup'] = @{ passed = $true; launch_executable = $rootExecutable; model = $startup['model']; frontend_pid = $startup['frontend_pid'] }
        } finally { $env:GALLIUM_DRIVER = $previousDriver }
    }
    Remove-OwnedOpenGL
    $driver = $null
    if ($ExpectedUpdateVersion) {
        # The update state lives under runtime/versions; verify-desktop-ota.ps1
        # drives the installed launcher and its bundled version in place.
        $otaOutput = Join-Path $output 'ota'
        & "$PSScriptRoot/verify-desktop-ota.ps1" -BundleDirectory $installDirectory -ExpectedVersion $ExpectedUpdateVersion `
            -ExpectedCommit $ExpectedUpdateCommit -OutputDirectory $otaOutput -UseSoftwareOpenGL:$UseSoftwareOpenGL
        $proof['ota'] = Get-Content -LiteralPath (Join-Path $otaOutput 'ota-verification.json') -Raw | ConvertFrom-Json -AsHashtable
    }
    $owned = Get-OwnedRegistration
    if ($owned.executable -ine $uninstaller) { throw 'The isolated uninstaller changed during verification.' }
    $null = Invoke-OwnedInstallerProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $output 'uninstall.log')") 'uninstall'
    $uninstalled = $true
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Test-Path -LiteralPath $uninstaller) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (@(Get-FormRecallRegistrations).Count -gt 0 -or (Test-Path -LiteralPath $shortcutPath) -or (Test-Path -LiteralPath $desktopShortcut)) {
        throw 'Uninstallation left the isolated registration or shortcuts behind.'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    while ((Test-Path -LiteralPath $installDirectory) -and [DateTime]::UtcNow -lt $deadline) { Start-Sleep -Milliseconds 100 }
    if (Test-Path -LiteralPath $installDirectory) {
        $remaining = @(Get-ChildItem -LiteralPath $installDirectory -Recurse -Force)
        if ($remaining.Count -gt 0) {
            $proof['remaining_program_files'] = @($remaining | ForEach-Object { [IO.Path]::GetRelativePath($installDirectory, $_.FullName) })
            throw 'Uninstallation left installed program files behind.'
        }
        throw 'Uninstallation left an empty installation directory behind.'
    }
    $proof['install_directory_removed'] = $true
    if (-not (Test-Path -LiteralPath $sentinel) -or (Get-FileHash -LiteralPath $sentinel).Hash -cne $sentinelHash) {
        throw 'Uninstallation removed or modified the isolated user data fixture.'
    }
    Copy-Item -LiteralPath $sentinel -Destination (Join-Path $output 'retained-user-data.json')
    $proof['user_data_preserved'] = $true
    $proof['user_data_sha256'] = $sentinelHash.ToLowerInvariant()
    $proof['program_files_removed'] = $true
    $proof['registration_removed'] = $true
    $proof['shortcuts_removed'] = $true
    $proof['passed'] = $true
} catch {
    $failure = $_
    $proof['error'] = $_.Exception.Message
} finally {
    if ($installationAttempted -and -not $uninstalled) {
        try {
            Remove-OwnedOpenGL
            $owned = Get-OwnedRegistration
            $null = Invoke-OwnedInstallerProcess $owned.executable @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$(Join-Path $output 'cleanup-uninstall.log')") 'cleanup-uninstall'
            $uninstalled = $true
        } catch { $proof['cleanup_error'] = $_.Exception.Message }
    }
    $proof['uninstall_completed'] = $uninstalled
    if ($uninstalled -and (Test-Path -LiteralPath $scratch)) {
        $resolvedScratch = (Resolve-Path -LiteralPath $scratch).Path
        if ([IO.Path]::GetFullPath($resolvedScratch) -ine $scratch -or
            [IO.Path]::GetDirectoryName($scratch) -ine $temporaryRoot -or
            (Get-Item -LiteralPath $scratch).Attributes.HasFlag([IO.FileAttributes]::ReparsePoint)) {
            throw 'Refusing to remove an installer verification directory outside its validated temporary path.'
        }
        Remove-Item -LiteralPath $resolvedScratch -Recurse -Force
        $proof['scratch_removed'] = $true
    }
    if ($ownsMutex) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
    $proof | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $output 'verification.json') -Encoding utf8NoBOM
}
if ($failure) { throw $failure }
Write-Host "Verified FormRecall $Version runtime-v2 installation (root launcher + runtime/base), base payload checksums, shortcuts, registration and uninstallation. Evidence: $output"
