"""Assemble the local 0.1.1 acceptance delivery without publishing it."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT.parent / "final-release-0.1.1"
GIT = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    artifacts = ROOT / "release/artifacts"
    installer = artifacts / "LearningModel-Setup-0.1.1-x64.exe"
    installer_hash = digest(installer)
    build = json.loads((artifacts / "verification/build-record.json").read_text("utf-8"))
    if build["version"] != "0.1.1" or build["tests_skipped"] or build["installer_skipped"]:
        raise SystemExit("Expected a complete, tested 0.1.1 build")
    suites = ET.parse(ROOT / "build/release-tests.xml").getroot()
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in suites.iter("testsuite"))
              for key in ("tests", "failures", "errors", "skipped")}
    if any(totals[key] for key in ("failures", "errors", "skipped")):
        raise SystemExit(f"Regression gate failed: {totals}")
    previous = json.loads((ROOT / "docs/native-binary-inventory.json").read_text("utf-8"))
    runtime = ROOT / "dist/LearningModel/_internal"
    native = {p.relative_to(runtime).as_posix(): digest(p)
              for p in runtime.rglob("*") if p.suffix.lower() in {".dll", ".pyd"}}
    old_native = {item["path"]: item["sha256"] for item in previous["files"]}
    if native != old_native:
        changed = sorted(key for key in set(native) | set(old_native)
                         if native.get(key) != old_native.get(key))
        raise SystemExit(f"Native dependencies changed; review corresponding sources: {changed}")
    toc = ast.literal_eval((ROOT / "build/release-pyinstaller/LearningModel/Analysis-00.toc").read_text("utf-8"))
    inputs: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, (tuple, list)):
            for entry in value:
                walk(entry)
        elif isinstance(value, str):
            try:
                path = Path(value)
                if path.is_absolute() and path.suffix in {".py", ".pyw"}:
                    inputs.add(path.resolve().relative_to(ROOT).as_posix())
            except (ValueError, OSError):
                pass

    walk(toc)
    required = {"study_app/core/dashboard.py", "study_app/core/local_study_plan.py",
                "study_app/core/subject_catalog.py", "study_app/ai/study_plan_service.py"}
    if not required <= inputs:
        raise SystemExit(f"Updated runtime files missing from Analysis: {required - inputs}")
    verification = artifacts / "verification"
    write_json(verification / "source-inputs-0.1.1.json", {
        "installer_sha256": installer_hash,
        "base_commit": subprocess.check_output(GIT + ["rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_state": "release working tree; runtime input hashes recorded below",
        "files": [{"path": name, "sha256": digest(ROOT / name)} for name in sorted(inputs)],
    })
    write_json(verification / "native-binary-inventory-0.1.1.json", {
        "installer_sha256": installer_hash,
        "matches_0_1_0_inventory": True,
        "files": [{"path": name, "sha256": value} for name, value in sorted(native.items())],
    })
    shutil.copy2(ROOT / "build/release-tests.xml", verification)
    for name in ("安装与升级说明.md", "更新记录.md", "第三方许可说明.md"):
        shutil.copy2(ROOT / "release" / name, artifacts / name)
    shutil.copy2(installer, OUT / installer.name)
    files = subprocess.check_output(
        GIT + ["ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=ROOT
    ).decode("utf-8").strip("\0").split("\0")
    with zipfile.ZipFile(OUT / "LearningModel-0.1.1-source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(set(files)):
            path = ROOT / name
            if path.is_file():
                archive.write(path, "LearningModel-0.1.1/" + name)
    with zipfile.ZipFile(OUT / "LearningModel-0.1.1-verification.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(verification.rglob("*")):
            if path.is_file():
                archive.write(path, "verification/" + path.relative_to(verification).as_posix())
        for name in ("安装与升级说明.md", "更新记录.md", "第三方许可说明.md"):
            archive.write(artifacts / name, name)
        for path in sorted((artifacts / "THIRD_PARTY_LICENSES").rglob("*")):
            if path.is_file():
                archive.write(path, "THIRD_PARTY_LICENSES/" + path.relative_to(artifacts / "THIRD_PARTY_LICENSES").as_posix())
    kit = OUT / "LearningModel-0.1.1-Windows-QA"
    kit.mkdir(exist_ok=True)
    shutil.copy2(installer, kit / installer.name)
    shutil.copy2(ROOT / "tools/accept_release_0_1_1.ps1", kit)
    shutil.copy2(ROOT / "tools/clean_windows_acceptance.ps1", kit)
    (kit / "SHA256SUMS.txt").write_text(f"{installer_hash}  {installer.name}\n", encoding="utf-8")
    (kit / "README.txt").write_text(
        "在既有 0.1.0 验收虚拟机中关闭学习模型，然后使用 Windows PowerShell：\n"
        "先正常登录测试用户 Windows 桌面，确保用户注册表配置已加载。\n"
        "powershell -NoProfile -File .\\accept_release_0_1_1.ps1\n"
        "仅当执行策略实际阻止本可信脚本时，可为本次进程追加 -ExecutionPolicy Bypass。\n"
        "脚本会覆盖升级、静默卸载保留数据、重装并检查数据库哈希及记录计数。\n"
        "保留结果和数据库备份供复核。结果目录含测试数据，请勿公开上传。\n"
        "脚本不会自动签核真实界面或外部 AI/OCR。\n", encoding="utf-8")
    checklist = ROOT / "docs/MANUAL_ACCEPTANCE_0.1.1.md"
    (kit / "MANUAL-CHECKLIST.md").write_text(
        checklist.read_text("utf-8").replace("LearningModel 0.1.0", "LearningModel 0.1.1"), encoding="utf-8")
    for suffix in ("third-party-sources", "third-party-notices"):
        # Locked dependencies and all native components are unchanged. Preserve
        # the original archive bytes and their internal upstream version names.
        shutil.copy2(ROOT.parent / "final-release" / f"LearningModel-0.1.0-{suffix}.zip",
                     OUT / f"LearningModel-0.1.1-{suffix}.zip")
    with zipfile.ZipFile(OUT / "LearningModel-0.1.1-Windows-QA.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(kit.rglob("*")):
            if path.is_file() and path.parent == kit:
                archive.write(path, kit.name + "/" + path.name)
    delivered = sorted(path for path in OUT.iterdir() if path.suffix in {".exe", ".zip"})
    for path in delivered:
        if path.suffix == ".zip":
            with zipfile.ZipFile(path) as archive:
                invalid = archive.testzip()
                if invalid:
                    raise SystemExit(f"Corrupt delivery archive: {path.name}: {invalid}")
    with zipfile.ZipFile(OUT / "LearningModel-0.1.1-source.zip") as archive:
        for name in inputs:
            if hashlib.sha256(archive.read("LearningModel-0.1.1/" + name)).hexdigest() != digest(ROOT / name):
                raise SystemExit(f"Delivered source differs from recorded build input: {name}")
        forbidden = {".git", ".venv-build", "app_data", "__pycache__"}
        for name in archive.namelist():
            if forbidden.intersection(Path(name).parts) or Path(name).suffix in {".sqlite", ".db", ".key", ".pem"}:
                raise SystemExit(f"Unexpected private/build file in source delivery: {name}")
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{digest(path)}  {path.name}\n" for path in delivered), encoding="utf-8")
    vm_path = verification / "vm-acceptance.json"
    vm = json.loads(vm_path.read_text("utf-8")) if vm_path.is_file() else {"status": "pending"}
    write_json(OUT / "acceptance-summary.json", {
        "version": "0.1.1", "installer_sha256": installer_hash,
        "regression": totals, "packaged_python_inputs": len(inputs),
        "unchanged_native_components": len(native),
        "packaging": "passed", "vm_installation": vm["status"],
        "final_signoff": vm.get("final_signoff", False),
        "manual_ui_review": vm.get("manual_ui_review", "pending"),
        "public_release": False,
    })
    print(json.dumps({"output": str(OUT), "installer_sha256": installer_hash, "regression": totals,
                      "python_inputs": len(inputs), "native_components": len(native)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
