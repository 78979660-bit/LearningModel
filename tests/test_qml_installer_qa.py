"""Installer preparation must consume only unchanged, verified QA inputs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def installer_inputs(tmp_path):
    root = tmp_path / "项目"
    (root / "tools").mkdir(parents=True)
    script = root / "tools/build_qml_installer_qa.ps1"
    shutil.copyfile(ROOT / "tools/build_qml_installer_qa.ps1", script)
    source = root / "app.py"
    source.write_text("safe input", encoding="utf-8")
    spec = root / "packaging/LearningModel.iss"
    spec.parent.mkdir()
    spec.write_text("installer input", encoding="utf-8")
    build = root / "output/qa"
    meta = build / "metadata"
    meta.mkdir(parents=True)
    dist = build / "dist/LearningModel"
    dist.mkdir(parents=True)
    exe = dist / "LearningModel.exe"
    exe.write_bytes(b"fake verified executable; never launched")
    manifests = {}
    for name, path, base in (("source", source, root), ("dist", exe, build)):
        receipt = meta / f"{name}-inputs.json"
        receipt.write_text(json.dumps({"files": [{"path": path.relative_to(base).as_posix(), "sha256": digest(path)}]}), encoding="utf-8")
        manifests[name] = {"path": receipt.relative_to(build).as_posix(), "sha256": digest(receipt), "fileCount": 1}
    record = {
        "status": "passed", "mode": "release-runtime-reproduction", "version": "0.1.1",
        "inputs": {"installerSpec": {"path": spec.relative_to(root).as_posix(), "sha256": digest(spec)}},
        "sourceInputs": manifests["source"], "distInputs": manifests["dist"],
        "distDirectory": "dist/LearningModel", "executable": "dist/LearningModel/LearningModel.exe",
        "executableSha256": digest(exe),
    }
    (meta / "run-record.json").write_text(json.dumps(record), encoding="utf-8")
    return root, build, script, source, exe, record


def preflight(inputs, build=None):
    root, candidate, script, *_ = inputs
    pwsh = shutil.which("pwsh.exe") or shutil.which("pwsh")
    if not pwsh:
        pytest.skip("PowerShell 7 unavailable")
    return subprocess.run([pwsh, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(script),
                           "-BuildDirectory", str(build or candidate), "-PreflightOnly"],
                          cwd=root, capture_output=True, encoding="utf-8", errors="replace", timeout=20)


def test_preflight_checks_unicode_paths_without_creating_installer(installer_inputs):
    result = preflight(installer_inputs)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "ready"
    assert not (installer_inputs[1] / "installer").exists()


@pytest.mark.parametrize("change", ["source", "distribution", "extra-file", "missing-file", "installer-spec", "manifest"])
def test_changed_inputs_rejected_before_output_creation(installer_inputs, change):
    root, build, _script, source, exe, _record = installer_inputs
    if change == "source":
        source.write_text("changed", encoding="utf-8")
    elif change == "distribution":
        exe.write_bytes(b"changed")
    elif change == "extra-file":
        (exe.parent / "unreviewed.dll").write_bytes(b"extra")
    elif change == "missing-file":
        exe.unlink()
    elif change == "installer-spec":
        (root / "packaging/LearningModel.iss").write_text("changed", encoding="utf-8")
    else:
        (build / "metadata/source-inputs.json").write_text("{}", encoding="utf-8")
    result = preflight(installer_inputs)
    assert result.returncode != 0
    assert not (build / "installer").exists()


@pytest.mark.parametrize("mutation", ["failed", "internal-qa", "missing-installer-tracking", "traversal"])
def test_unverified_or_escaping_receipts_rejected(installer_inputs, mutation):
    _root, build, _script, _source, _exe, record = installer_inputs
    if mutation == "failed":
        record["status"] = "failed"
    elif mutation == "internal-qa":
        record["mode"] = "internal-qa"
    elif mutation == "missing-installer-tracking":
        del record["inputs"]["installerSpec"]
    else:
        record["inputs"]["installerSpec"]["path"] = "../outside.iss"
    (build / "metadata/run-record.json").write_text(json.dumps(record), encoding="utf-8")
    result = preflight(installer_inputs)
    assert result.returncode != 0
    assert not (build / "installer").exists()


def test_arbitrary_build_directory_rejected(installer_inputs):
    result = preflight(installer_inputs, installer_inputs[0].parent)
    assert result.returncode != 0
    assert "immediate child" in result.stderr
