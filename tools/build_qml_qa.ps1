#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$PythonPath = '',
    [string]$RunName = '',
    [switch]$RequireReleaseRuntime,
    [switch]$PreflightOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot 'output'))
$specFile = Join-Path $projectRoot 'packaging\LearningModel.spec'
$installerSpecFile = Join-Path $projectRoot 'packaging\LearningModel.iss'
$versionResourceFile = Join-Path $projectRoot 'packaging\version_info.txt'
$lockFile = Join-Path $projectRoot 'requirements-build.lock.txt'
$metadataFile = Join-Path $projectRoot 'study_app\app_metadata.py'
$verifyScript = Join-Path $PSScriptRoot 'verify_release.ps1'
$buildScriptFile = [IO.Path]::GetFullPath($PSCommandPath)

function Get-Sha256 {
    param([Parameter(Mandatory)][string]$Path)
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Write-JsonFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][object]$Value
    )
    $json = $Value | ConvertTo-Json -Depth 12
    [IO.File]::WriteAllText($Path, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Add-SourceManifestFile {
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][System.Collections.IDictionary]$Manifest
    )
    $fullPath = [IO.Path]::GetFullPath($Path)
    $projectPrefix = $projectRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $fullPath.StartsWith($projectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Source manifest path escapes project root: $fullPath"
    }
    if (-not (Test-Path -LiteralPath $fullPath -PathType Leaf)) {
        throw "Source manifest input is missing: $fullPath"
    }
    if (((Get-Item -LiteralPath $fullPath).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing linked source manifest input: $fullPath"
    }
    $relative = [IO.Path]::GetRelativePath($projectRoot, $fullPath).Replace('\', '/')
    $Manifest[$relative] = Get-Sha256 $fullPath
}

function Get-StudyAppCodeFiles {
    param([Parameter(Mandatory)][string]$Directory)
    $blockedDirectories = @(
        'app_data', 'backups', 'cache', 'logs', 'output', 'tests',
        '.git', '.pytest_cache', '__pycache__'
    )
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if ($item.PSIsContainer) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
            if ($item.Name -in $blockedDirectories -or $item.Name -like '.venv*') { continue }
            Get-StudyAppCodeFiles -Directory $item.FullName
            continue
        }
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { continue }
        if ($item.Extension.ToLowerInvariant() -in @('.py', '.qml', '.sql')) {
            $item.FullName
        }
    }
}

function New-SourceInputManifest {
    $manifest = [ordered]@{}
    $studyRoot = Join-Path $projectRoot 'study_app'
    foreach ($file in Get-StudyAppCodeFiles -Directory $studyRoot) {
        Add-SourceManifestFile -Path $file -Manifest $manifest
    }

    # Match non-code resources that the checked-in PyInstaller spec admits;
    # QML SVGs are read from the runtime's shared, explicit resource allowlist.
    $resourcePaths = @(
        'study_app/resources/default_model.json',
        'study_app/resources/default_records.json',
        'study_app/ui/assets/app.ico',
        'study_app/ui/assets/check.svg',
        'study_app/ui/assets/chevron-down.svg',
        'study_app/ui/assets/menu.svg',
        'study_app/ui/assets/nav-assistant.svg',
        'study_app/ui/assets/nav-graph.svg',
        'study_app/ui/assets/nav-home.svg',
        'study_app/ui/assets/nav-plan.svg',
        'study_app/ui/assets/nav-settings.svg'
    )
    $qmlResourcesFile = Join-Path $studyRoot 'quick\resources.py'
    $qmlResourcesText = Get-Content -LiteralPath $qmlResourcesFile -Raw -Encoding utf8
    $tupleMatch = [Regex]::Match($qmlResourcesText, '(?s)QML_RESOURCE_FILES\s*=\s*\((?<items>.*?)\)')
    if (-not $tupleMatch.Success) {
        throw "Cannot parse QML_RESOURCE_FILES from $qmlResourcesFile"
    }
    $resourceMatches = [Regex]::Matches($tupleMatch.Groups['items'].Value, '"([^"\r\n]*)"')
    if ($resourceMatches.Count -eq 0) {
        throw "QML_RESOURCE_FILES contains no resource paths: $qmlResourcesFile"
    }
    $qmlResourceNames = @($resourceMatches | ForEach-Object { $_.Groups[1].Value })
    if (@($qmlResourceNames | Sort-Object -Unique).Count -ne $qmlResourceNames.Count) {
        throw "QML_RESOURCE_FILES contains duplicate paths: $qmlResourcesFile"
    }
    foreach ($name in $qmlResourceNames) {
        $normalized = $name.Replace('\', '/')
        $extension = [IO.Path]::GetExtension($normalized).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($normalized) -or [IO.Path]::IsPathRooted($normalized) -or
            $normalized -match '(^|/)\.\.?(/|$)' -or $normalized.Contains(':') -or
            ($extension -notin @('.qml', '.svg', '.png', '.ttf', '.txt') -and $normalized -ne 'qmldir')) {
            throw "Unsupported or unsafe QML resource path in QML_RESOURCE_FILES: $name"
        }
        $resourcePaths += 'study_app/quick/qml/' + $normalized
    }
    foreach ($relative in $resourcePaths) {
        $localRelative = $relative.Replace('/', '\')
        Add-SourceManifestFile -Path (Join-Path $projectRoot $localRelative) -Manifest $manifest
    }

    $launcher = Join-Path $projectRoot 'run_study_app.pyw'
    Add-SourceManifestFile -Path $launcher -Manifest $manifest
    foreach ($file in Get-ChildItem -LiteralPath $projectRoot -File -Filter 'learning_*.py') {
        Add-SourceManifestFile -Path $file.FullName -Manifest $manifest
    }

    $expectedQmlResources = @($qmlResourceNames | ForEach-Object { 'study_app/quick/qml/' + $_.Replace('\', '/') })
    $actualQmlResources = @($manifest.Keys | Where-Object { $_.StartsWith('study_app/quick/qml/', [StringComparison]::OrdinalIgnoreCase) })
    $missingQmlResources = @($expectedQmlResources | Where-Object { $_ -notin $actualQmlResources })
    $extraQmlResources = @($actualQmlResources | Where-Object { $_ -notin $expectedQmlResources })
    if ($missingQmlResources.Count -gt 0 -or $extraQmlResources.Count -gt 0) {
        throw "QML source manifest does not exactly match QML_RESOURCE_FILES. Missing: $($missingQmlResources -join ', '); extra: $($extraQmlResources -join ', ')"
    }
    $allowedExtensions = @('.py', '.pyw', '.qml', '.sql', '.svg', '.png', '.ico', '.json', '.ttf', '.txt')
    $unsupportedPaths = @(
        $manifest.Keys | Where-Object {
            ([IO.Path]::GetExtension($_).ToLowerInvariant() -notin $allowedExtensions) -and
            ([IO.Path]::GetFileName($_) -ne 'qmldir')
        }
    )
    if ($unsupportedPaths.Count -gt 0) {
        throw "Source manifest contains unsupported file extensions: $($unsupportedPaths -join ', ')"
    }

    $entries = @(
        foreach ($relative in ($manifest.Keys | Sort-Object)) {
            [ordered]@{ path = $relative; sha256 = $manifest[$relative] }
        }
    )
    return [ordered]@{ schemaVersion = 1; files = $entries }
}

function Get-SourceInputManifestDifferences {
    param(
        [Parameter(Mandatory)][object]$Expected,
        [Parameter(Mandatory)][object]$Actual
    )
    $expectedByPath = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
    $actualByPath = [System.Collections.Generic.Dictionary[string, string]]::new([System.StringComparer]::Ordinal)
    foreach ($entry in $Expected.files) { $expectedByPath[[string]$entry.path] = [string]$entry.sha256 }
    foreach ($entry in $Actual.files) { $actualByPath[[string]$entry.path] = [string]$entry.sha256 }

    $paths = @($expectedByPath.Keys)
    $paths += @($actualByPath.Keys)
    $paths = @($paths | Sort-Object -Unique)
    $differences = @(
        foreach ($path in $paths) {
            $hasExpected = $expectedByPath.ContainsKey($path)
            $hasActual = $actualByPath.ContainsKey($path)
            if (-not $hasExpected) {
                "added: $path"
            } elseif (-not $hasActual) {
                "removed: $path"
            } elseif ($expectedByPath[$path] -ne $actualByPath[$path]) {
                "changed: $path"
            }
        }
    )
    return $differences
}

function Get-RecordedInputHashDifferences {
    param([Parameter(Mandatory)][System.Collections.IDictionary]$Inputs)
    $differences = @(
        foreach ($name in $Inputs.Keys) {
            $entry = $Inputs[$name]
            $relativePath = ([string]$entry.path).Replace('/', '\')
            $path = Join-Path $projectRoot $relativePath
            if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
                "missing: $($entry.path)"
            } elseif ((Get-Sha256 $path) -ne [string]$entry.sha256) {
                "changed: $($entry.path)"
            }
        }
    )
    return $differences
}

function Add-DistributionManifestDirectory {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$RelativeRoot,
        [Parameter(Mandatory)][AllowEmptyCollection()][System.Collections.Generic.List[object]]$Files
    )
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing linked packaged output: $($item.FullName)"
        }
        if ($item.PSIsContainer) {
            Add-DistributionManifestDirectory -Directory $item.FullName -RelativeRoot $RelativeRoot -Files $Files
            continue
        }
        $relative = [IO.Path]::GetRelativePath($RelativeRoot, $item.FullName).Replace('\', '/')
        $Files.Add([ordered]@{
            path = $relative
            sha256 = Get-Sha256 $item.FullName
        })
    }
}

function New-DistributionManifest {
    param(
        [Parameter(Mandatory)][string]$Directory,
        [Parameter(Mandatory)][string]$RelativeTo
    )
    if (-not (Test-Path -LiteralPath $Directory -PathType Container)) {
        throw "Packaged output directory is missing: $Directory"
    }
    $root = [IO.Path]::GetFullPath($Directory)
    if (((Get-Item -LiteralPath $root).Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing linked packaged output directory: $root"
    }
    $manifestRoot = [IO.Path]::GetFullPath($RelativeTo)
    $manifestPrefix = $manifestRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
    if (-not $root.StartsWith($manifestPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Packaged output is outside the manifest root: $root"
    }
    $files = [System.Collections.Generic.List[object]]::new()
    Add-DistributionManifestDirectory -Directory $root -RelativeRoot $manifestRoot -Files $files
    $entries = @($files | Sort-Object -Property path)
    if ($entries.Count -eq 0) {
        throw "Packaged output contains no files: $root"
    }
    return [ordered]@{ schemaVersion = 1; files = $entries }
}

function Invoke-NativeLogged {
    param(
        [Parameter(Mandatory)][string]$FilePath,
        [Parameter(Mandatory)][string[]]$NativeArgs,
        [Parameter(Mandatory)][string]$LogPath,
        [Parameter(Mandatory)][string]$Description
    )
    Write-Host "==> $Description"
    $lines = @(& $FilePath @NativeArgs 2>&1)
    $exitCode = $LASTEXITCODE
    $textLines = @($lines | ForEach-Object { [string]$_ })
    [IO.File]::WriteAllText(
        $LogPath,
        (($textLines -join [Environment]::NewLine).TrimEnd() + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
    foreach ($line in $textLines) { Write-Host $line }
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode; see $LogPath"
    }
}

if (-not (Test-Path -LiteralPath $outputRoot -PathType Container)) {
    throw "Project output directory does not exist: $outputRoot"
}
foreach ($requiredFile in @($specFile, $installerSpecFile, $versionResourceFile, $lockFile, $metadataFile, $verifyScript, $buildScriptFile)) {
    if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
        throw "Required QA build input is missing: $requiredFile"
    }
}

$versionText = Get-Content -LiteralPath $metadataFile -Raw -Encoding utf8
$versionMatch = [Regex]::Match($versionText, '(?m)^APP_VERSION\s*=\s*["'']([^"'']+)["'']\s*$')
if (-not $versionMatch.Success) {
    throw "Cannot read APP_VERSION from $metadataFile"
}
$appVersion = $versionMatch.Groups[1].Value

if ([string]::IsNullOrWhiteSpace($RunName)) {
    $RunName = 'qa-{0}-{1}' -f (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss'), ([guid]::NewGuid().ToString('N').Substring(0, 8))
}
if ($RunName -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$' -or
    $RunName.EndsWith('.') -or $RunName.EndsWith(' ')) {
    throw 'RunName must be a single safe folder name of 1-64 ASCII letters, digits, dots, underscores, or hyphens.'
}
$reservedName = ($RunName -split '\.')[0]
if ($reservedName -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$') {
    throw "RunName is a reserved Windows device name: $RunName"
}

$runDirectory = [IO.Path]::GetFullPath((Join-Path $outputRoot $RunName))
$outputPrefix = $outputRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $runDirectory.StartsWith($outputPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing output outside project/output: $runDirectory"
}
if (Test-Path -LiteralPath $runDirectory) {
    throw "RunName already exists; choose a new unique name. Existing data was left untouched: $runDirectory"
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $projectRoot '.venv-qml\Scripts\python.exe'
} elseif (-not [IO.Path]::IsPathRooted($PythonPath)) {
    $PythonPath = Join-Path $projectRoot $PythonPath
}
$PythonPath = [IO.Path]::GetFullPath($PythonPath)

$probeCode = @'
import importlib.metadata as metadata
import json, platform, re, struct, sys
from pathlib import Path

def norm(name):
    return re.sub(r"[-_.]+", "-", name).casefold()

installed = {}
for item in metadata.distributions():
    name = item.metadata.get("Name")
    if name:
        installed[norm(str(name))] = str(item.version)

pins = {}
pin_errors = []
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([^;\s]+)", line)
    if match is None:
        pin_errors.append("invalid pin: " + line)
        continue
    name, expected = match.groups()
    actual = installed.get(norm(name))
    pins[name] = {"expected": expected, "installed": actual, "matches": actual == expected}
    if actual is None:
        pin_errors.append(f"missing {name}=={expected}")
    elif actual != expected:
        pin_errors.append(f"{name}: expected {expected}, found {actual}")

qt = {"moduleVersion": None, "runtimeVersion": None, "error": None}
try:
    import PySide6
    from PySide6.QtCore import qVersion
    qt["moduleVersion"] = str(getattr(PySide6, "__version__", "")) or None
    qt["runtimeVersion"] = str(qVersion())
except Exception as error:
    qt["error"] = type(error).__name__ + ": " + str(error)

locked = {norm(name) for name in pins}
extras = sorted(name for name in installed if name not in locked)
print(json.dumps({
    "python": {
        "version": platform.python_version(),
        "implementation": platform.python_implementation(),
        "bits": struct.calcsize("P") * 8,
        "platform": sys.platform,
        "executable": str(Path(sys.executable).resolve()),
        "baseExecutable": str(Path(getattr(sys, "_base_executable", sys.executable)).resolve()),
    },
    "qt": qt,
    "packages": dict(sorted(installed.items())),
    "lockedPackages": pins,
    "pinErrors": pin_errors,
    "unlockedDistributions": extras,
}, ensure_ascii=True))
'@

$runCreated = $false
$reportDirectory = Join-Path $runDirectory 'metadata'
$environmentPath = Join-Path $reportDirectory 'environment.json'
$recordPath = Join-Path $reportDirectory 'run-record.json'
$buildLogPath = Join-Path $reportDirectory 'build.log'
$verifyLogPath = Join-Path $reportDirectory 'verification.log'
$runtime = $null
$mode = if ($RequireReleaseRuntime) { 'release-runtime-reproduction' } else { 'internal-qa' }
$summary = [ordered]@{
    schemaVersion = 1
    application = 'LearningModel'
    version = $appVersion
    runName = $RunName
    mode = $mode
    releaseRuntimeRequired = [bool]$RequireReleaseRuntime
    status = 'preflight'
    startedAt = (Get-Date).ToUniversalTime().ToString('o')
    finishedAt = $null
    pythonPath = $PythonPath
    outputDirectory = $runDirectory
    environmentReport = 'metadata/environment.json'
    buildLog = 'metadata/build.log'
    verificationLog = 'metadata/verification.log'
    inputs = [ordered]@{
        buildScript = [ordered]@{ path = 'tools/build_qml_qa.ps1'; sha256 = Get-Sha256 $buildScriptFile }
        spec = [ordered]@{ path = 'packaging/LearningModel.spec'; sha256 = Get-Sha256 $specFile }
        installerSpec = [ordered]@{ path = 'packaging/LearningModel.iss'; sha256 = Get-Sha256 $installerSpecFile }
        versionResource = [ordered]@{ path = 'packaging/version_info.txt'; sha256 = Get-Sha256 $versionResourceFile }
        lock = [ordered]@{ path = 'requirements-build.lock.txt'; sha256 = Get-Sha256 $lockFile }
        verifier = [ordered]@{ path = 'tools/verify_release.ps1'; sha256 = Get-Sha256 $verifyScript }
    }
    error = $null
}

if ($PreflightOnly) {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python interpreter not found: $PythonPath"
    }
    try {
        $probeOutput = & $PythonPath -B -c $probeCode $lockFile
        $probeExitCode = $LASTEXITCODE
    } catch {
        throw "Cannot start Python interpreter '$PythonPath': $($_.Exception.Message)"
    }
    if ($probeExitCode -ne 0) {
        throw "Python environment probe failed with exit code $probeExitCode."
    }
    $runtime = ($probeOutput -join [Environment]::NewLine) | ConvertFrom-Json
    $architectureOk = $runtime.python.implementation -eq 'CPython' -and
        $runtime.python.platform -eq 'win32' -and [int]$runtime.python.bits -eq 64
    if (-not $architectureOk) {
        throw "QA packaging requires 64-bit CPython on Windows; found $($runtime.python.implementation) $($runtime.python.version), $($runtime.python.bits)-bit, $($runtime.python.platform)."
    }
    if ($RequireReleaseRuntime -and $runtime.python.version -ne '3.14.5') {
        throw "Release-runtime reproduction requires CPython 3.14.5 x64; found $($runtime.python.implementation) $($runtime.python.version), $($runtime.python.bits)-bit. No build was started."
    }
    if (@($runtime.pinErrors).Count -gt 0) {
        throw "Pinned build dependencies are missing or mismatched: $(@($runtime.pinErrors) -join '; ')"
    }
    if ($RequireReleaseRuntime -and @($runtime.unlockedDistributions).Count -gt 0) {
        throw "Release-runtime reproduction requires an exact locked environment; found unlocked distributions: $(@($runtime.unlockedDistributions) -join ', '). No build was started."
    }
    if (-not $runtime.qt.runtimeVersion -or $runtime.qt.error) {
        throw "Qt/PySide6 runtime probe failed: $($runtime.qt.error)"
    }
    $summary.status = 'preflight-passed'
    $summary.runtime = $runtime.python
    $summary.qt = $runtime.qt
    $summary.unlockedDistributionCount = @($runtime.unlockedDistributions).Count
    $preflightSourceManifest = New-SourceInputManifest
    $summary.sourceInputCount = @($preflightSourceManifest.files).Count
    $summary.sourceInputFiles = @($preflightSourceManifest.files)
    $summary | ConvertTo-Json -Depth 8 -Compress
    return
}

# Create exactly one fresh run directory. Existing runs and legacy build/dist/
# release folders are never removed or replaced.
New-Item -ItemType Directory -Path $runDirectory -ErrorAction Stop | Out-Null
$runCreated = $true
New-Item -ItemType Directory -Path $reportDirectory -ErrorAction Stop | Out-Null
$summary.status = 'probing-runtime'
Write-JsonFile -Path $recordPath -Value $summary

$environmentNames = @('PATH', 'PYTHONUTF8', 'PYTHONDONTWRITEBYTECODE', 'PYINSTALLER_CONFIG_DIR', 'TEMP', 'TMP')
$savedEnvironment = @{}
foreach ($name in $environmentNames) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$failure = $null
try {
    if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
        throw "Python interpreter not found: $PythonPath"
    }
    $probeOutput = & $PythonPath -B -c $probeCode $lockFile
    $probeExitCode = $LASTEXITCODE
    if ($probeExitCode -ne 0) {
        throw "Python environment probe failed with exit code $probeExitCode."
    }
    $runtime = ($probeOutput -join [Environment]::NewLine) | ConvertFrom-Json
    Write-JsonFile -Path $environmentPath -Value $runtime
    $summary.runtime = $runtime.python
    $summary.qt = $runtime.qt
    $summary.unlockedDistributionCount = @($runtime.unlockedDistributions).Count
    Write-JsonFile -Path $recordPath -Value $summary

    $architectureOk = $runtime.python.implementation -eq 'CPython' -and
        $runtime.python.platform -eq 'win32' -and [int]$runtime.python.bits -eq 64
    if (-not $architectureOk) {
        throw "QA packaging requires 64-bit CPython on Windows; found $($runtime.python.implementation) $($runtime.python.version), $($runtime.python.bits)-bit, $($runtime.python.platform)."
    }
    if ($RequireReleaseRuntime -and $runtime.python.version -ne '3.14.5') {
        throw "Release-runtime reproduction requires CPython 3.14.5 x64; found $($runtime.python.implementation) $($runtime.python.version), $($runtime.python.bits)-bit."
    }
    if (@($runtime.pinErrors).Count -gt 0) {
        throw "Pinned build dependencies are missing or mismatched: $(@($runtime.pinErrors) -join '; ')"
    }
    if ($RequireReleaseRuntime -and @($runtime.unlockedDistributions).Count -gt 0) {
        throw "Release-runtime reproduction requires an exact locked environment; found unlocked distributions: $(@($runtime.unlockedDistributions) -join ', '). No PyInstaller build was started."
    }
    if (-not $runtime.qt.runtimeVersion -or $runtime.qt.error) {
        throw "Qt/PySide6 runtime probe failed: $($runtime.qt.error)"
    }
    if (@($runtime.packages.PSObject.Properties.Name) -notcontains 'pyinstaller') {
        throw 'PyInstaller is not installed in the selected interpreter; no installation was attempted.'
    }

    $sourceManifest = New-SourceInputManifest
    $sourceManifestPath = Join-Path $reportDirectory 'source-inputs.json'
    Write-JsonFile -Path $sourceManifestPath -Value $sourceManifest
    $summary.sourceInputs = [ordered]@{
        path = 'metadata/source-inputs.json'
        fileCount = @($sourceManifest.files).Count
        sha256 = Get-Sha256 $sourceManifestPath
    }

    $summary.status = 'building'
    Write-JsonFile -Path $recordPath -Value $summary

    $distPath = Join-Path $runDirectory 'dist'
    $workPath = Join-Path $runDirectory 'work'
    $pyInstallerArgs = @(
        '-B', '-m', 'PyInstaller',
        '--noconfirm',
        '--distpath', $distPath,
        '--workpath', $workPath,
        $specFile
    )
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYINSTALLER_CONFIG_DIR = Join-Path $runDirectory 'pyinstaller-config'
    $runTemp = Join-Path $runDirectory 'temp'
    New-Item -ItemType Directory -Path $runTemp -ErrorAction Stop | Out-Null
    $env:TEMP = $runTemp
    $env:TMP = $runTemp
    $pythonDirectory = [IO.Path]::GetDirectoryName($PythonPath)
    $basePythonDirectory = [IO.Path]::GetDirectoryName([string]$runtime.python.baseExecutable)
    $buildPathEntries = @(
        $pythonDirectory,
        $basePythonDirectory,
        (Join-Path $env:SystemRoot 'System32'),
        $env:SystemRoot
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique
    $env:PATH = $buildPathEntries -join [IO.Path]::PathSeparator
    Invoke-NativeLogged -FilePath $PythonPath -NativeArgs $pyInstallerArgs -LogPath $buildLogPath -Description 'Build isolated onedir QA package'

    $distDirectory = Join-Path $distPath 'LearningModel'
    $applicationExe = Join-Path $distDirectory 'LearningModel.exe'
    $warningPath = Join-Path $workPath 'LearningModel\warn-LearningModel.txt'
    if (-not (Test-Path -LiteralPath $applicationExe -PathType Leaf)) {
        throw "PyInstaller did not produce $applicationExe"
    }
    if (-not (Test-Path -LiteralPath $warningPath -PathType Leaf)) {
        throw "PyInstaller did not produce its import-warning report: $warningPath"
    }
    $warningText = Get-Content -LiteralPath $warningPath -Raw -Encoding utf8
    $criticalMissingImports = @(
        $warningText -split '\r?\n' |
            Where-Object { $_ -match '^missing module named (study_app(?:\.|\s)|learning_[A-Za-z0-9_]+)' }
    )
    if ($criticalMissingImports.Count -gt 0) {
        throw "PyInstaller omitted application modules:$([Environment]::NewLine)$($criticalMissingImports -join [Environment]::NewLine)"
    }
    Copy-Item -LiteralPath $warningPath -Destination (Join-Path $reportDirectory 'pyinstaller-warnings.txt') -ErrorAction Stop

    $summary.status = 'verifying'
    $summary.distDirectory = [IO.Path]::GetRelativePath($runDirectory, $distDirectory).Replace('\', '/')
    Write-JsonFile -Path $recordPath -Value $summary
    $pwshPath = Join-Path $PSHOME 'pwsh.exe'
    if (-not (Test-Path -LiteralPath $pwshPath -PathType Leaf)) {
        throw "PowerShell 7 executable was not found at $pwshPath"
    }
    $verifyArgs = @(
        '-NoLogo', '-NoProfile', '-NonInteractive',
        '-File', $verifyScript,
        '-DistDirectory', $distDirectory,
        '-ExpectedVersion', $appVersion,
        '-RunSelfTest'
    )
    Invoke-NativeLogged -FilePath $pwshPath -NativeArgs $verifyArgs -LogPath $verifyLogPath -Description 'Verify onedir bundle and packaged self-test'

    $distManifest = New-DistributionManifest -Directory $distDirectory -RelativeTo $runDirectory
    $distManifestPath = Join-Path $reportDirectory 'dist-inputs.json'
    Write-JsonFile -Path $distManifestPath -Value $distManifest
    $summary.distInputs = [ordered]@{
        path = 'metadata/dist-inputs.json'
        fileCount = @($distManifest.files).Count
        sha256 = Get-Sha256 $distManifestPath
    }

    $postBuildSourceManifest = New-SourceInputManifest
    $postBuildSourceManifestPath = Join-Path $reportDirectory 'source-inputs-after-build.json'
    Write-JsonFile -Path $postBuildSourceManifestPath -Value $postBuildSourceManifest
    $sourceManifestDifferences = @(Get-SourceInputManifestDifferences -Expected $sourceManifest -Actual $postBuildSourceManifest)
    $summary.sourceInputVerification = [ordered]@{
        status = if ($sourceManifestDifferences.Count -eq 0) { 'passed' } else { 'failed' }
        path = 'metadata/source-inputs-after-build.json'
        fileCount = @($postBuildSourceManifest.files).Count
        sha256 = Get-Sha256 $postBuildSourceManifestPath
        differences = $sourceManifestDifferences
    }
    if ($sourceManifestDifferences.Count -gt 0) {
        throw "Source inputs changed during the QA build: $($sourceManifestDifferences -join '; '). Discard this package and run a fresh build."
    }

    $inputHashDifferences = @(Get-RecordedInputHashDifferences -Inputs $summary.inputs)
    $summary.inputVerification = [ordered]@{
        status = if ($inputHashDifferences.Count -eq 0) { 'passed' } else { 'failed' }
        checkedCount = @($summary.inputs.Keys).Count
        differences = $inputHashDifferences
    }
    if ($inputHashDifferences.Count -gt 0) {
        throw "Build inputs changed during the QA build: $($inputHashDifferences -join '; '). Discard this package and run a fresh build."
    }

    $summary.status = 'passed'
    $summary.executable = [IO.Path]::GetRelativePath($runDirectory, $applicationExe).Replace('\', '/')
    $summary.executableSha256 = Get-Sha256 $applicationExe
    $summary.executableBytes = (Get-Item -LiteralPath $applicationExe).Length
} catch {
    $failure = $_.Exception.Message
    $summary.status = 'failed'
    $summary.error = $failure
} finally {
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    if ($runCreated) {
        $summary.finishedAt = (Get-Date).ToUniversalTime().ToString('o')
        try { Write-JsonFile -Path $recordPath -Value $summary } catch { Write-Warning "Could not write run record: $($_.Exception.Message)" }
    }
}

if ($failure) {
    Write-Error "QA build failed: $failure. Run record: $recordPath"
    exit 1
}
Write-Host "Internal QA package verified: $(Join-Path $runDirectory $summary.executable)"
Write-Host "Runtime: CPython $($runtime.python.version) x64; Qt $($runtime.qt.runtimeVersion)."
Write-Host "This is classified as $($summary.mode), not a formal release build."
