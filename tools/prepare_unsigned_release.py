"""Validate a frozen candidate and acceptance evidence; prepare locally, never upload."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile


class ReleaseGateError(ValueError):
    pass


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8-sig'))


def child(root: Path, relative: str) -> Path:
    path = root / relative
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ReleaseGateError(f'Unsafe relative path: {relative}')
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ReleaseGateError(f'Linked or escaping input: {relative}')
    if not path.is_file():
        raise ReleaseGateError(f'Missing input: {relative}')
    return path


def checked(root: Path, item: dict) -> Path:
    path = child(root, item['path'])
    if digest(path) != item['sha256']:
        raise ReleaseGateError(f'Input hash changed: {item["path"]}')
    return path


REQUIRED_GATES = {
    'windows_installation', 'upgrade_preservation', 'uninstall_reinstall',
    'business_workflow', 'interactive_installer', 'dpi_visual',
    'stability', 'ocr', 'failure_recovery', 'source_materials', 'privacy',
}


def validate(project: Path, build: Path, acceptance_path: Path) -> dict:
    project, build = project.resolve(), build.resolve()
    if build.parent != project / 'output':
        raise ReleaseGateError('Build must be an immediate child of project/output')
    record = read(child(build, 'metadata/run-record.json'))
    installer = read(child(build, 'installer/installer-record.json'))
    acceptance = read(acceptance_path)
    if record.get('status') != 'passed' or record.get('mode') != 'release-runtime-reproduction':
        raise ReleaseGateError('A passed release-runtime build is required')
    if installer.get('status') != 'built-and-verified':
        raise ReleaseGateError('Installer verification has not passed')
    if acceptance.get('status') != 'passed' or acceptance.get('release_mode') != 'unsigned':
        raise ReleaseGateError('Passed acceptance explicitly selecting unsigned release is required')
    if acceptance.get('uploaded') is not False:
        raise ReleaseGateError('This workflow prepares local artifacts only')
    if installer.get('signed') is not False:
        raise ReleaseGateError('This workflow is for an unsigned candidate')
    if acceptance.get('version') != record.get('version') or installer.get('version') != record.get('version'):
        raise ReleaseGateError('Versions do not match')
    if installer.get('defaultEntry') != 'qml':
        raise ReleaseGateError('Installed entry must select QML')
    for item in record['inputs'].values():
        checked(project, item)
    for key, base in (('sourceInputs', project), ('distInputs', build)):
        manifest = read(checked(build, record[key]))
        if len(manifest['files']) != record[key]['fileCount']:
            raise ReleaseGateError('Manifest file count changed')
        for item in manifest['files']:
            checked(base, item)
        if key == 'distInputs':
            actual = {p.relative_to(build).as_posix() for p in (build / record['distDirectory']).rglob('*') if p.is_file()}
            if actual != {i['path'] for i in manifest['files']}:
                raise ReleaseGateError('Distribution inventory changed')
    executable = child(build, record['executable'])
    setup = child(build, f'installer/LearningModel-Setup-{record["version"]}-x64.exe')
    for key, path, expected in (
        ('exe_sha256', executable, record['executableSha256']),
        ('installer_sha256', setup, installer['installerSha256']),
    ):
        actual = digest(path)
        if actual != expected or actual != acceptance.get(key):
            raise ReleaseGateError(f'Acceptance belongs to another binary: {key}')
    evidence_root = acceptance_path.resolve().parent
    regression = acceptance['regression']
    suites = ET.parse(checked(evidence_root, regression)).getroot()
    totals = {key: sum(int(s.get(key, 0)) for s in suites.iter('testsuite'))
              for key in ('tests', 'failures', 'errors', 'skipped')}
    if not totals['tests'] or totals['failures'] or totals['errors']:
        raise ReleaseGateError(f'Regression failed or empty: {totals}')
    skipped = {case.get('name') for case in suites.iter('testcase') if case.find('skipped') is not None}
    if skipped:
        native = ET.parse(checked(evidence_root, acceptance['native_regression'])).getroot()
        passed = {case.get('name') for case in native.iter('testcase')
                  if not any(case.find(kind) is not None for kind in ('failure', 'error', 'skipped'))}
        if not skipped <= passed:
            raise ReleaseGateError(f'Skipped tests need native passing evidence: {sorted(skipped - passed)}')
    gates = acceptance.get('gates', {})
    if REQUIRED_GATES - gates.keys():
        raise ReleaseGateError(f'Missing acceptance gates: {sorted(REQUIRED_GATES - gates.keys())}')
    for name, gate in gates.items():
        if gate.get('status') != 'passed' or not gate.get('evidence'):
            raise ReleaseGateError(f'Acceptance gate is incomplete: {name}')
        for item in gate['evidence']:
            checked(evidence_root, item)
    materials = {}
    for kind, suffix in (('source', 'source'), ('third_party_sources', 'third-party-sources'),
                         ('third_party_notices', 'third-party-notices')):
        item = acceptance.get('materials', {}).get(kind)
        if not item:
            raise ReleaseGateError(f'Missing matching distribution materials: {kind}')
        path = checked(evidence_root, item)
        if path.name != f'LearningModel-{record["version"]}-{suffix}.zip':
            raise ReleaseGateError(f'Unexpected material filename: {path.name}')
        with zipfile.ZipFile(path) as archive:
            if archive.testzip() is not None:
                raise ReleaseGateError(f'Corrupt material archive: {path.name}')
            if kind == 'source':
                source_manifest = read(checked(build, record['sourceInputs']))
                expected = source_manifest['files'] + list(record['inputs'].values())
                for row in expected:
                    data = archive.read(f'LearningModel-{record["version"]}/{row["path"]}')
                    if hashlib.sha256(data).hexdigest() != row['sha256']:
                        raise ReleaseGateError(f'Source archive differs from build: {row["path"]}')
        materials[kind] = path
    if acceptance.get('signature_status') != {'application': 'NotSigned', 'installer': 'NotSigned'}:
        raise ReleaseGateError('Actual unsigned status must be recorded for both binaries')
    return {'version': record['version'], 'installer': setup, 'regression': totals,
            'materials': materials, 'acceptance_sha256': digest(acceptance_path),
            'installer_sha256': digest(setup)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True)
    parser.add_argument('--acceptance', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    project = Path(__file__).resolve().parent.parent
    result = validate(project, args.build, args.acceptance)
    if args.preflight_only:
        print(json.dumps({'status': 'ready-for-local-preparation', **{k: v for k, v in result.items() if k not in ('installer', 'materials')}}))
        return
    if args.output is None:
        raise ReleaseGateError('A new local output directory is required')
    output = args.output.resolve()
    workspace = project
    if project.parent.name == 'release-workflow' and project.parent.parent.name == 'output':
        workspace = project.parent.parent.parent
    if not output.is_relative_to(workspace) or output.exists():
        raise ReleaseGateError('Use a new output directory inside the workspace')
    output.mkdir(parents=True)
    shutil.copy2(result['installer'], output / result['installer'].name)
    for path in result['materials'].values():
        shutil.copy2(path, output / path.name)
    for name in ('安装与升级说明.md', '更新记录.md', '第三方许可说明.md'):
        shutil.copy2(project / 'release' / name, output / name)
    shutil.copy2(project / 'LICENSE', output / 'LICENSE')
    (output / '未签名说明.txt').write_text(
        '此正式版的程序和安装包未进行代码签名。Windows 或浏览器可能显示未知发布者或 SmartScreen 提示，'
        '受管理的电脑可能禁止运行。请从项目的正式下载渠道取得安装包，并核对 SHA-256。'
        '校验和验证文件一致性，不代替发布者身份验证。本目录仅为本地准备，尚未上传发布。\n', encoding='utf-8')
    receipt = {k: v for k, v in result.items() if k not in ('installer', 'materials')}
    receipt.update(status='locally-prepared', release_mode='unsigned', signed=False,
                   uploaded=False, published=False, download_verification='pending-publication')
    (output / 'local-release-record.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    checksums = ''.join(f'{digest(path)}  {path.name}\n' for path in sorted(output.iterdir()) if path.is_file())
    (output / 'SHA256SUMS.txt').write_text(checksums, encoding='utf-8')
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
