#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '0.1.0',
    [string]$PythonPath = '',
    [switch]$Bootstrap,
    [switch]$SkipTests,
    [switch]$SkipInstaller,
    [switch]$PublicRelease,
    [string]$InnoCompilerPath = '',
    [string]$SignToolPath = '',
    [string]$CertificateThumbprint = '',
    [string]$TimestampUrl = ''
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$lockFile = Join-Path $projectRoot 'requirements-build.lock.txt'
$specFile = Join-Path $projectRoot 'packaging\LearningModel.spec'
$issFile = Join-Path $projectRoot 'packaging\LearningModel.iss'
$iconFile = Join-Path $projectRoot 'study_app\ui\assets\app.ico'
$distParent = Join-Path $projectRoot 'dist'
$distDirectory = Join-Path $distParent 'LearningModel'
$workDirectory = Join-Path $projectRoot 'build\release-pyinstaller'
$testIsolationRoot = Join-Path $projectRoot 'build\release-pytest-isolation'
$artifactsDirectory = Join-Path $projectRoot 'release\artifacts'
$licenseDirectory = Join-Path $artifactsDirectory 'THIRD_PARTY_LICENSES'
$verificationDirectory = Join-Path $artifactsDirectory 'verification'
$pyInstallerWarningPath = Join-Path $workDirectory 'LearningModel\warn-LearningModel.txt'
$installerPath = Join-Path $artifactsDirectory "LearningModel-Setup-$Version-x64.exe"
$checksumPath = Join-Path $artifactsDirectory 'SHA256SUMS.txt'

function Assert-SafeProjectChild {
    param([Parameter(Mandatory)][string]$Path)
    $rootPrefix = $projectRoot.TrimEnd('\') + '\'
    $fullPath = [IO.Path]::GetFullPath($Path)
    if (-not $fullPath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing filesystem mutation outside project root: $fullPath"
    }
    return $fullPath
}

function Remove-SafeDirectory {
    param([Parameter(Mandatory)][string]$Path)
    $fullPath = Assert-SafeProjectChild $Path
    if (Test-Path -LiteralPath $fullPath) {
        if (-not (Test-Path -LiteralPath $fullPath -PathType Container)) {
            throw "Expected directory but found another item: $fullPath"
        }
        Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction Stop
    }
}

function Remove-SafeFile {
    param([Parameter(Mandatory)][string]$Path)
    $fullPath = Assert-SafeProjectChild $Path
    if (Test-Path -LiteralPath $fullPath) {
        if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
            throw "Expected file but found another item: $fullPath"
        }
        Remove-Item -LiteralPath $fullPath -Force -ErrorAction Stop
    }
}

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$NativeArgs,
        [Parameter(Mandatory)][string]$Description
    )
    Write-Host "==> $Description"
    & $FilePath @NativeArgs
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode"
    }
}

function Resolve-InnoCompiler {
    if (-not [string]::IsNullOrWhiteSpace($InnoCompilerPath)) {
        if (-not (Test-Path -LiteralPath $InnoCompilerPath -PathType Leaf)) {
            throw "Inno Setup compiler not found: $InnoCompilerPath"
        }
        return (Resolve-Path -LiteralPath $InnoCompilerPath).Path
    }
    $candidates = @(
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe')
    )
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates += Join-Path $env:ProgramFiles 'Inno Setup 7\ISCC.exe'
        $candidates += Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe'
    }
    foreach ($candidate in $candidates) {
        if (-not [string]::IsNullOrWhiteSpace($candidate) -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw 'Inno Setup 6.3 or newer compiler (ISCC.exe) was not found. Install it or pass -InnoCompilerPath.'
}

function Invoke-CodeSigning {
    param([Parameter(Mandatory)][string]$TargetPath)
    if ([string]::IsNullOrWhiteSpace($SignToolPath)) {
        return
    }
    $signArgs = @(
        'sign',
        '/fd', 'SHA256',
        '/sha1', $CertificateThumbprint,
        '/tr', $TimestampUrl,
        '/td', 'SHA256',
        $TargetPath
    )
    Invoke-NativeChecked -FilePath $SignToolPath -NativeArgs $signArgs -Description "Sign $([IO.Path]::GetFileName($TargetPath))"
}

if ($PublicRelease) {
    throw @'
PUBLIC RELEASE BLOCKED: AGPL-3.0-only has been selected for project source.
Source materials are collected and Windows VM smoke tests have run.
This workflow currently produces draft prerelease candidates only.
See docs/OPEN_SOURCE.md and docs/RELEASE_SUPPLEMENT.md.
Local QA builds may continue without -PublicRelease.
'@
}

$signingValues = @($SignToolPath, $CertificateThumbprint, $TimestampUrl)
$specifiedSigningValues = @($signingValues | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if ($specifiedSigningValues.Count -notin @(0, 3)) {
    throw 'Code signing requires -SignToolPath, -CertificateThumbprint, and -TimestampUrl together.'
}
if (-not [string]::IsNullOrWhiteSpace($SignToolPath) -and -not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) {
    throw "SignTool not found: $SignToolPath"
}

foreach ($requiredFile in @($lockFile, $specFile, $issFile, $iconFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required release input is missing: $requiredFile"
    }
}

$metadataPath = Join-Path $projectRoot 'study_app\app_metadata.py'
$metadataText = Get-Content -LiteralPath $metadataPath -Raw -Encoding utf8
$versionMatch = [Regex]::Match($metadataText, '(?m)^APP_VERSION\s*=\s*["'']([^"'']+)["'']\s*$')
if (-not $versionMatch.Success) {
    throw "Cannot read APP_VERSION from $metadataPath"
}
if ($versionMatch.Groups[1].Value -ne $Version) {
    throw "Requested version $Version does not match app metadata version $($versionMatch.Groups[1].Value)."
}
$versionResource = Get-Content -LiteralPath (Join-Path $projectRoot 'packaging\version_info.txt') -Raw -Encoding utf8
if ($versionResource -notmatch "StringStruct\('ProductVersion', '$([Regex]::Escape($Version))'\)") {
    throw 'packaging\version_info.txt is not synchronized with the requested version.'
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot '.venv-build\Scripts\python.exe'
} elseif (-not [IO.Path]::IsPathRooted($PythonPath)) {
    $PythonPath = Join-Path $projectRoot $PythonPath
}
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
$defaultVenvPython = [IO.Path]::GetFullPath((Join-Path $projectRoot '.venv-build\Scripts\python.exe'))

if ($Bootstrap -and $PythonPath -ne $defaultVenvPython) {
    throw '-Bootstrap manages only the project-local .venv-build environment. Omit -PythonPath.'
}

if ($Bootstrap -and -not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    $launcherCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    $pythonLauncher = ''
    if ($null -ne $launcherCommand) {
        $pythonLauncher = $launcherCommand.Source
    }
    $basePython = ''
    $venvArgs = @()
    if (-not [string]::IsNullOrWhiteSpace($pythonLauncher)) {
        $basePython = $pythonLauncher
        $venvArgs = @('-3.14', '-m', 'venv', (Join-Path $projectRoot '.venv-build'))
    } else {
        $localPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python314\python.exe'
        if (Test-Path -LiteralPath $localPython -PathType Leaf) {
            $basePython = $localPython
            $venvArgs = @('-m', 'venv', (Join-Path $projectRoot '.venv-build'))
        }
    }
    if ([string]::IsNullOrWhiteSpace($basePython)) {
        throw 'CPython 3.14 x64 was not found through py.exe or the standard per-user install path.'
    }
    Invoke-NativeChecked -FilePath $basePython -NativeArgs $venvArgs -Description 'Create isolated Python 3.14 x64 build environment'
}

if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Build interpreter not found: $PythonPath. Run with -Bootstrap or pass -PythonPath."
}

$probeCode = 'import json, platform, struct, sys; print(json.dumps({"version": platform.python_version(), "bits": struct.calcsize("P") * 8, "platform": sys.platform, "implementation": platform.python_implementation(), "executable": sys._base_executable}))'
$probeOutput = & $PythonPath -c $probeCode
$probeExitCode = $LASTEXITCODE
if ($probeExitCode -ne 0) {
    throw "Cannot inspect build interpreter; exit code $probeExitCode"
}
$probe = $probeOutput | ConvertFrom-Json
if ($probe.version -ne '3.14.5' -or $probe.bits -ne 64 -or $probe.platform -ne 'win32' -or $probe.implementation -ne 'CPython') {
    throw "Release build requires CPython 3.14.5 x64 on Windows; found $($probe.implementation) $($probe.version), $($probe.bits)-bit, $($probe.platform)."
}

$env:PYTHONUTF8 = '1'
$env:PYTHONDONTWRITEBYTECODE = '1'
Set-Location -LiteralPath $projectRoot

if ($Bootstrap) {
    $installArgs = @(
        '-m', 'pip', 'install',
        '--disable-pip-version-check',
        '--only-binary=:all:',
        '-r', $lockFile
    )
    Invoke-NativeChecked -FilePath $PythonPath -NativeArgs $installArgs -Description 'Install exact release dependencies'
}

$verifyPinsCode = @'
from importlib import metadata
from pathlib import Path
import re
import sys

lock = Path(sys.argv[1])
failures = []
locked_names = set()
for raw in lock.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^;\s]+)", line)
    if match is None:
        failures.append(f"not an exact pin: {line}")
        continue
    name, expected = match.groups()
    locked_names.add(re.sub(r"[-_.]+", "-", name).casefold())
    try:
        actual = metadata.version(name)
    except metadata.PackageNotFoundError:
        failures.append(f"missing: {name}=={expected}")
        continue
    if actual != expected:
        failures.append(f"version mismatch: {name} expected {expected}, found {actual}")
installed_names = {
    re.sub(r"[-_.]+", "-", str(item.metadata.get("Name") or "")).casefold()
    for item in metadata.distributions()
    if item.metadata.get("Name")
}
unexpected = sorted(installed_names - locked_names)
if unexpected:
    failures.append("unexpected distributions in build environment: " + ", ".join(unexpected))
if failures:
    raise SystemExit("\n".join(failures))
'@
$pinArgs = @('-c', $verifyPinsCode, $lockFile)
Invoke-NativeChecked -FilePath $PythonPath -NativeArgs $pinArgs -Description 'Verify exact dependency versions'

if (-not $SkipTests) {
    Remove-SafeDirectory $testIsolationRoot
    $testUserData = Join-Path $testIsolationRoot 'user-data'
    $testLegacyRoot = Join-Path $testIsolationRoot 'empty-legacy'
    New-Item -ItemType Directory -Path $testUserData -Force -ErrorAction Stop | Out-Null
    New-Item -ItemType Directory -Path $testLegacyRoot -Force -ErrorAction Stop | Out-Null
    $savedEnvironment = @{}
    foreach ($name in @('LEARNINGMODEL_DATA_ROOT', 'LEARNINGMODEL_LEGACY_ROOT', 'QT_QPA_PLATFORM')) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        $env:LEARNINGMODEL_DATA_ROOT = $testUserData
        $env:LEARNINGMODEL_LEGACY_ROOT = $testLegacyRoot
        $env:QT_QPA_PLATFORM = 'offscreen'
        $testArgs = @('-m', 'pytest', '-q')
        Invoke-NativeChecked -FilePath $PythonPath -NativeArgs $testArgs -Description 'Run release test suite'
    } finally {
        foreach ($name in $savedEnvironment.Keys) {
            $priorValue = $savedEnvironment[$name]
            if ($null -eq $priorValue) {
                Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
            } else {
                [Environment]::SetEnvironmentVariable($name, $priorValue, 'Process')
            }
        }
        Remove-SafeDirectory $testIsolationRoot
    }
}

Remove-SafeDirectory $workDirectory
Remove-SafeDirectory $distDirectory
Remove-SafeDirectory $licenseDirectory
Remove-SafeDirectory $verificationDirectory
Remove-SafeFile $installerPath
Remove-SafeFile $checksumPath
New-Item -ItemType Directory -Path $distParent -Force -ErrorAction Stop | Out-Null
New-Item -ItemType Directory -Path $artifactsDirectory -Force -ErrorAction Stop | Out-Null

$pyInstallerArgs = @(
    '-m', 'PyInstaller',
    '--clean',
    '--noconfirm',
    '--distpath', $distParent,
    '--workpath', $workDirectory,
    $specFile
)
# PyInstaller's Windows dependency scanner consults PATH.  Build hosts such as
# Codex can prepend unrelated native toolchains (for example Poppler), whose
# generic DLL names may otherwise shadow the Windows/Qt dependencies and make
# the frozen application fail at runtime.  Analyze with a deliberately narrow,
# reproducible PATH and restore the caller's environment afterwards.
$savedBuildPath = $env:PATH
$pythonDirectory = [IO.Path]::GetDirectoryName($PythonPath)
$basePythonDirectory = [IO.Path]::GetDirectoryName([string]$probe.executable)
$buildPathEntries = @(
    $pythonDirectory,
    $basePythonDirectory,
    (Join-Path $env:SystemRoot 'System32'),
    $env:SystemRoot
) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
try {
    $env:PATH = $buildPathEntries -join [IO.Path]::PathSeparator
    Invoke-NativeChecked -FilePath $PythonPath -NativeArgs $pyInstallerArgs -Description 'Build PyInstaller onedir application'
} finally {
    $env:PATH = $savedBuildPath
}

$applicationExe = Join-Path $distDirectory 'LearningModel.exe'
if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
    throw "PyInstaller did not produce $applicationExe"
}
if (-not (Test-Path -LiteralPath $pyInstallerWarningPath -PathType Leaf)) {
    throw "PyInstaller did not produce its missing-import report: $pyInstallerWarningPath"
}
$warningText = Get-Content -LiteralPath $pyInstallerWarningPath -Raw -Encoding utf8
$criticalMissingImports = @(
    $warningText -split "`r?`n" |
        Where-Object { $_ -match '^missing module named (study_app(?:\.|\s)|learning_[A-Za-z0-9_]+)' }
)
if ($criticalMissingImports.Count -gt 0) {
    throw "PyInstaller omitted application modules:`n$($criticalMissingImports -join "`n")"
}
New-Item -ItemType Directory -Path $verificationDirectory -Force -ErrorAction Stop | Out-Null
Copy-Item -LiteralPath $pyInstallerWarningPath -Destination (Join-Path $verificationDirectory 'pyinstaller-warnings.txt') -Force
Invoke-CodeSigning -TargetPath $applicationExe

$pwshPath = Join-Path $PSHOME 'pwsh.exe'
$verifyScript = Join-Path $PSScriptRoot 'verify_release.ps1'
$verifyArgs = @(
    '-NoLogo', '-NoProfile', '-NonInteractive',
    '-File', $verifyScript,
    '-DistDirectory', $distDirectory,
    '-ExpectedVersion', $Version,
    '-RunSelfTest'
)
Invoke-NativeChecked -FilePath $pwshPath -NativeArgs $verifyArgs -Description 'Verify onedir release and packaged self-test'

$licenseCollector = Join-Path $PSScriptRoot 'collect_licenses.py'
$licenseArgs = @(
    $licenseCollector,
    '--lock-file', $lockFile,
    '--output', $licenseDirectory,
    '--include-build-tools'
)
Invoke-NativeChecked -FilePath $PythonPath -NativeArgs $licenseArgs -Description 'Collect third-party license evidence'

if (-not $SkipInstaller) {
    $innoCompiler = Resolve-InnoCompiler
    $innoVersionOutput = & $innoCompiler '--version'
    $innoVersionExitCode = $LASTEXITCODE
    if ($innoVersionExitCode -ne 0) {
        throw "Cannot inspect Inno Setup compiler; exit code $innoVersionExitCode"
    }
    $innoVersion = ([string]($innoVersionOutput | Select-Object -First 1)).Trim()
    if ($innoVersion -ne '7.1.0') {
        throw "Release build requires Inno Setup 7.1.0; found '$innoVersion'."
    }
    $innoArgs = @(
        "/DAppVersion=$Version",
        "/O$artifactsDirectory",
        "/FLearningModel-Setup-$Version-x64",
        $issFile
    )
    Invoke-NativeChecked -FilePath $innoCompiler -NativeArgs $innoArgs -Description 'Build Inno Setup installer'
    if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
        throw "Inno Setup did not produce $installerPath"
    }
    Invoke-CodeSigning -TargetPath $installerPath

    $verifyInstallerArgs = @(
        '-NoLogo', '-NoProfile', '-NonInteractive',
        '-File', $verifyScript,
        '-DistDirectory', $distDirectory,
        '-InstallerPath', $installerPath,
        '-ExpectedVersion', $Version
    )
    Invoke-NativeChecked -FilePath $pwshPath -NativeArgs $verifyInstallerArgs -Description 'Verify final installer inputs and identity'

    $hash = Get-FileHash -LiteralPath $installerPath -Algorithm SHA256
    $checksumLine = "$($hash.Hash.ToLowerInvariant())  $([IO.Path]::GetFileName($installerPath))`n"
    [IO.File]::WriteAllText($checksumPath, $checksumLine, [Text.UTF8Encoding]::new($false))
}

foreach ($documentName in @('安装与升级说明.md', '更新记录.md', '第三方许可说明.md')) {
    $sourceDocument = Join-Path (Join-Path $projectRoot 'release') $documentName
    if (-not (Test-Path -LiteralPath $sourceDocument -PathType Leaf)) {
        throw "Required release document is missing: $sourceDocument"
    }
    Copy-Item -LiteralPath $sourceDocument -Destination (Join-Path $artifactsDirectory $documentName) -Force -ErrorAction Stop
}

New-Item -ItemType Directory -Path $verificationDirectory -Force -ErrorAction Stop | Out-Null
$freezeArgs = @('-m', 'pip', 'freeze', '--all')
$freezeOutput = & $PythonPath @freezeArgs
$freezeExitCode = $LASTEXITCODE
if ($freezeExitCode -ne 0) {
    throw "Cannot record the complete installed package inventory; exit code $freezeExitCode"
}
[IO.File]::WriteAllText(
    (Join-Path $verificationDirectory 'installed-packages.txt'),
    (($freezeOutput -join "`n").TrimEnd() + "`n"),
    [Text.UTF8Encoding]::new($false)
)
$buildRecord = [ordered]@{
    application = 'LearningModel'
    version = $Version
    python = [string]$probe.version
    architecture = 'x64'
    built_at = (Get-Date).ToUniversalTime().ToString('o')
    tests_skipped = [bool]$SkipTests
    installer_skipped = [bool]$SkipInstaller
    signed = -not [string]::IsNullOrWhiteSpace($SignToolPath)
    public_release = $false
    public_release_blocker = 'Draft prerelease; distribute matching source/notice attachments and review docs/RELEASE_ACCEPTANCE.md'
}
$recordJson = $buildRecord | ConvertTo-Json -Depth 3
[IO.File]::WriteAllText(
    (Join-Path $verificationDirectory 'build-record.json'),
    $recordJson + "`n",
    [Text.UTF8Encoding]::new($false)
)

Write-Host ''
Write-Host "Internal/QA release build completed: $distDirectory"
if (-not $SkipInstaller) {
    Write-Host "Installer: $installerPath"
    Write-Host "SHA-256: $checksumPath"
}
Write-Warning 'Binary release remains a draft: complete the source-material and validation checklist in docs/OPEN_SOURCE.md.'
