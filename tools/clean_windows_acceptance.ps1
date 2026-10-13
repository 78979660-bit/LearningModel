#requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Preflight', 'Install', 'Finish')]
    [string]$Phase = 'Preflight',
    [string]$KitDirectory = '',
    [ValidatePattern('^\d+\.\d+\.\d+$')]
    [string]$Version = '0.1.1'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($KitDirectory)) { $KitDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path }
$kit = (Resolve-Path -LiteralPath $KitDirectory).Path
$installerName = "LearningModel-Setup-$Version-x64.exe"
$installer = Join-Path $kit $installerName
$checksumPattern = '^[0-9a-f]{64}  ' + [Regex]::Escape($installerName) + '$'
$checksum = @(Get-Content -LiteralPath (Join-Path $kit 'SHA256SUMS.txt') | Where-Object { $_ -match $checksumPattern })
if ($checksum.Count -ne 1) { throw 'Expected exactly one installer entry in SHA256SUMS.txt' }
$expectedHash = ($checksum[0] -split '\s+')[0]
$results = Join-Path $kit 'results'
$statePath = Join-Path $results 'acceptance-state.json'
$dataRoot = Join-Path $env:LOCALAPPDATA 'LearningModel'
$appId = '{E06D2BBE-5FA4-4F72-B03E-130652DE5A52}_is1'
$registryKeys = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appId",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$appId",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\$appId"
)

function Write-JsonFile($Path, $Value) {
    [IO.File]::WriteAllText($Path, (($Value | ConvertTo-Json -Depth 12) + "`n"), [Text.UTF8Encoding]::new($false))
}
function Invoke-CheckedProcess($File, [string[]]$Arguments, [int]$TimeoutSeconds = 120) {
    # Arguments are fixed switches or quoted, validated local Windows paths.
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru -WindowStyle Hidden
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        throw 'Process timed out. It was NOT forcibly terminated; inspect it before retrying.'
    }
    $process.Refresh()
    if ($process.ExitCode -ne 0) { throw "Process failed with exit code $($process.ExitCode)" }
}
function Get-Preflight {
    $os = Get-CimInstance Win32_OperatingSystem
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    $admin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $detected = @()
    foreach ($name in @('python.exe', 'python3.exe', 'py.exe', 'git.exe', 'node.exe', 'tesseract.exe')) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command -and $command.Source -notmatch '\\Microsoft\\WindowsApps\\') { $detected += $name }
    }
    $registered = @()
    foreach ($key in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')) {
        $registered += @(Get-ItemProperty -Path $key -ErrorAction SilentlyContinue | Where-Object { $_.PSObject.Properties['DisplayName'] -and $_.DisplayName -match 'Python|Git version|GitHub Desktop|Visual Studio|Codex|Tesseract|ChatGPT' } | Select-Object -ExpandProperty DisplayName)
    }
    $oldInstall = @($registryKeys | Where-Object { Test-Path -LiteralPath $_ }).Count -gt 0
    $inherited = @('LEARNINGMODEL_DATA_ROOT', 'LEARNINGMODEL_LEGACY_ROOT', 'QT_QPA_PLATFORM', 'QT_SCALE_FACTOR') | Where-Object { [Environment]::GetEnvironmentVariable($_, 'Process') }
    $hash = if (Test-Path -LiteralPath $installer) { (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' }
    return [ordered]@{
        time_utc = [DateTime]::UtcNow.ToString('o'); os = $os.Caption; build = $os.BuildNumber
        x64 = [Environment]::Is64BitOperatingSystem; administrator = $admin
        detected_commands = @($detected); registered_development_apps = @($registered)
        existing_installation = $oldInstall; existing_user_data = (Test-Path -LiteralPath $dataRoot)
        inherited_overrides = @($inherited); installer_sha256 = $hash
        installer_matches = ($hash -eq $expectedHash)
        eligible = ([Environment]::Is64BitOperatingSystem -and [int]$os.BuildNumber -ge 17763 -and -not $admin -and $detected.Count -eq 0 -and $registered.Count -eq 0 -and -not $oldInstall -and -not (Test-Path -LiteralPath $dataRoot) -and @($inherited).Count -eq 0 -and $hash -eq $expectedHash)
        manual_review_required = 'Absence from PATH/registry is not proof of a clean machine. Record fresh VM/PC provenance and inspect the manual checklist.'
    }
}
function Run-SelfTest($Program, $Name) {
    $output = Join-Path $results ($Name + '.json')
    Invoke-CheckedProcess $Program @('--self-test', ('"' + $output + '"'))
    if (-not (Test-Path -LiteralPath $output)) { throw 'Self-test report missing' }
    $report = Get-Content -LiteralPath $output -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.status -ne 'ok' -or $report.version -ne $Version -or [int]$report.schema_version -ne 5) { throw 'Self-test status/version/schema mismatch' }
    # Current --self-test deliberately uses a disposable synthetic profile.
    # Its counts must never stand in for the installed user's database.
    if ([IO.Path]::GetFullPath($report.user_root) -eq [IO.Path]::GetFullPath($dataRoot)) { throw 'Self-test unexpectedly used the real profile' }
    if ($report.qml.status -ne 'ok' -or @($report.qml.qml_warnings).Count -ne 0) { throw 'Packaged QML self-test failed' }
    return $report
}

function Get-ActualDatabaseCounts([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw 'Actual acceptance database is missing' }
    if (-not ('LearningModelQA.ReadOnlySqlite' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace LearningModelQA {
    public static class ReadOnlySqlite {
        [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
        private static extern int sqlite3_open_v2(byte[] path, out IntPtr db, int flags, IntPtr vfs);
        [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
        private static extern int sqlite3_prepare_v2(IntPtr db, byte[] sql, int bytes, out IntPtr statement, IntPtr tail);
        [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
        private static extern int sqlite3_step(IntPtr statement);
        [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
        private static extern long sqlite3_column_int64(IntPtr statement, int column);
        [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
        private static extern int sqlite3_finalize(IntPtr statement);
        [DllImport("winsqlite3.dll", CallingConvention=CallingConvention.Cdecl)]
        private static extern int sqlite3_close(IntPtr db);
        public static long Count(string path, string table) {
            // Fixed allowlist: no dynamic SQL supplied by application contents.
            if (table != "subjects" && table != "modules" && table != "topics" && table != "learning_records" && table != "problem_attempts")
                throw new ArgumentException("Unknown count table");
            IntPtr db = IntPtr.Zero, statement = IntPtr.Zero;
            try {
                byte[] file = System.Text.Encoding.UTF8.GetBytes(path + "\0");
                if (sqlite3_open_v2(file, out db, 1, IntPtr.Zero) != 0) throw new InvalidOperationException("Read-only database open failed");
                byte[] sql = System.Text.Encoding.UTF8.GetBytes("SELECT COUNT(*) FROM " + table + "\0");
                if (sqlite3_prepare_v2(db, sql, sql.Length, out statement, IntPtr.Zero) != 0 || sqlite3_step(statement) != 100)
                    throw new InvalidOperationException("Actual database count failed");
                return sqlite3_column_int64(statement, 0);
            } finally {
                if (statement != IntPtr.Zero) sqlite3_finalize(statement);
                if (db != IntPtr.Zero) sqlite3_close(db);
            }
        }
    }
}
'@
    }
    $counts = [ordered]@{}
    foreach ($table in @('subjects', 'modules', 'topics', 'learning_records', 'problem_attempts')) {
        $counts[$table] = [LearningModelQA.ReadOnlySqlite]::Count([IO.Path]::GetFullPath($Path), $table)
    }
    return $counts
}
New-Item -ItemType Directory -Path $results -Force | Out-Null
if ($Phase -eq 'Preflight' -or $Phase -eq 'Install') {
    $preflight = Get-Preflight
    Write-JsonFile (Join-Path $results 'preflight.json') $preflight
    if ($Phase -eq 'Preflight') {
        Write-Output ($preflight | ConvertTo-Json -Depth 6)
        return
    }
    if (-not $preflight.eligible) { throw 'Not an eligible clean standard-user environment. See results/preflight.json. Nothing was installed.' }
    if (Test-Path -LiteralPath $statePath) { throw 'This kit already has an acceptance run. Use a fresh extracted kit.' }
    $runId = [Guid]::NewGuid().ToString('N')
    $testRoot = Join-Path $env:LOCALAPPDATA ('LearningModel-QA-' + $runId)
    $installRoot = Join-Path $testRoot '中文安装目录'
    if (Test-Path -LiteralPath $testRoot) { throw 'Test directory unexpectedly exists' }
    New-Item -ItemType Directory -Path $testRoot | Out-Null
    $state = [ordered]@{ run_id = $runId; test_root = $testRoot; install_root = $installRoot; data_root = $dataRoot; status = 'installing'; installer_sha256 = $expectedHash; manual_pass = $false }
    Write-JsonFile $statePath $state
    $env:LEARNINGMODEL_LEGACY_ROOT = Join-Path $testRoot 'empty-legacy'
    New-Item -ItemType Directory -Path $env:LEARNINGMODEL_LEGACY_ROOT | Out-Null
    Invoke-CheckedProcess $installer @('/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', ('/DIR="' + $installRoot + '"'), ('/LOG="' + (Join-Path $results 'install.log') + '"'))
    $program = Join-Path $installRoot 'LearningModel.exe'
    $selfTest = Run-SelfTest $program 'first-start'
    $state.status = 'awaiting_manual_checks'
    Write-JsonFile $statePath $state
    # Explicit Install phase launches the application for the requested UI checks.
    Start-Process -FilePath $program -ArgumentList '--qml' -WorkingDirectory $testRoot | Out-Null
    Write-Output 'Installation and isolated packaged QML self-test passed. Verify the actual fresh profile and UI in MANUAL-CHECKLIST.md, close the app, then run -Phase Finish.'
    return
}

# Finish only touches the installation created by this kit; no recursive delete.
if (-not (Test-Path -LiteralPath $statePath)) { throw 'Run Install first' }
$state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($state.status -ne 'awaiting_manual_checks') { throw 'This run is not ready for Finish' }
if ([Environment]::GetEnvironmentVariable('LEARNINGMODEL_DATA_ROOT', 'Process')) { throw 'Remove the data-root override before Finish; do not redirect this acceptance run.' }
$expectedRoot = Join-Path $env:LOCALAPPDATA ('LearningModel-QA-' + $state.run_id)
if ($state.run_id -notmatch '^[a-f0-9]{32}$' -or $state.test_root -ne $expectedRoot -or $state.install_root -ne (Join-Path $expectedRoot '中文安装目录') -or $state.data_root -ne $dataRoot) { throw 'Acceptance state path validation failed' }
if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) { throw 'Installer changed' }
if (Get-Process -Name LearningModel -ErrorAction SilentlyContinue) { throw 'Close LearningModel before Finish' }
$program = Join-Path $state.install_root 'LearningModel.exe'
$uninstaller = Join-Path $state.install_root 'unins000.exe'
$database = Join-Path $dataRoot 'data\learning_app.sqlite'
if (-not (Test-Path -LiteralPath $database)) { throw 'Acceptance database is missing before uninstall' }
$null = Run-SelfTest $program 'before-uninstall'
$baseline = Get-ActualDatabaseCounts $database
$beforeHash = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
Invoke-CheckedProcess $uninstaller @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
if (Test-Path -LiteralPath $program) { throw 'Uninstall left the application executable' }
if ((Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $beforeHash) { throw 'Uninstall changed the database' }
$state | Add-Member -NotePropertyName uninstall_preserved_database -NotePropertyValue $true -Force
Invoke-CheckedProcess $installer @('/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', ('/DIR="' + $state.install_root + '"'))
if ((Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -ne $beforeHash) { throw 'Reinstall changed the database before startup' }
$report = Run-SelfTest $program 'after-reinstall'
$afterCounts = Get-ActualDatabaseCounts $database
foreach ($counter in @('subjects', 'modules', 'topics', 'learning_records', 'problem_attempts')) {
    if ($baseline[$counter] -ne $afterCounts[$counter]) { throw "Record count changed after reinstall: $counter" }
}
$state | Add-Member -NotePropertyName actual_database_counts_before -NotePropertyValue $baseline -Force
$state | Add-Member -NotePropertyName actual_database_counts_after -NotePropertyValue $afterCounts -Force
$state | Add-Member -NotePropertyName reinstall_self_test -NotePropertyValue $report.status -Force
$state.status = 'automated_lifecycle_passed_manual_review_pending'
Write-JsonFile $statePath $state
Write-Output 'Lifecycle checks passed. Application and test data are retained for review. Manual checks are NOT automatically marked passed.'
