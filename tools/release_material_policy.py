"""Verify distribution evidence; this is not a legal compliance attestation."""
from __future__ import annotations

import base64
import hashlib
from importlib import metadata
import json
import io
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import zipfile

SOURCE_PREFIX = 'LearningModel-0.1.1/'
REQUIRED_NATIVE_SOURCES = {
    'qtbase': '6.11.1', 'qtsvg': '6.11.1', 'qtimageformats': '6.11.1',
    'PySide6 and Shiboken6': '6.11.1', 'CPython': '3.14.5', 'MuPDF': '1.27.2',
    'Mesa llvmpipe': '11.2.2', 'LLVM (Mesa renderer)': '3.6.2',
    **{'CPython external: ' + tag: tag for tag in [
        'bzip2-1.0.8', 'libffi-3.4.4', 'openssl-3.0.20', 'mpdecimal-4.0.0',
        'sqlite-3.50.4.0', 'xz-5.2.5', 'zlib-ng-2.2.4', 'zstd-1.5.7']},
}
SOURCE_ALIASES = {'pyside6-essentials': 'PySide6 and Shiboken6', 'shiboken6': 'PySide6 and Shiboken6'}


def canonical(name: str) -> str:
    return re.sub(r'[-_.]+', '-', name).casefold()


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def checked_file(root: Path, name: str) -> Path:
    relative = PurePosixPath(name.replace('\\', '/'))
    if relative.is_absolute() or not relative.parts or any(p in {'.', '..'} or ':' in p for p in relative.parts):
        raise ValueError('Unsafe material path')
    root = root.absolute()
    path = root.joinpath(*relative.parts)
    for candidate in [*root.parents, root, *list(path.parents)[:len(relative.parts) - 1], path]:
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked material path')
    if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError('Material is not a regular file within its root')
    return path


def read_pins(lock: Path) -> dict[str, str]:
    pins = {}
    for line in lock.read_text(encoding='utf-8').splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        match = re.fullmatch(r'([\w.-]+)==([^\s;]+)', line.strip())
        if not match or canonical(match[1]) in pins:
            raise ValueError('Dependency lock must contain unique exact pins')
        pins[canonical(match[1])] = match[2]
    if not pins:
        raise ValueError('Dependency lock is empty')
    return pins


def trusted_project_files(root: Path, policy_path: Path, reviewed_commit: str | None) -> dict[str, str]:
    expected_path = root / 'release/distribution-policy.json'
    if policy_path.absolute() != expected_path.absolute() or policy_path.resolve() != expected_path.resolve():
        raise ValueError('Distribution policy must be the repository policy, not an external override')
    if reviewed_commit is None or not re.fullmatch('[0-9a-f]{40}', reviewed_commit):
        raise ValueError('An explicitly reviewed source commit is required')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)
    if git('rev-parse', 'HEAD').decode().strip() != reviewed_commit:
        raise ValueError('Source HEAD differs from explicitly reviewed commit')
    with zipfile.ZipFile(io.BytesIO(git('archive', '--format=zip', reviewed_commit))) as archive:
        trusted = {name: hashlib.sha256(archive.read(name)).hexdigest()
                   for name in archive.namelist() if not name.endswith('/')}
    for name in ['release/distribution-policy.json', 'LICENSE', 'docs/OPEN_SOURCE.md', 'requirements-build.lock.txt']:
        if name not in trusted or digest(checked_file(root, name)) != trusted[name]:
            raise ValueError('Policy, license decision or dependency lock differs from reviewed commit')
    return trusted


def installed_license_inventory(pins: dict[str, str]) -> dict[str, list[str]]:
    """Read trusted installed wheel RECORDs, independent of collected material files."""
    result = {}
    for name, version in pins.items():
        distribution = metadata.distribution(name)
        if distribution.version != version:
            raise ValueError('Trusted installed license version differs from exact lock')
        hashes = []
        for item in distribution.files or []:
            parts = PurePosixPath(str(item).replace('\\', '/'))
            if ('licenses' not in {part.casefold() for part in parts.parts}
                    and not re.match(r'^(license|licence|copying|notice|copyright|authors?)([._-].*)?$', parts.name, re.I)):
                continue
            if item.hash is None or item.hash.mode != 'sha256':
                raise ValueError('Trusted installed license lacks a SHA256 wheel RECORD')
            expected = base64.urlsafe_b64decode(item.hash.value + '=' * (-len(item.hash.value) % 4)).hex()
            if digest(Path(distribution.locate_file(item))) != expected:
                raise ValueError('Trusted installed license differs from wheel RECORD')
            hashes.append(expected)
        if not hashes:
            raise ValueError('Trusted installed wheel has no original license evidence')
        result[name] = sorted(hashes)
    return result


def verified_file_inventory(root: Path, names: set[str]) -> list[dict]:
    actual = set()
    for path in root.rglob('*'):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('Linked input in material directory')
        if path.is_file(): actual.add(path.relative_to(root).as_posix())
    if actual != names:
        raise ValueError('Material directory contains missing or unlisted files')
    return [{'path': name, 'sha256': digest(checked_file(root, name))} for name in sorted(names)]


def write_verified_zip(destination: Path, root: Path, inventory: list[dict], prefix: str) -> None:
    """Write only verified files; check the exact bytes passed to the ZIP writer."""
    verified_file_inventory(root, {item['path'] for item in inventory})
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for item in inventory:
            contents = checked_file(root, item['path']).read_bytes()
            if hashlib.sha256(contents).hexdigest() != item['sha256']:
                raise ValueError('Material changed after verification')
            archive.writestr(prefix + item['path'], contents)


def evaluate_materials(*, project_root: Path, lock_file: Path, policy_path: Path | None,
                       license_root: Path, distributions: list[dict],
                       source_archives: Path | None = None, source_notices: Path | None = None,
                       project_source: Path | None = None, reviewed_commit: str | None = None) -> dict:
    report = {'status': 'blocked', 'documented_route': None,
              'legal_attestation': False, 'reasons': [], 'verified': {}}
    try:
        if policy_path is None or not policy_path.is_file():
            raise ValueError('Explicit distribution policy is missing')
        trusted = trusted_project_files(project_root, policy_path, reviewed_commit)
        if lock_file.absolute() != (project_root / 'requirements-build.lock.txt').absolute():
            raise ValueError('Dependency lock must be the reviewed repository lock')
        policy = json.loads(policy_path.read_text(encoding='utf-8'))
        if policy.get('format_version') != 1 or policy.get('project_license') != 'AGPL-3.0-only':
            raise ValueError('Unknown or unsupported distribution policy')
        if policy.get('runtime_routes', {}).get('pymupdf') != 'AGPL-3.0-only':
            raise ValueError('PyMuPDF distribution route is not explicitly documented')
        for key in ['license_file', 'documented_decision']:
            item = policy[key]
            expected_name = 'LICENSE' if key == 'license_file' else 'docs/OPEN_SOURCE.md'
            if item['path'] != expected_name:
                raise ValueError('License decision paths cannot override required repository documents')
            path = checked_file(project_root, item['path'])
            if digest(path) != item['sha256']:
                raise ValueError('Documented license decision changed; review policy again')
        license_text = checked_file(project_root, policy['license_file']['path']).read_text(encoding='utf-8')
        if 'GNU AFFERO GENERAL PUBLIC LICENSE' not in license_text:
            raise ValueError('Project license does not match documented AGPL route')
        report['documented_route'] = 'AGPL-3.0-only'
        if source_archives is None or source_notices is None or project_source is None:
            raise ValueError('Corresponding project source, dependency sources and notices are required')
        pins = read_pins(lock_file)
        actual = {canonical(d['name']): d['version'] for d in distributions}
        if len(actual) != len(distributions) or actual != pins:
            raise ValueError('License distribution inventory differs from exact dependency lock')
        trusted_licenses = installed_license_inventory(pins)
        license_names = {'LICENSE-MANIFEST.json'}
        for item in distributions:
            names = item['license_files']
            hashes = item.get('license_file_sha256', {})
            if not names or set(names) != set(hashes):
                raise ValueError('License texts lack complete collection-time hashes')
            for name in names:
                if digest(checked_file(license_root, name)) != hashes[name]:
                    raise ValueError('License text checksum mismatch')
            if sorted(hashes.values()) != trusted_licenses[canonical(item['name'])]:
                raise ValueError('Collected license texts differ from trusted installed wheel RECORD')
            license_names.update(names)
        source_manifest_path = checked_file(source_archives, 'SOURCE-MANIFEST.json')
        if digest(source_manifest_path) != policy['source_manifest_sha256']:
            raise ValueError('Source inventory differs from independently reviewed manifest')
        source_manifest = json.loads(source_manifest_path.read_text(encoding='utf-8'))
        if source_manifest.get('omissions') or not source_manifest.get('archives'):
            raise ValueError('Dependency source inventory is incomplete')
        records = {}
        archive_names = set()
        for item in source_manifest['archives']:
            name = item['filename']
            if '/' in name or '\\' in name or name in archive_names:
                raise ValueError('Duplicate or unsafe source archive filename')
            archive_names.add(name)
            key = canonical(item['component'])
            if key in records:
                raise ValueError('Duplicate dependency source component')
            if digest(checked_file(source_archives, name)) != item['sha256']:
                raise ValueError('Dependency source checksum mismatch')
            if item.get('upstream_sha256') and item['upstream_sha256'] != item['sha256']:
                raise ValueError('Dependency source differs from upstream checksum')
            records[key] = item['version']
        aliases = {canonical(k): canonical(v) for k, v in SOURCE_ALIASES.items()}
        if policy.get('source_aliases') != SOURCE_ALIASES or policy.get('additional_sources') != REQUIRED_NATIVE_SOURCES:
            raise ValueError('Policy cannot shrink mandatory native source coverage')
        required = {}
        for name, version in pins.items():
            key = aliases.get(name, name)
            if key in required and required[key] != version:
                raise ValueError('Aliased source components require conflicting versions')
            required[key] = version
        for name, version in REQUIRED_NATIVE_SOURCES.items():
            key = canonical(name)
            if key in required and required[key] != version:
                raise ValueError('Source policy conflicts with dependency lock')
            required[key] = version
        if records != required:
            raise ValueError('Source component versions differ from reviewed policy and dependency lock')
        checksums = ''.join(f"{item['sha256']}  {item['filename']}\n" for item in source_manifest['archives'])
        if checked_file(source_archives, 'SHA256SUMS.txt').read_text(encoding='utf-8') != checksums:
            raise ValueError('Source checksum list differs from verified inventory')
        notice_manifest_path = checked_file(source_notices, 'NOTICE-MANIFEST.json')
        if digest(notice_manifest_path) != policy['notice_manifest_sha256']:
            raise ValueError('Notice inventory differs from independently reviewed manifest')
        notices = json.loads(notice_manifest_path.read_text(encoding='utf-8'))
        if not notices:
            raise ValueError('No source notice evidence')
        seen_notices = set()
        for item in notices:
            if item['archive'] not in archive_names or item['path'] in seen_notices:
                raise ValueError('Notice inventory contains unknown archives or duplicate paths')
            seen_notices.add(item['path'])
            if digest(checked_file(source_notices, item['path'])) != item['sha256']:
                raise ValueError('Source notice checksum mismatch')
        if not project_source.is_file():
            raise ValueError('Corresponding project source archive is missing')
        inputs = policy['required_project_sources']
        if set(inputs) != set(trusted) or len(inputs) != len(set(inputs)):
            raise ValueError('Project source coverage is not configured')
        with zipfile.ZipFile(project_source) as archive:
            if archive.testzip() is not None or len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError('Corrupt or duplicate project source entries')
            prefix = policy['project_source_prefix']
            if prefix != SOURCE_PREFIX:
                raise ValueError('Unsafe project source prefix')
            for name in archive.namelist():
                relative = PurePosixPath(name)
                if (relative.is_absolute() or '\\' in name
                        or any(part in {'.', '..'} or ':' in part for part in relative.parts)):
                    raise ValueError('Unsafe project source archive entry')
            if {name for name in archive.namelist() if not name.endswith('/')} != {prefix + name for name in trusted}:
                raise ValueError('Project source archive does not exactly cover reviewed source commit')
            for name in inputs:
                local = checked_file(project_root, name)
                if (digest(local) != trusted[name]
                        or hashlib.sha256(archive.read(prefix + name)).hexdigest() != trusted[name]):
                    raise ValueError('Corresponding project source differs from reviewed files')
        report['status'] = 'materials_verified'
        report['verified'] = {'distributions': len(actual), 'source_archives': len(records),
                              'source_notices': len(notices), 'project_source': digest(project_source),
                              'file_inventory': {
                                  'licenses': verified_file_inventory(license_root, license_names),
                                  'sources': verified_file_inventory(source_archives, archive_names | {'SOURCE-MANIFEST.json', 'SHA256SUMS.txt'}),
                                  'notices': verified_file_inventory(source_notices, seen_notices | {'NOTICE-MANIFEST.json'}),
                              }}
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        report['status'] = 'blocked'
        report['reasons'].append(str(error))
    return report


def installed_record_inventory(pins: dict[str, str]) -> dict[Path, dict]:
    result = {}
    distributions = list(metadata.distributions())
    installed = {canonical(d.metadata['Name']): d.version for d in distributions}
    if installed != pins or len(distributions) != len(pins):
        raise ValueError('Installed build environment differs from exact lock')
    for distribution in distributions:
        name = canonical(distribution.metadata['Name'])
        for item in distribution.files or []:
            if item.suffix.lower() not in {'.dll', '.pyd'}:
                continue
            if item.hash is None or item.hash.mode != 'sha256':
                raise ValueError('Native wheel input lacks a SHA256 RECORD entry')
            expected = base64.urlsafe_b64decode(item.hash.value + '=' * (-len(item.hash.value) % 4)).hex()
            path = Path(distribution.locate_file(item)).resolve()
            if path in result:
                raise ValueError('Native wheel input belongs to multiple distributions')
            if digest(path) != expected:
                raise ValueError('Installed native input differs from wheel RECORD')
            result[path] = {'package': name, 'version': distribution.version, 'sha256': expected}
    return result


def verify_native_inputs(runtime: Path, analysis: object, wheel_records: dict[Path, dict],
                         platform_roots: tuple[Path, ...], platform_files: set[Path] | None = None) -> list[dict]:
    expected = {}
    def walk(value):
        if isinstance(value, (list, tuple)):
            if len(value) == 3 and all(isinstance(v, str) for v in value) and value[2] in {'BINARY', 'EXTENSION'}:
                name, source, kind = value
                relative = name.replace('\\', '/')
                if Path(relative).suffix.lower() in {'.dll', '.pyd'}:
                    if relative in expected:
                        raise ValueError('Duplicate native Analysis destination')
                    expected[relative] = Path(source).resolve()
            else:
                for entry in value: walk(entry)
    walk(analysis)
    actual = {p.relative_to(runtime).as_posix() for p in runtime.rglob('*') if p.suffix.lower() in {'.dll', '.pyd'}}
    if not expected or actual != set(expected):
        raise ValueError('Frozen native file set differs from PyInstaller Analysis inputs')
    inventory = []
    for name, source in sorted(expected.items()):
        packaged = checked_file(runtime, name)
        source_hash = digest(source)
        if digest(packaged) != source_hash:
            raise ValueError('Frozen native file differs from its build input')
        record = wheel_records.get(source)
        if record is not None:
            if source_hash != record['sha256']:
                raise ValueError('Native build input differs from reviewed wheel RECORD')
            origin = {'package': record['package'], 'version': record['version'], 'wheel_record_verified': True}
        elif (source in {p.resolve() for p in platform_files or set()}
              or any(source.is_relative_to(root.resolve()) for root in platform_roots)):
            origin = {'platform_input': source.name, 'wheel_record_verified': False}
        else:
            raise ValueError('Unrecognized native input outside locked wheels and interpreter/system roots: ' + name)
        inventory.append({'path': name, 'sha256': source_hash, **origin})
    return inventory
