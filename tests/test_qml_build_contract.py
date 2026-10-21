from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "build_qml_qa.ps1"
OUTPUT = ROOT / "output"
PYTHON = ROOT / ".venv-qml" / "Scripts" / "python.exe"


def _pwsh() -> str:
    executable = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell 7 is not installed")
    return executable


def _run(*arguments: str, script: Path = SCRIPT) -> subprocess.CompletedProcess[str]:
    command = [
        _pwsh(),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str(script),
        *arguments,
    ]
    return subprocess.run(
        command,
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=60,
        check=False,
    )


def _write_probe_python(path: Path, *, version: str, unlocked: list[str]) -> Path:
    if os.name != "nt":
        pytest.skip("The PowerShell build contract runs on Windows")
    payload = {
        "python": {
            "version": version,
            "implementation": "CPython",
            "bits": 64,
            "platform": "win32",
            "executable": r"C:\Python\python.exe",
            "baseExecutable": r"C:\Python\python.exe",
        },
        "qt": {"moduleVersion": "6.11.1", "runtimeVersion": "6.11.1", "error": None},
        "packages": {"pyinstaller": "6.22.3"},
        "pinErrors": [],
        "unlockedDistributions": unlocked,
    }
    path.write_text(
        "@echo off\r\necho " + json.dumps(payload, separators=(",", ":")) + "\r\nexit /b 0\r\n",
        encoding="ascii",
    )
    return path


def test_build_script_uses_spec_whitelist_unique_output_and_packaged_self_test():
    source = SCRIPT.read_text(encoding="utf-8")

    assert "packaging\\LearningModel.spec" in source
    assert "Join-Path $projectRoot 'output'" in source
    assert "RunName already exists" in source
    assert "--distpath" in source and "--workpath" in source
    assert "-RunSelfTest" in source
    assert "PYINSTALLER_CONFIG_DIR" in source
    assert "source-inputs.json" in source
    assert "source-inputs-after-build.json" in source
    assert "Get-SourceInputManifestDifferences" in source
    assert "dist-inputs.json" in source
    assert "installerSpec = [ordered]@{ path = 'packaging/LearningModel.iss'" in source
    assert "requires an exact locked environment" in source
    assert "packaging/version_info.txt" in source
    assert "study_app/resources/default_model.json" in source
    assert "study_app/resources/default_records.json" in source
    assert "Get-StudyAppCodeFiles" in source
    assert "learning_*.py" in source
    assert "ensure_ascii=True" in source
    assert "Remove-SafeDirectory" not in source
    assert "'--clean'" not in source


def test_source_and_distribution_manifests_are_verified_before_passed_status():
    source = SCRIPT.read_text(encoding="utf-8")

    verifier = source.index("-Description 'Verify onedir bundle and packaged self-test'")
    source_check = source.index("$postBuildSourceManifest = New-SourceInputManifest")
    dist_manifest = source.index("$distManifest = New-DistributionManifest")
    passed = source.index("$summary.status = 'passed'")
    assert verifier < dist_manifest < source_check < passed
    assert "Source inputs changed during the QA build" in source
    assert "Build inputs changed during the QA build" in source
    assert "Refusing linked packaged output" in source


def test_manifest_helpers_hash_bundle_files_and_report_source_drift(tmp_path):
    if os.name != "nt":
        pytest.skip("The PowerShell build contract runs on Windows")
    build_root = tmp_path / "run"
    dist_root = build_root / "dist" / "LearningModel"
    (dist_root / "_internal").mkdir(parents=True)
    (dist_root / "LearningModel.exe").write_bytes(b"qa-executable")
    (dist_root / "_internal" / "Qt6Core.dll").write_bytes(b"qt-runtime")

    helper_script = tmp_path / "manifest-helper-contract.ps1"
    helper_script.write_text(
        r"""
$tokens = $null
$parseErrors = $null
$sourceAst = [System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count -gt 0) { throw ($parseErrors -join '; ') }
$names = @('Get-Sha256', 'Get-SourceInputManifestDifferences', 'Add-DistributionManifestDirectory', 'New-DistributionManifest')
$functionNodes = @($sourceAst.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $names -contains $node.Name }, $true))
$functionText = ($functionNodes | ForEach-Object { $_.Extent.Text }) -join [Environment]::NewLine
$functionLoader = [ScriptBlock]::Create($functionText)
. $functionLoader
$bundle = New-DistributionManifest -Directory $args[1] -RelativeTo $args[2]
$expected = [ordered]@{ files = @(
    [ordered]@{ path = 'keep.py'; sha256 = 'same' },
    [ordered]@{ path = 'remove.py'; sha256 = 'old' },
    [ordered]@{ path = 'change.py'; sha256 = 'before' }
) }
$actual = [ordered]@{ files = @(
    [ordered]@{ path = 'keep.py'; sha256 = 'same' },
    [ordered]@{ path = 'added.qml'; sha256 = 'new' },
    [ordered]@{ path = 'change.py'; sha256 = 'after' }
) }
$drift = @(Get-SourceInputManifestDifferences -Expected $expected -Actual $actual)
[ordered]@{ bundle = $bundle; drift = $drift } | ConvertTo-Json -Depth 8 -Compress
""".strip()
        + "\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            _pwsh(),
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(helper_script),
            str(SCRIPT),
            str(dist_root),
            str(build_root),
        ],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    report = json.loads(result.stdout.strip().splitlines()[-1])
    files = {item["path"]: item["sha256"] for item in report["bundle"]["files"]}
    assert files == {
        "dist/LearningModel/LearningModel.exe": hashlib.sha256(b"qa-executable").hexdigest(),
        "dist/LearningModel/_internal/Qt6Core.dll": hashlib.sha256(b"qt-runtime").hexdigest(),
    }
    assert report["drift"] == ["added: added.qml", "changed: change.py", "removed: remove.py"]


@pytest.mark.parametrize(
    "unsafe",
    ["..", "../outside", "..\\outside", "C:\\outside", "CON", "bad:name", "trailing."],
)
def test_unsafe_run_names_fail_before_creating_output(unsafe, tmp_path):
    # This safety check owns its output directory. Parallel build/probe jobs
    # may legitimately add files to the real project output while it runs.
    isolated_root = tmp_path / "isolated-project"
    required = ("tools/build_qml_qa.ps1", "tools/verify_release.ps1",
                "packaging/LearningModel.spec", "packaging/LearningModel.iss",
                "packaging/version_info.txt", "requirements-build.lock.txt",
                "study_app/app_metadata.py")
    for relative in required:
        destination = isolated_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, destination)
    isolated_output = isolated_root / "output"
    isolated_output.mkdir()
    marker = isolated_output / "keep.txt"
    marker.write_text("preexisting output", encoding="utf-8")
    before = {path.name for path in isolated_output.iterdir()}
    result = _run("-RunName", unsafe, "-PreflightOnly", "-PythonPath", "missing-python.exe",
                  script=isolated_root / "tools/build_qml_qa.ps1")

    assert result.returncode != 0
    assert "RunName" in result.stderr + result.stdout
    assert {path.name for path in isolated_output.iterdir()} == before
    assert marker.read_text(encoding="utf-8") == "preexisting output"


def test_existing_run_name_is_rejected_and_left_untouched():
    run_name = "qa-contract-collision-" + uuid.uuid4().hex[:12]
    candidate = OUTPUT / run_name
    candidate.mkdir()
    marker = candidate / "keep.txt"
    marker.write_text("preexisting run", encoding="utf-8")
    try:
        result = _run("-RunName", run_name, "-PreflightOnly", "-PythonPath", "missing-python.exe")
        assert result.returncode != 0
        assert "already exists" in result.stderr + result.stdout
        assert marker.read_text(encoding="utf-8") == "preexisting run"
        assert sorted(path.name for path in candidate.iterdir()) == ["keep.txt"]
    finally:
        resolved_output = OUTPUT.resolve()
        resolved_candidate = candidate.resolve()
        assert resolved_candidate.parent == resolved_output
        shutil.rmtree(resolved_candidate)


def test_qa_preflight_records_real_312_qt_environment_without_creating_run():
    if not PYTHON.is_file():
        pytest.skip("project QA Python 3.12 environment is unavailable")
    run_name = "qa-contract-preflight-" + uuid.uuid4().hex[:12]
    result = _run("-PythonPath", str(PYTHON), "-RunName", run_name, "-PreflightOnly")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "preflight-passed"
    assert report["mode"] == "internal-qa"
    assert report["runtime"]["version"].startswith("3.12.")
    assert report["runtime"]["executable"] == str(PYTHON.resolve())
    assert report["qt"]["runtimeVersion"]
    assert report["inputs"]["buildScript"]["sha256"] == hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
    assert report["sourceInputCount"] > 0
    source_paths = {item["path"] for item in report["sourceInputFiles"]}
    assert "run_study_app.pyw" in source_paths
    assert "study_app/resources/default_model.json" in source_paths
    assert "study_app/resources/default_records.json" in source_paths
    assert "study_app/quick/qml/pages/PreferencesPane.qml" in source_paths
    assert "study_app/quick/qml/assets/sakura-branch.svg" in source_paths
    assert "study_app/quick/qml/assets/sakura-corner.png" in source_paths
    assert "study_app/quick/qml/assets/bamboo-leaves.svg" in source_paths
    assert "study_app/quick/qml/assets/icons/settings.svg" in source_paths
    assert "study_app/quick/qml/qmldir" in source_paths
    resource_text = (ROOT / "study_app" / "quick" / "resources.py").read_text(encoding="utf-8")
    resource_block = re.search(r"QML_RESOURCE_FILES\s*=\s*\((.*?)\)", resource_text, re.DOTALL)
    assert resource_block is not None
    expected_qml_paths = {
        "study_app/quick/qml/" + name.replace("\\", "/")
        for name in re.findall(r'"([^"\r\n]*)"', resource_block.group(1))
    }
    actual_qml_paths = {path for path in source_paths if path.startswith("study_app/quick/qml/")}
    assert actual_qml_paths == expected_qml_paths
    assert all(
        path.endswith((".qml", ".svg", ".png", ".ttf", ".txt")) or path.endswith("/qmldir")
        for path in actual_qml_paths
    )
    assert all(
        path.lower().endswith((".py", ".pyw", ".qml", ".sql", ".svg", ".png", ".ico", ".json", ".ttf", ".txt"))
        or path.lower().endswith("/qmldir")
        for path in source_paths
    )
    assert not any(
        segment in {"app_data", "backups", "logs", "cache", "output", "tests"}
        for path in source_paths
        for segment in path.lower().split("/")
    )
    assert {path for path in source_paths if path.endswith(".json")} == {
        "study_app/resources/default_model.json",
        "study_app/resources/default_records.json",
    }
    assert not (OUTPUT / run_name).exists()


def test_release_runtime_flag_rejects_qa_python_before_build_or_output_creation():
    if not PYTHON.is_file():
        pytest.skip("project QA Python 3.12 environment is unavailable")
    run_name = "qa-contract-release-gate-" + uuid.uuid4().hex[:12]
    result = _run(
        "-PythonPath",
        str(PYTHON),
        "-RunName",
        run_name,
        "-RequireReleaseRuntime",
        "-PreflightOnly",
    )

    assert result.returncode != 0
    assert "requires CPython 3.14.5 x64" in result.stderr + result.stdout
    assert not (OUTPUT / run_name).exists()


def test_release_runtime_preflight_rejects_unlocked_distributions(tmp_path):
    if not _pwsh():
        pytest.skip("PowerShell 7 is not installed")
    fake_python = _write_probe_python(
        tmp_path / "release-python-probe.cmd",
        version="3.14.5",
        unlocked=["unexpected-package"],
    )
    run_name = "qa-contract-unlocked-release-" + uuid.uuid4().hex[:12]
    result = _run(
        "-PythonPath",
        str(fake_python),
        "-RunName",
        run_name,
        "-RequireReleaseRuntime",
        "-PreflightOnly",
    )

    assert result.returncode != 0
    assert "exact locked environment" in result.stderr + result.stdout
    assert "unexpected-package" in result.stderr + result.stdout
    assert not (OUTPUT / run_name).exists()


def test_internal_qa_preflight_allows_unlocked_distributions(tmp_path):
    if not _pwsh():
        pytest.skip("PowerShell 7 is not installed")
    fake_python = _write_probe_python(
        tmp_path / "qa-python-probe.cmd",
        version="3.12.10",
        unlocked=["qa-only-tool"],
    )
    run_name = "qa-contract-unlocked-internal-" + uuid.uuid4().hex[:12]
    result = _run("-PythonPath", str(fake_python), "-RunName", run_name, "-PreflightOnly")

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "preflight-passed"
    assert report["mode"] == "internal-qa"
    assert report["unlockedDistributionCount"] == 1
    assert not (OUTPUT / run_name).exists()
