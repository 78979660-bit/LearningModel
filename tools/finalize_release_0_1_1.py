"""Assemble the local 0.1.1 acceptance delivery without publishing it."""
from __future__ import annotations

import ast
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT.parent / "final-release-0.1.1"
GIT = ["git", "-c", f"safe.directory={ROOT.as_posix()}"]
SYMLINK_CASES = {('tests.test_source_notices_output_paths', name) for name in [
    *(f'test_rejects_output_symlinks_without_changing_outside_files[{mode}-{part}]'
      for mode in ['False', 'True'] for part in ['root', 'prefix', 'nested', 'leaf', 'manifest']),
    'test_rejects_linked_output_ancestor',
]}
SPECIALIST_FILES = {'symlink-admin.xml', 'symlink-admin.log', 'run-admin-symlink-once.ps1',
                    'admin-symlink-process.json', 'admin-symlink-uac-launch.json'}
SPECIALIST_INPUTS = {'tests/test_source_notices_output_paths.py', 'tools/extract_source_notices.py', 'tests/conftest.py'}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def regression_build_fields(report: Path) -> dict:
    """Persist the real pytest XML count convention without modifying the report."""
    root = ET.parse(report).getroot()
    cases = list(root.iter('testcase'))
    totals = {key: sum(int(s.get(key, 0)) for s in root.iter('testsuite'))
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if not cases or totals['tests'] < len(cases) or any(root.iter('failure')) or any(root.iter('error')):
        raise SystemExit('Regression gate failed: invalid build test evidence')
    return {'passed_subtests': totals['tests'] - len(cases), 'primary_test_cases': len(cases),
            'test_report_sha256': digest(report)}


def validate_regression_report(report: Path, expected_cases: set[tuple[str, str]] | None = None,
                               passed_subtests: int | None = None,
                               allowed_skipped: set[tuple[str, str]] | None = None) -> dict[str, int]:
    """Inspect actual cases, not just caller-controlled suite summary attributes."""
    root = ET.parse(report).getroot()
    suites = list(root.iter('testsuite'))
    cases = list(root.iter('testcase'))
    identities = [(case.get('classname', ''), case.get('name', '')) for case in cases]
    totals = {key: sum(int(s.get(key, 0)) for s in suites)
              for key in ('tests', 'failures', 'errors', 'skipped')}
    skipped = {(case.get('classname', ''), case.get('name', ''))
               for case in cases if case.find('skipped') is not None}
    # Old historical build records predate this field. Their original pytest XML
    # includes successful subtests only in the summary count; retain that format.
    if passed_subtests is None and expected_cases is None:
        passed_subtests = totals['tests'] - len(cases)
    if (not suites or not cases or len(set(identities)) != len(identities)
            or any(not classname or not name for classname, name in identities)
            or any(list(root.iter(tag)) for tag in ('failure', 'error'))
            or any(totals[key] for key in ('failures', 'errors'))
            or skipped != (allowed_skipped or set()) or totals['skipped'] != len(skipped)
            or not isinstance(passed_subtests, int) or passed_subtests < 0
            or totals['tests'] != len(cases) + passed_subtests
            or (expected_cases is not None and set(identities) != expected_cases)):
        raise SystemExit(f'Regression gate failed: incomplete or inconsistent actual test cases: {totals}')
    return totals


def verify_specialist_supplement(root: Path, material_root: Path, evidence: dict,
                                skipped: set[tuple[str, str]]) -> dict:
    """Accept only the independently reviewed, fixed eleven-case specialist run."""
    if __package__:
        from .release_material_policy import checked_file, read_pins
    else:
        from release_material_policy import checked_file, read_pins
    if (skipped != SYMLINK_CASES or evidence.get('reviewed_reuse') is not True
            or evidence.get('missing_contemporaneous_implementation_hashes') is not True
            or not evidence.get('reuse_reason')
            or set(evidence.get('files', {})) != SPECIALIST_FILES
            or set(evidence.get('source_files', {})) != SPECIALIST_INPUTS
            or evidence.get('case_ids') != sorted('::'.join(key) for key in SYMLINK_CASES)
            or evidence.get('python_version') != '.'.join(map(str, sys.version_info[:3]))
            or evidence.get('pointer_bits') != struct.calcsize('P') * 8
            or evidence.get('dependency_pins') != read_pins(root / 'requirements-build.lock.txt')):
        raise SystemExit('Regression gate failed: specialist evidence scope, review or environment mismatch')
    for name, expected in evidence['source_files'].items():
        if digest(checked_file(root, name)) != expected:
            raise SystemExit('Regression gate failed: specialist tested source changed')
    for name, expected in evidence['files'].items():
        if digest(checked_file(material_root, name)) != expected:
            raise SystemExit('Regression gate failed: original specialist artifact changed')
    process = json.loads(checked_file(material_root, 'admin-symlink-process.json').read_text('utf-8'))
    launch = json.loads(checked_file(material_root, 'admin-symlink-uac-launch.json').read_text('utf-8'))
    arguments = process.get('test_arguments', [])
    if (process.get('administrator') is not True or process.get('status') != 'completed'
            or process.get('exit_code') != 0 or launch.get('status') != 'exited' or launch.get('exit_code') != 0
            or process.get('scope') != '11 real symlink tests only'
            or launch.get('scope') != 'one administrator PowerShell for 11 symlink tests only'
            or len(arguments) != 8
            or arguments[:7] != ['-m', 'pytest', '-q', '-ra', 'tests/test_source_notices_output_paths.py',
                                 '-k', 'rejects_output_symlinks or rejects_linked_output_ancestor']
            or not arguments[7].startswith('--junitxml=')
            or not arguments[7].endswith('symlink-admin.xml')):
        raise SystemExit('Regression gate failed: specialist launch or process did not complete authorized scope')
    try:
        chronology = [datetime.fromisoformat(value) for value in [launch['requested_utc'], process['started_utc'],
                                                                 process['finished_utc'], launch['finished_utc']]]
        if (any(value.tzinfo is None for value in chronology) or chronology != sorted(chronology)
                or not isinstance(launch.get('process_id'), int) or launch['process_id'] <= 0):
            raise ValueError('Inconsistent process chronology')
    except (KeyError, TypeError, ValueError):
        raise SystemExit('Regression gate failed: specialist launch and exit chronology mismatch')
    script = checked_file(material_root, 'run-admin-symlink-once.ps1').read_text('utf-8')
    test_hash = evidence['source_files']['tests/test_source_notices_output_paths.py']
    if test_hash.upper() not in script or 'Authorized test file changed; stopped' not in script:
        raise SystemExit('Regression gate failed: specialist launch lacks the original test-file guard')
    validate_regression_report(checked_file(material_root, 'symlink-admin.xml'), SYMLINK_CASES, 0)
    return {'specialist_passed': 11, 'covered_full_run_skips': 11, 'uncovered_skips': 0,
            'full_run_original_report_preserved': True, 'specialist_original_report_preserved': True,
            'missing_contemporaneous_implementation_hashes': True, 'reuse_reason': evidence['reuse_reason']}


def preserve_specialist_originals(original_root: Path, destination: Path, hashes: dict[str, str]) -> None:
    destination.mkdir(exist_ok=True)
    for name, expected in hashes.items():
        source = original_root / name
        original = source.read_bytes()
        if hashlib.sha256(original).hexdigest() != expected:
            raise SystemExit('Regression gate failed: specialist original changed after verification')
        target = destination / name
        if target.resolve() != source.resolve():
            target.write_bytes(original)


def verify_revision_regression(root: Path, policy: Path, reviewed_commit: str, report: Path,
                               specialist_root: Path | None = None) -> dict[str, int]:
    """Require separately reviewed, commit-pinned evidence for the complete current suite.

    Missing evidence stays blocked. A mutable build record cannot authorize itself.
    """
    if __package__:
        from .release_material_policy import trusted_project_files
    else:
        from release_material_policy import trusted_project_files
    trusted = trusted_project_files(root, policy, reviewed_commit)
    evidence_name = 'release/regression-evidence.json'
    evidence_path = root / evidence_name
    if evidence_name not in trusted or not evidence_path.is_file() or digest(evidence_path) != trusted[evidence_name]:
        raise SystemExit('Regression gate failed: separately reviewed regression evidence is missing or changed')
    evidence = json.loads(evidence_path.read_text('utf-8'))
    # Excluding only this evidence file avoids a self-referential hash. All other
    # tracked code, tests, lock files and policies are bound to the tested snapshot.
    snapshot = {name: sha for name, sha in trusted.items() if name != evidence_name}
    if (evidence.get('schema') != 1 or evidence.get('source_files') != snapshot
            or evidence.get('report_sha256') != digest(report)
            or evidence.get('python_version') != '.'.join(map(str, sys.version_info[:3]))
            or evidence.get('pointer_bits') != struct.calcsize('P') * 8
            or evidence.get('command') != ['-m', 'pytest', 'tests']):
        raise SystemExit('Regression gate failed: reviewed test provenance does not match this source or report')
    result = subprocess.run([sys.executable, '-m', 'pytest', '--collect-only', '-q', 'tests'],
                            cwd=root, capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise SystemExit('Regression gate failed: current full test collection failed')
    nodeids = [line.strip() for line in result.stdout.splitlines() if line.startswith('tests/') and '::' in line]
    if not nodeids or len(nodeids) != len(set(nodeids)) or sorted(nodeids) != evidence.get('nodeids'):
        raise SystemExit('Regression gate failed: reviewed evidence does not cover the current complete test collection')
    from _pytest.junitxml import mangle_test_address
    identities = set()
    for nodeid in nodeids:
        address = mangle_test_address(nodeid)
        identities.add(('.'.join(address[:-1]), address[-1]))
    tree = ET.parse(report)
    skipped = {(case.get('classname', ''), case.get('name', ''))
               for case in tree.findall('.//testcase') if case.find('skipped') is not None}
    if skipped:
        if skipped != SYMLINK_CASES or not all('1314' in case.find('skipped').get('message', '')
                for case in tree.findall('.//testcase') if case.find('skipped') is not None):
            raise SystemExit('Regression gate failed: skips are outside the fixed WinError 1314 specialist scope')
        coverage = verify_specialist_supplement(root, specialist_root or root / 'release/artifacts/verification/regression',
                                               evidence.get('specialist', {}), skipped)
        totals = validate_regression_report(report, identities, evidence.get('passed_subtests', -1), skipped)
        totals.update(coverage)
        return totals
    return validate_regression_report(report, identities, evidence.get('passed_subtests', -1))


def main() -> None:
    global OUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['historical', 'revision'], default='historical')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--distribution-policy', type=Path, default=ROOT / 'release/distribution-policy.json')
    parser.add_argument('--reviewed-commit')
    parser.add_argument('--specialist-evidence-root', type=Path)
    parser.add_argument('--dependency-sources', type=Path, default=ROOT / 'release/artifacts/THIRD_PARTY_SOURCES')
    parser.add_argument('--dependency-notices', type=Path, default=ROOT / 'release/artifacts/THIRD_PARTY_SOURCE_NOTICES')
    options = parser.parse_args()
    if options.mode == 'revision':
        if (options.distribution_policy.absolute() != (ROOT / 'release/distribution-policy.json').absolute()
                or options.distribution_policy.resolve() != (ROOT / 'release/distribution-policy.json').resolve()):
            raise SystemExit('External distribution policy overrides are forbidden')
        if not options.reviewed_commit:
            raise SystemExit('Revision requires an explicitly reviewed source commit')
        if options.output is None or options.output.exists():
            raise SystemExit('Revision output must be a new explicitly selected directory')
        if subprocess.check_output(GIT + ['status', '--porcelain'], cwd=ROOT).strip():
            raise SystemExit('Revision source must be a clean reviewed commit')
        OUT = options.output.resolve()
    OUT.mkdir(exist_ok=True)
    artifacts = ROOT / "release/artifacts"
    installer = artifacts / "LearningModel-Setup-0.1.1-x64.exe"
    installer_hash = digest(installer)
    build = json.loads((artifacts / "verification/build-record.json").read_text("utf-8"))
    if build["version"] != "0.1.1" or build["tests_skipped"] or build["installer_skipped"]:
        raise SystemExit("Expected a complete, tested 0.1.1 build")
    if options.mode == 'revision':
        totals = verify_revision_regression(ROOT, options.distribution_policy, options.reviewed_commit,
                                           ROOT / 'build/release-tests.xml', options.specialist_evidence_root)
    else:
        if build.get('test_report_sha256') and build['test_report_sha256'] != digest(ROOT / 'build/release-tests.xml'):
            raise SystemExit('Regression gate failed: build test report hash changed')
        totals = validate_regression_report(ROOT / 'build/release-tests.xml',
                                           passed_subtests=build.get('passed_subtests'))
    previous = json.loads((ROOT / "docs/native-binary-inventory.json").read_text("utf-8"))
    runtime = ROOT / "dist/LearningModel/_internal"
    native = {p.relative_to(runtime).as_posix(): digest(p)
              for p in runtime.rglob("*") if p.suffix.lower() in {".dll", ".pyd"}}
    old_native = {item["path"]: item["sha256"] for item in previous["files"]}
    if options.mode == 'historical' and native != old_native:
        changed = sorted(key for key in set(native) | set(old_native)
                         if native.get(key) != old_native.get(key))
        raise SystemExit(f"Native dependencies changed; review corresponding sources: {changed}")
    toc = ast.literal_eval((ROOT / "build/release-pyinstaller/LearningModel/Analysis-00.toc").read_text("utf-8"))
    if options.mode == 'revision':
        if __package__:
            from .release_material_policy import installed_record_inventory, read_pins, verify_native_inputs
        else:
            from release_material_policy import installed_record_inventory, read_pins, verify_native_inputs
        if sys.platform != 'win32' or sys.version_info[:3] != (3, 14, 5) or struct.calcsize('P') != 8:
            raise SystemExit('Revision verification requires the actual Windows CPython 3.14.5 build environment')
        native_proof = verify_native_inputs(
            runtime, toc, installed_record_inventory(read_pins(ROOT / 'requirements-build.lock.txt')),
            (Path(sys.base_prefix) / 'DLLs', Path(os.environ['SystemRoot']) / 'System32'),
            {Path(sys.base_prefix) / name for name in ['python314.dll', 'python3.dll', 'vcruntime140.dll', 'vcruntime140_1.dll']},
        )
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
        "matches_0_1_0_inventory": native == old_native,
        "verification_mode": options.mode,
        "input_proof": native_proof if options.mode == 'revision' else [],
        "files": [{"path": name, "sha256": value} for name, value in sorted(native.items())],
    })
    shutil.copy2(ROOT / "build/release-tests.xml", verification)
    if options.mode == 'revision':
        record_path = ROOT / 'release/regression-evidence.json'
        record = json.loads(record_path.read_text('utf-8'))
        shutil.copy2(record_path, verification / 'regression-evidence.json')
        if totals.get('covered_full_run_skips'):
            original_root = options.specialist_evidence_root or ROOT / 'release/artifacts/verification/regression'
            saved_originals = verification / 'regression'
            preserve_specialist_originals(original_root, saved_originals, record['specialist']['files'])
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
    if options.mode == 'revision':
        if __package__:
            from .release_material_policy import evaluate_materials
        else:
            from release_material_policy import evaluate_materials
        license_root = artifacts / 'THIRD_PARTY_LICENSES'
        collected = json.loads((license_root / 'LICENSE-MANIFEST.json').read_text('utf-8'))
        assessment = evaluate_materials(
            project_root=ROOT, lock_file=ROOT / 'requirements-build.lock.txt',
            policy_path=options.distribution_policy, license_root=license_root,
            distributions=collected['distributions'], source_archives=options.dependency_sources,
            source_notices=options.dependency_notices,
            project_source=OUT / 'LearningModel-0.1.1-source.zip',
            reviewed_commit=options.reviewed_commit,
        )
        write_json(verification / 'distribution-material-assessment.json', assessment)
        if assessment['status'] != 'materials_verified':
            raise SystemExit('Revision distribution materials gate failed: ' + '; '.join(assessment['reasons']))
    with zipfile.ZipFile(OUT / "LearningModel-0.1.1-verification.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(verification.rglob("*")):
            if path.is_file():
                archive.write(path, "verification/" + path.relative_to(verification).as_posix())
        for name in ("安装与升级说明.md", "更新记录.md", "第三方许可说明.md"):
            archive.write(artifacts / name, name)
        if options.mode == 'historical':
            license_files = [{'path': path.relative_to(artifacts / 'THIRD_PARTY_LICENSES').as_posix(),
                              'sha256': digest(path)} for path in (artifacts / 'THIRD_PARTY_LICENSES').rglob('*') if path.is_file()]
        else:
            license_files = assessment['verified']['file_inventory']['licenses']
        for item in license_files:
            contents = (artifacts / 'THIRD_PARTY_LICENSES' / item['path']).read_bytes()
            if hashlib.sha256(contents).hexdigest() != item['sha256']:
                raise SystemExit('License input changed after verification')
            archive.writestr('THIRD_PARTY_LICENSES/' + item['path'], contents)
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
        destination = OUT / f"LearningModel-0.1.1-{suffix}.zip"
        if options.mode == 'historical':
            shutil.copy2(ROOT.parent / "final-release" / f"LearningModel-0.1.0-{suffix}.zip", destination)
        else:
            directory = options.dependency_sources if suffix == 'third-party-sources' else options.dependency_notices
            if __package__:
                from .release_material_policy import write_verified_zip
            else:
                from release_material_policy import write_verified_zip
            category = 'sources' if suffix == 'third-party-sources' else 'notices'
            write_verified_zip(destination, directory, assessment['verified']['file_inventory'][category], directory.name + '/')
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
        "unchanged_native_components": sum(native.get(k) == value for k, value in old_native.items()),
        "native_verification_mode": options.mode,
        "packaging": "passed", "vm_installation": vm["status"],
        "final_signoff": vm.get("final_signoff", False),
        "manual_ui_review": vm.get("manual_ui_review", "pending"),
        "public_release": False,
    })
    print(json.dumps({"output": str(OUT), "installer_sha256": installer_hash, "regression": totals,
                      "python_inputs": len(inputs), "native_components": len(native)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
