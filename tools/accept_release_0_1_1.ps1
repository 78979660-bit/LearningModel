#requires -Version 5.1
[CmdletBinding()]
param([string]$KitDirectory = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($KitDirectory)) { $KitDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path }
$kit = (Resolve-Path -LiteralPath $KitDirectory).Path
$installer = Join-Path $kit 'LearningModel-Setup-0.1.1-x64.exe'
$checksum = Get-Content -LiteralPath (Join-Path $kit 'SHA256SUMS.txt') | Where-Object { $_ -match '  LearningModel-Setup-0\.1\.1-x64\.exe$' }
if (@($checksum).Count -ne 1) { throw 'Installer checksum must appear exactly once' }
$expected = ($checksum -split '\s+')[0]
if ($expected -notmatch '^[0-9a-f]{64}$' -or (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Installer checksum mismatch' }
$results = Join-Path $kit ('results-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $results | Out-Null
$appRoot = Join-Path $env:LOCALAPPDATA 'Programs\LearningModel'
$program = Join-Path $appRoot 'LearningModel.exe'
$database = Join-Path $env:LOCALAPPDATA 'LearningModel\data\learning_app.sqlite'
if (Get-Process -Name LearningModel -ErrorAction SilentlyContinue) { throw 'Close LearningModel before acceptance' }
$userSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
if (-not (Test-Path -LiteralPath ('Registry::HKEY_USERS\' + $userSid))) {
    throw 'User registry hive is not loaded. Log in to the QA Windows desktop before running installation acceptance.'
}
$profileProbe = 'HKCU:\Software\LearningModel-QA-ProfileProbe-' + [Guid]::NewGuid().ToString('N')
try {
    New-Item -Path $profileProbe -ErrorAction Stop | Out-Null
    Remove-Item -LiteralPath $profileProbe -ErrorAction Stop
} catch { throw 'User registry profile is not writable. Installation was not attempted.' }
foreach ($name in @('LEARNINGMODEL_DATA_ROOT','LEARNINGMODEL_LEGACY_ROOT','QT_QPA_PLATFORM')) {
    if ([Environment]::GetEnvironmentVariable($name,'Process')) { throw "Remove inherited override: $name" }
}
if (-not (Test-Path -LiteralPath $database) -or -not (Test-Path -LiteralPath $program)) { throw 'This upgrade test requires an existing 0.1.0 QA installation and database' }
if ((Get-Item -LiteralPath $program).VersionInfo.ProductVersion -ne '0.1.0') { throw 'Expected installed baseline 0.1.0' }
function Invoke-QAProcess([string]$File, [string[]]$NativeArguments) {
    $process = Start-Process -FilePath $File -ArgumentList $NativeArguments -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(120000)) { throw 'QA process timeout; process retained for inspection' }
    if ($process.ExitCode -ne 0) { throw "QA process $([IO.Path]::GetFileName($File)) failed: $($process.ExitCode)" }
}
function Invoke-SelfTest([string]$Name, [string]$Version) {
    $resultPath = Join-Path $results ($Name + '.json')
    Invoke-QAProcess $program @('--self-test', ('"' + $resultPath + '"'))
    $report = Get-Content -LiteralPath $resultPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($report.status -ne 'ok' -or $report.version -ne $Version -or $report.schema_version -ne 5) { throw 'Self-test status/version/schema mismatch' }
    return $report
}
$baseline = Invoke-SelfTest 'baseline' '0.1.0'
Copy-Item -LiteralPath $database -Destination (Join-Path $results 'database-before.sqlite')
$before = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
$before | Set-Content -LiteralPath (Join-Path $results 'database-before.sha256') -Encoding ASCII
$installArgs = @('/SP-', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/TASKS=""', ('/DIR="' + $appRoot + '"'))
Invoke-QAProcess $installer ($installArgs + ('/LOG="' + (Join-Path $results 'upgrade.log') + '"'))
$upgradePreserved = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -eq $before
$upgraded = Invoke-SelfTest 'upgraded' '0.1.1'
$afterStartup = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
Invoke-QAProcess (Join-Path $appRoot 'unins000.exe') @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="' + (Join-Path $results 'uninstall.log') + '"'))
$removed = -not (Test-Path -LiteralPath $program)
$uninstallPreserved = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -eq $afterStartup
Invoke-QAProcess $installer ($installArgs + ('/LOG="' + (Join-Path $results 'reinstall.log') + '"'))
$reinstallPreserved = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash -eq $afterStartup
$reinstalled = Invoke-SelfTest 'reinstalled' '0.1.1'
$countsPreserved = $true
foreach ($counter in @('subjects','modules','topics','learning_records','problem_attempts')) {
    if ($baseline.counts.$counter -ne $upgraded.counts.$counter -or $upgraded.counts.$counter -ne $reinstalled.counts.$counter) { $countsPreserved = $false }
}
# Fresh profile self-test is separate from the existing upgrade profile.
$env:LEARNINGMODEL_DATA_ROOT = Join-Path $results 'fresh-profile'
$env:LEARNINGMODEL_LEGACY_ROOT = Join-Path $results 'empty-legacy'
New-Item -ItemType Directory -Path $env:LEARNINGMODEL_LEGACY_ROOT | Out-Null
try { $fresh = Invoke-SelfTest 'fresh' '0.1.1' } finally {
    Remove-Item -LiteralPath Env:LEARNINGMODEL_DATA_ROOT
    Remove-Item -LiteralPath Env:LEARNINGMODEL_LEGACY_ROOT
}
$freshEmpty = $fresh.counts.subjects -eq 0 -and $fresh.counts.learning_records -eq 0
$result = [ordered]@{
    version = '0.1.1'; installer_sha256 = $expected
    os = (Get-CimInstance Win32_OperatingSystem).Caption
    build = [Environment]::OSVersion.Version.ToString()
    upgrade_preserved_database = $upgradePreserved
    uninstall_removed_program = $removed
    uninstall_preserved_database = $uninstallPreserved
    reinstall_preserved_database = $reinstallPreserved
    record_counts_preserved = $countsPreserved
    fresh_profile_empty = $freshEmpty
    manual_ui_review = 'pending'; external_ai_ocr_end_to_end = 'not tested'
    passed = ($upgradePreserved -and $removed -and $uninstallPreserved -and $reinstallPreserved -and $countsPreserved -and $freshEmpty)
}
$result | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $results 'result.json') -Encoding UTF8
$result | ConvertTo-Json -Depth 8
if (-not $result.passed) { throw 'Installation lifecycle acceptance failed' }
