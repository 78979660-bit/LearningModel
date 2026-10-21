#requires -Version 7.0
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$BuildDirectory,
    [string]$InnoCompilerPath = '',
    [switch]$PreflightOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = Join-Path $projectRoot 'output'
if (-not [IO.Path]::IsPathRooted($BuildDirectory)) {
    $BuildDirectory = Join-Path $projectRoot $BuildDirectory
}
$buildRoot = [IO.Path]::GetFullPath($BuildDirectory)
if (-not [string]::Equals([IO.Path]::GetDirectoryName($buildRoot), $outputRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'BuildDirectory must be an immediate child of the project output directory.'
}

function Assert-PlainFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing input: $Path" }
    $walk = Get-Item -LiteralPath $Path
    while ($null -ne $walk) {
        if (($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Linked input is not accepted: $($walk.FullName)"
        }
        if ([string]::Equals($walk.FullName, $projectRoot, [StringComparison]::OrdinalIgnoreCase)) { break }
        if ($walk -is [IO.FileInfo]) { $walk = $walk.Directory } else { $walk = $walk.Parent }
    }
}

function Get-InputPath([string]$Root, [string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative)) { throw 'Manifest paths must be relative.' }
    $result = [IO.Path]::GetFullPath((Join-Path $Root $Relative))
    if (-not $result.StartsWith($Root.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Manifest path escapes its root: $Relative"
    }
    Assert-PlainFile $result
    return $result
}

function Assert-Hash([string]$Path, [string]$Expected) {
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected) {
        throw "Input hash changed: $Path"
    }
}

$recordPath = Get-InputPath $buildRoot 'metadata/run-record.json'
$record = Get-Content -LiteralPath $recordPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
if ($record.status -ne 'passed' -or $record.mode -ne 'release-runtime-reproduction') {
    throw 'Installer requires a passed release-runtime QA build.'
}
if ($record.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid recorded application version.' }
if ($record.distDirectory -ne 'dist/LearningModel' -or $record.executable -ne 'dist/LearningModel/LearningModel.exe') {
    throw 'Unexpected distribution layout in the build receipt.'
}
$version = [string]$record.version
foreach ($entry in $record.inputs.Values) {
    Assert-Hash (Get-InputPath $projectRoot $entry.path) $entry.sha256
}
$issPath = Get-InputPath $projectRoot 'packaging/LearningModel.iss'
if (-not $record.inputs.Contains('installerSpec')) { throw 'Rebuild with installer input tracking before making an installer.' }
Assert-Hash $issPath $record.inputs.installerSpec.sha256

function Assert-Manifests {
    foreach ($pair in @(@($record.sourceInputs, $projectRoot), @($record.distInputs, $buildRoot))) {
        $receipt = $pair[0]
        $manifestPath = Get-InputPath $buildRoot $receipt.path
        Assert-Hash $manifestPath $receipt.sha256
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding utf8 | ConvertFrom-Json -AsHashtable
        if ($manifest.files.Count -ne $receipt.fileCount) { throw 'Manifest file count differs from its build receipt.' }
        foreach ($entry in $manifest.files) {
            Assert-Hash (Get-InputPath $pair[1] $entry.path) $entry.sha256
        }
        if ($receipt -eq $record.distInputs) {
            $actual = @(Get-ChildItem -LiteralPath (Join-Path $buildRoot $record.distDirectory) -File -Recurse)
            if ($actual.Count -ne $manifest.files.Count) { throw 'Distribution file inventory changed.' }
        }
    }
}
Assert-Manifests
$distRoot = [IO.Path]::GetFullPath((Join-Path $buildRoot $record.distDirectory))
$exePath = Get-InputPath $buildRoot $record.executable
Assert-Hash $exePath $record.executableSha256
if ($PreflightOnly) {
    [ordered]@{ status = 'ready'; version = $version; buildDirectory = $buildRoot; publicRelease = $false } | ConvertTo-Json
    return
}
if ([string]::IsNullOrWhiteSpace($InnoCompilerPath)) {
    $InnoCompilerPath = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'
}
if (-not (Test-Path -LiteralPath $InnoCompilerPath -PathType Leaf)) { throw 'Inno Setup compiler not found.' }
$compilerVersion = & $InnoCompilerPath '--version'
$compilerExit = $LASTEXITCODE
if ($compilerExit -ne 0 -or ([string]$compilerVersion).Trim() -ne '7.1.0') { throw 'Inno Setup 7.1.0 is required.' }
$installerRoot = Join-Path $buildRoot 'installer'
if (Test-Path -LiteralPath $installerRoot) { throw 'Installer output already exists; use a new build directory.' }
New-Item -ItemType Directory -Path $installerRoot -ErrorAction Stop | Out-Null
$installerPath = Join-Path $installerRoot "LearningModel-Setup-$version-x64.exe"
$result = [ordered]@{
    status = 'running'; version = $version; publicRelease = $false; signed = $false
    buildRecordSha256 = (Get-FileHash -LiteralPath $recordPath -Algorithm SHA256).Hash.ToLowerInvariant()
    compilerVersion = '7.1.0'; installerSpecSha256 = $record.inputs.installerSpec.sha256
    installerScriptSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    lifecycleAcceptance = 'pending'; defaultEntry = 'qml'; qmlArgument = '--qml'
}
try {
    $compileArgs = @("/DAppVersion=$version", "/DAppSource=$distRoot", "/O$installerRoot", "/FLearningModel-Setup-$version-x64", $issPath)
    & $InnoCompilerPath @compileArgs *> (Join-Path $installerRoot 'compile.log')
    $compileExit = $LASTEXITCODE
    if ($compileExit -ne 0) { throw "Inno Setup failed: $compileExit" }
    Assert-Manifests
    foreach ($entry in $record.inputs.Values) {
        Assert-Hash (Get-InputPath $projectRoot $entry.path) $entry.sha256
    }
    $verifyArgs = @('-NoLogo', '-NoProfile', '-NonInteractive', '-File', (Join-Path $PSScriptRoot 'verify_release.ps1'), '-DistDirectory', $distRoot, '-InstallerPath', $installerPath, '-ExpectedVersion', $version)
    & (Join-Path $PSHOME 'pwsh.exe') @verifyArgs *> (Join-Path $installerRoot 'verification.log')
    $verifyExit = $LASTEXITCODE
    if ($verifyExit -ne 0) { throw "Installer verification failed: $verifyExit" }
    $result['installerSha256'] = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $result['installerBytes'] = (Get-Item -LiteralPath $installerPath).Length
    [IO.File]::WriteAllText((Join-Path $installerRoot 'SHA256SUMS.txt'), "$($result.installerSha256)  $([IO.Path]::GetFileName($installerPath))`n", [Text.UTF8Encoding]::new($false))
    $result.status = 'built-and-verified'
    Write-Output "Internal QA installer: $installerPath"
} catch {
    $result.status = 'failed'
    $result['error'] = $_.Exception.Message
    throw
} finally {
    [IO.File]::WriteAllText((Join-Path $installerRoot 'installer-record.json'), ($result | ConvertTo-Json -Depth 6) + "`n", [Text.UTF8Encoding]::new($false))
}
