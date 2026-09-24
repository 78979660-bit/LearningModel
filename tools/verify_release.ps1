#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$DistDirectory = '',
    [string]$InstallerPath = '',
    [string]$ExpectedVersion = '0.1.0',
    [switch]$RunSelfTest,
    [switch]$PublicRelease
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if ([string]::IsNullOrWhiteSpace($DistDirectory)) {
    $DistDirectory = Join-Path $projectRoot 'dist\LearningModel'
} elseif (-not [IO.Path]::IsPathRooted($DistDirectory)) {
    $DistDirectory = Join-Path $projectRoot $DistDirectory
}
$distRoot = [IO.Path]::GetFullPath($DistDirectory)
$exePath = Join-Path $distRoot 'LearningModel.exe'

function Add-Failure {
    param([System.Collections.Generic.List[string]]$Failures, [string]$Message)
    $Failures.Add($Message)
}

function Get-RelativePathSafe {
    param([string]$BasePath, [string]$ChildPath)
    return [IO.Path]::GetRelativePath($BasePath, $ChildPath).Replace('/', '\')
}

$failures = [System.Collections.Generic.List[string]]::new()

if ($PublicRelease) {
    Add-Failure $failures 'PUBLIC RELEASE BLOCKED: draft prerelease workflow; see docs/RELEASE_ACCEPTANCE.md for completed and outstanding validation.'
}

if (-not (Test-Path -LiteralPath $distRoot -PathType Container)) {
    Add-Failure $failures "Missing onedir output: $distRoot"
} elseif (-not (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    Add-Failure $failures "Missing executable: $exePath"
}

if ($failures.Count -eq 0 -or (Test-Path -LiteralPath $distRoot -PathType Container)) {
    $allowedTopLevel = @('LearningModel.exe', '_internal')
    foreach ($item in Get-ChildItem -LiteralPath $distRoot -Force) {
        if ($item.Name -notin $allowedTopLevel) {
            Add-Failure $failures "Unexpected top-level release item: $($item.Name)"
        }
    }

    $forbiddenSegments = @(
        'app_data', 'tests', '.pytest_cache', '.git', '.venv', '.venv-build',
        '__pycache__', 'backups', 'logs', 'cache'
    )
    $forbiddenNames = @(
        'learning_model_v1.json', 'learning_records.json', '.env',
        'id_rsa', 'id_ed25519'
    )
    $allItems = Get-ChildItem -LiteralPath $distRoot -Recurse -Force
    foreach ($item in $allItems) {
        $relative = Get-RelativePathSafe $distRoot $item.FullName
        $segments = $relative -split '[\\/]'
        if ($segments | Where-Object { $_.ToLowerInvariant() -in $forbiddenSegments }) {
            Add-Failure $failures "Forbidden release path: $relative"
        }
        if (-not $item.PSIsContainer -and $item.Name.ToLowerInvariant() -in $forbiddenNames) {
            Add-Failure $failures "Forbidden release file: $relative"
        }
        if (-not $item.PSIsContainer -and $item.Extension.ToLowerInvariant() -in @('.sqlite', '.sqlite3', '.db', '.pem', '.key')) {
            Add-Failure $failures "Database or credential-like file in release: $relative"
        }
    }

    $allowedProjectData = @(
        'data\schema.sql',
        'data\f5_schema.sql',
        'resources\default_model.json',
        'resources\default_records.json',
        'ui\assets\check.svg',
        'ui\assets\chevron-down.svg',
        'ui\assets\menu.svg',
        'ui\assets\nav-assistant.svg',
        'ui\assets\nav-graph.svg',
        'ui\assets\nav-home.svg',
        'ui\assets\nav-plan.svg',
        'ui\assets\nav-settings.svg',
        'integrations\fill_chatgpt_prompt.ps1',
        'integrations\run_chatgpt_pdf_workflow.ps1',
        'integrations\capture_chatgpt_pdf.ps1'
    )
    $packagedStudyApp = Join-Path $distRoot '_internal\study_app'
    if (-not (Test-Path -LiteralPath $packagedStudyApp -PathType Container)) {
        Add-Failure $failures 'Missing packaged study_app resource directory.'
    } else {
        foreach ($file in Get-ChildItem -LiteralPath $packagedStudyApp -Recurse -File) {
            $relative = Get-RelativePathSafe $packagedStudyApp $file.FullName
            if ($relative -notin $allowedProjectData) {
                Add-Failure $failures "Project resource is outside the strict whitelist: $relative"
            }
        }
        foreach ($relative in $allowedProjectData) {
            $required = Join-Path $packagedStudyApp $relative
            if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
                Add-Failure $failures "Required packaged resource is missing: study_app\$relative"
            }
        }
    }

    foreach ($jsonRelative in @(
        '_internal\study_app\resources\default_model.json',
        '_internal\study_app\resources\default_records.json'
    )) {
        $jsonPath = Join-Path $distRoot $jsonRelative
        if (Test-Path -LiteralPath $jsonPath -PathType Leaf) {
            try {
                $null = Get-Content -LiteralPath $jsonPath -Raw -Encoding utf8 | ConvertFrom-Json
            } catch {
                Add-Failure $failures "Invalid packaged JSON: $jsonRelative ($($_.Exception.Message))"
            }
        }
    }

    $textExtensions = @('.txt', '.json', '.ini', '.xml', '.md', '.html', '.ps1', '.sql', '.svg')
    $escapedProjectRoot = [Regex]::Escape($projectRoot)
    foreach ($file in Get-ChildItem -LiteralPath $distRoot -Recurse -File) {
        if ($file.Extension.ToLowerInvariant() -notin $textExtensions -or $file.Length -gt 5MB) {
            continue
        }
        $content = Get-Content -LiteralPath $file.FullName -Raw -Encoding utf8 -ErrorAction SilentlyContinue
        if ($null -ne $content -and $content -match $escapedProjectRoot) {
            $relative = Get-RelativePathSafe $distRoot $file.FullName
            Add-Failure $failures "Developer source path leaked into text resource: $relative"
        }
    }
}

if (Test-Path -LiteralPath $exePath -PathType Leaf) {
    $versionInfo = (Get-Item -LiteralPath $exePath).VersionInfo
    if ($versionInfo.ProductVersion -notlike "$ExpectedVersion*") {
        Add-Failure $failures "Executable product version '$($versionInfo.ProductVersion)' does not match $ExpectedVersion."
    }
    if ($versionInfo.FileDescription -ne '学习模型') {
        Add-Failure $failures "Unexpected executable description: '$($versionInfo.FileDescription)'"
    }
}

if (-not [string]::IsNullOrWhiteSpace($InstallerPath)) {
    if (-not [IO.Path]::IsPathRooted($InstallerPath)) {
        $InstallerPath = Join-Path $projectRoot $InstallerPath
    }
    $resolvedInstaller = [IO.Path]::GetFullPath($InstallerPath)
    if (-not (Test-Path -LiteralPath $resolvedInstaller -PathType Leaf)) {
        Add-Failure $failures "Missing installer: $resolvedInstaller"
    } else {
        $expectedName = "LearningModel-Setup-$ExpectedVersion-x64.exe"
        if ([IO.Path]::GetFileName($resolvedInstaller) -ne $expectedName) {
            Add-Failure $failures "Installer filename must be $expectedName"
        }
        if ((Get-Item -LiteralPath $resolvedInstaller).Length -le 0) {
            Add-Failure $failures 'Installer is empty.'
        }
    }
}

if ($RunSelfTest -and (Test-Path -LiteralPath $exePath -PathType Leaf)) {
    $tempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    $selfTestRoot = Join-Path $tempBase ("LearningModel-verify-" + [guid]::NewGuid().ToString('N'))
    $selfTestData = Join-Path $selfTestRoot 'user-data'
    $selfTestResult = Join-Path $selfTestRoot 'self-test.json'
    New-Item -ItemType Directory -Path $selfTestRoot -ErrorAction Stop | Out-Null
    try {
        $processInfo = [Diagnostics.ProcessStartInfo]::new()
        $processInfo.FileName = $exePath
        $processInfo.UseShellExecute = $false
        $processInfo.ArgumentList.Add('--self-test')
        $processInfo.ArgumentList.Add($selfTestResult)
        $processInfo.Environment['LEARNINGMODEL_DATA_ROOT'] = $selfTestData
        $processInfo.Environment['PYTHONUTF8'] = '1'
        $process = [Diagnostics.Process]::Start($processInfo)
        if (-not $process.WaitForExit(120000)) {
            $process.Kill($true)
            Add-Failure $failures 'Packaged self-test timed out after 120 seconds.'
        } elseif ($process.ExitCode -ne 0) {
            Add-Failure $failures "Packaged self-test exited with code $($process.ExitCode)."
        } elseif (-not (Test-Path -LiteralPath $selfTestResult -PathType Leaf)) {
            Add-Failure $failures 'Packaged self-test did not produce its JSON result.'
        } else {
            try {
                $result = Get-Content -LiteralPath $selfTestResult -Raw -Encoding utf8 | ConvertFrom-Json
                if ($result.status -ne 'ok' -or $result.version -ne $ExpectedVersion) {
                    Add-Failure $failures 'Packaged self-test returned an unexpected status or version.'
                }
                if (-not $result.model_exists -or -not $result.records_exists) {
                    Add-Failure $failures 'Packaged self-test did not create clean default user data.'
                }
                $requiredSmokeModules = @(
                    'PySide6.QtWidgets',
                    'PIL.Image',
                    'fitz',
                    'pymupdf',
                    'pypdf',
                    'pytesseract',
                    'reportlab.pdfgen.canvas',
                    'study_app.core.subject_pdf_pipeline',
                    'study_app.ui.main_window'
                )
                foreach ($moduleName in $requiredSmokeModules) {
                    if ($moduleName -notin @($result.module_smoke)) {
                        Add-Failure $failures "Packaged self-test omitted module smoke: $moduleName"
                    }
                }
                $expectedUserRoot = [IO.Path]::GetFullPath($selfTestData).TrimEnd('\')
                $reportedUserRoot = [IO.Path]::GetFullPath([string]$result.user_root).TrimEnd('\')
                if ($reportedUserRoot -ne $expectedUserRoot) {
                    Add-Failure $failures 'Packaged application ignored LEARNINGMODEL_DATA_ROOT during self-test.'
                }
            } catch {
                Add-Failure $failures "Cannot validate packaged self-test output: $($_.Exception.Message)"
            }
        }
    } finally {
        $resolvedSelfTestRoot = [IO.Path]::GetFullPath($selfTestRoot)
        $expectedPrefix = $tempBase.TrimEnd('\') + '\LearningModel-verify-'
        if ($resolvedSelfTestRoot.StartsWith($expectedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            Remove-Item -LiteralPath $resolvedSelfTestRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Error ("Release verification failed:`n - " + ($failures -join "`n - "))
    exit 1
}

$fileCount = (Get-ChildItem -LiteralPath $distRoot -Recurse -File).Count
$totalBytes = (Get-ChildItem -LiteralPath $distRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Output "Release verification passed: $fileCount files, $totalBytes bytes, version $ExpectedVersion."
Write-Output 'Scope: internal/QA binary; open-source decision recorded in docs/OPEN_SOURCE.md, binary release checklist pending.'
