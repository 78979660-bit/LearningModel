from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path
import zipfile

import pytest

from tools import release_material_policy as policy
from tools import collect_licenses as collector


def write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def evidence(tmp_path, monkeypatch=None):
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'docs').mkdir()
    (root / 'LICENSE').write_text('GNU AFFERO GENERAL PUBLIC LICENSE version 3', encoding='utf-8')
    (root / 'docs/OPEN_SOURCE.md').write_text('Owner explicitly selected AGPL-3.0-only.', encoding='utf-8')
    (root / 'runtime.py').write_text('VERIFIED_RUNTIME = True\n', encoding='utf-8')
    lock = root / 'requirements-build.lock.txt'
    lock.write_text('PyMuPDF==1.27.2.3\nPillow==12.3.0\n', encoding='utf-8')
    licenses = tmp_path / 'licenses'
    sources = tmp_path / 'sources'
    notices = tmp_path / 'notices'
    for p in [licenses, sources, notices]: p.mkdir()
    distributions = []
    trusted_environment = tmp_path / 'trusted-installed-wheels'
    trusted_environment.mkdir()
    if monkeypatch is not None: monkeypatch.syspath_prepend(str(trusted_environment))
    else: sys.path.insert(0, str(trusted_environment))
    for name, version in [('PyMuPDF', '1.27.2.3'), ('Pillow', '12.3.0')]:
        file = licenses / (name + '.txt')
        file.write_text('synthetic license evidence')
        distributions.append({'name': name, 'version': version, 'license_files': [file.name],
                              'license_file_sha256': {file.name: policy.digest(file)}})
        import base64
        dist_info = trusted_environment / (name.lower() + '-' + version + '.dist-info')
        dist_info.mkdir()
        (dist_info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
        original = dist_info / 'LICENSE'
        original.write_bytes(file.read_bytes())
        value = base64.urlsafe_b64encode(hashlib.sha256(original.read_bytes()).digest()).decode().rstrip('=')
        (dist_info / 'RECORD').write_text(f'{dist_info.name}/LICENSE,sha256={value},{original.stat().st_size}\n')
    write_json(licenses / 'LICENSE-MANIFEST.json', {'distributions': distributions})
    archives = []
    components = [('PyMuPDF', '1.27.2.3'), ('Pillow', '12.3.0'), *policy.REQUIRED_NATIVE_SOURCES.items()]
    for index, (component, version) in enumerate(components):
        file = sources / f'component-{index}.tar.gz'
        file.write_bytes(component.encode())
        archives.append({'component': component, 'version': version, 'filename': file.name,
                         'sha256': policy.digest(file), 'upstream_sha256': policy.digest(file)})
    write_json(sources / 'SOURCE-MANIFEST.json', {'archives': archives, 'omissions': []})
    (sources / 'SHA256SUMS.txt').write_text(''.join(f"{item['sha256']}  {item['filename']}\n" for item in archives))
    notice = notices / 'NOTICE.txt'
    notice.write_text('synthetic source notice')
    write_json(notices / 'NOTICE-MANIFEST.json', [{'archive': archives[0]['filename'],
               'path': notice.name, 'sha256': policy.digest(notice)}])
    configuration = {
        'format_version': 1, 'project_license': 'AGPL-3.0-only',
        'runtime_routes': {'pymupdf': 'AGPL-3.0-only'},
        'license_file': {'path': 'LICENSE', 'sha256': policy.digest(root / 'LICENSE')},
        'documented_decision': {'path': 'docs/OPEN_SOURCE.md', 'sha256': policy.digest(root / 'docs/OPEN_SOURCE.md')},
        'additional_sources': policy.REQUIRED_NATIVE_SOURCES, 'source_aliases': policy.SOURCE_ALIASES,
        'required_project_sources': ['LICENSE', 'docs/OPEN_SOURCE.md', 'requirements-build.lock.txt', 'runtime.py', 'release/distribution-policy.json'],
        'project_source_prefix': policy.SOURCE_PREFIX,
        'source_manifest_sha256': policy.digest(sources / 'SOURCE-MANIFEST.json'),
        'notice_manifest_sha256': policy.digest(notices / 'NOTICE-MANIFEST.json'),
    }
    (root / 'release').mkdir()
    config_path = root / 'release/distribution-policy.json'
    write_json(config_path, configuration)
    for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=fixture', '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false', 'commit', '-qm', 'trusted fixture')]:
        subprocess.run(['git', '-C', str(root), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reviewed = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD']).decode().strip()
    archive = tmp_path / 'project.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        for name in configuration['required_project_sources']: z.write(root / name, policy.SOURCE_PREFIX + name)
    arguments = {'project_root': root, 'lock_file': lock, 'policy_path': config_path,
                 'license_root': licenses, 'distributions': distributions, 'source_archives': sources,
                 'source_notices': notices, 'project_source': archive, 'reviewed_commit': reviewed}
    return arguments, configuration


def assess(evidence):
    return policy.evaluate_materials(**evidence[0])


def test_complete_evidence_verifies_materials_without_legal_attestation(evidence):
    result = assess(evidence)
    assert result['status'] == 'materials_verified'
    assert result['documented_route'] == 'AGPL-3.0-only'
    assert result['legal_attestation'] is False
    assert result['verified']['source_archives'] == 18


@pytest.mark.parametrize('change', [
    'missing_policy', 'unknown_route', 'missing_material', 'decision_changed',
    'license_missing', 'license_tampered', 'license_hash_missing', 'distribution_version',
    'distribution_duplicate', 'source_version', 'source_tampered', 'source_manifest_rehashed',
    'notice_tampered', 'notice_manifest_rehashed', 'project_source_tampered', 'path_escape',
    'source_omission', 'upstream_mismatch', 'project_source_unsafe_entry',
])
def test_unknown_missing_or_tampered_evidence_fails_closed(evidence, change):
    arguments, config = evidence
    if change == 'missing_policy': arguments['policy_path'] = None
    elif change == 'unknown_route':
        config['project_license'] = 'unknown'
        write_json(arguments['policy_path'], config)
    elif change == 'missing_material': arguments['source_notices'] = None
    elif change == 'decision_changed': (arguments['project_root'] / 'docs/OPEN_SOURCE.md').write_text('changed')
    elif change == 'license_missing': (arguments['license_root'] / 'Pillow.txt').unlink()
    elif change == 'license_tampered': (arguments['license_root'] / 'Pillow.txt').write_text('tampered')
    elif change == 'license_hash_missing': arguments['distributions'][0]['license_file_sha256'] = {}
    elif change == 'distribution_version': arguments['distributions'][0]['version'] = 'old'
    elif change == 'distribution_duplicate': arguments['distributions'].append(copy.deepcopy(arguments['distributions'][0]))
    elif change in {'source_version', 'source_manifest_rehashed', 'source_omission', 'upstream_mismatch'}:
        path = arguments['source_archives'] / 'SOURCE-MANIFEST.json'
        manifest = json.loads(path.read_text())
        if change == 'source_version': manifest['archives'][1]['version'] = '12.1.0'
        elif change == 'source_omission': manifest['omissions'] = ['missing source']
        elif change == 'upstream_mismatch': manifest['archives'][0]['upstream_sha256'] = '0' * 64
        else: manifest['archives'][0]['sha256'] = '0' * 64
        write_json(path, manifest)
        if change != 'source_manifest_rehashed':
            config['source_manifest_sha256'] = policy.digest(path)
            write_json(arguments['policy_path'], config)
    elif change == 'source_tampered': (arguments['source_archives'] / 'component-1.tar.gz').write_bytes(b'tampered')
    elif change == 'notice_tampered': (arguments['source_notices'] / 'NOTICE.txt').write_text('tampered')
    elif change == 'notice_manifest_rehashed':
        write_json(arguments['source_notices'] / 'NOTICE-MANIFEST.json', [])
    elif change == 'project_source_tampered':
        with zipfile.ZipFile(arguments['project_source'], 'w') as z:
            z.writestr(policy.SOURCE_PREFIX + 'LICENSE', 'different license')
    elif change == 'path_escape':
        arguments['distributions'][0]['license_files'] = ['../outside']
        arguments['distributions'][0]['license_file_sha256'] = {'../outside': '0' * 64}
    elif change == 'project_source_unsafe_entry':
        with zipfile.ZipFile(arguments['project_source'], 'a') as z: z.writestr('../private', 'outside')
    assert assess(evidence)['status'] == 'blocked'
    assert assess(evidence)['reasons']


def test_missing_materials_preserves_documented_route_but_blocks_distribution(evidence):
    evidence[0]['project_source'] = None
    result = assess(evidence)
    assert result['documented_route'] == 'AGPL-3.0-only'
    assert result['status'] == 'blocked'


def test_collect_cli_required_materials_returns_nonzero_for_unknown_policy(tmp_path, monkeypatch):
    lock = tmp_path / 'lock.txt'
    lock.write_text('Pillow==12.3.0\n')
    monkeypatch.setattr(collector, 'RUNTIME_DISTRIBUTIONS', ('Pillow',))
    monkeypatch.setattr(collector, 'collect_distribution', lambda *a, **k: {'name': 'Pillow', 'version': '12.3.0'})
    assert collector.main(['--lock-file', str(lock), '--output', str(tmp_path / 'output'),
                           '--project-root', str(tmp_path), '--require-release-materials']) == 1
    report = json.loads((tmp_path / 'output/LICENSE-MANIFEST.json').read_text())
    assert report['release_blockers']
    assert report['distribution_assessment']['legal_attestation'] is False


def test_license_and_collected_hash_cannot_be_rewritten_together(evidence):
    item = evidence[0]['distributions'][0]
    path = evidence[0]['license_root'] / item['license_files'][0]
    path.write_text('REPLACED LICENSE BYTES')
    item['license_file_sha256'][path.name] = policy.digest(path)
    assert assess(evidence)['status'] == 'blocked'


@pytest.mark.parametrize('category', ['license_root', 'source_archives', 'source_notices'])
def test_unlisted_files_block_material_validation(evidence, category):
    (evidence[0][category] / 'UNREVIEWED_PRIVATE.txt').write_text('unverified')
    assert assess(evidence)['status'] == 'blocked'


def test_external_policy_cannot_shrink_native_or_project_source_coverage(evidence):
    arguments, config = evidence
    external = arguments['project_root'].parent / 'external-policy.json'
    changed = copy.deepcopy(config)
    changed['additional_sources'] = {}
    changed['required_project_sources'] = ['LICENSE']
    write_json(external, changed)
    arguments['policy_path'] = external
    assert assess(evidence)['status'] == 'blocked'


def test_modified_repository_policy_must_match_reviewed_commit(evidence):
    arguments, config = evidence
    changed = copy.deepcopy(config)
    changed['additional_sources'] = {}
    write_json(arguments['policy_path'], changed)
    assert assess(evidence)['status'] == 'blocked'


def test_zip_writer_rejects_extra_files_and_changes_after_verification(tmp_path):
    root = tmp_path / 'materials'
    root.mkdir()
    path = root / 'verified.txt'
    path.write_text('verified')
    inventory = policy.verified_file_inventory(root, {path.name})
    path.write_text('changed')
    with pytest.raises(ValueError, match='changed after verification'):
        policy.write_verified_zip(tmp_path / 'changed.zip', root, inventory, 'materials/')
    path.write_text('verified')
    (root / 'extra.txt').write_text('unverified')
    with pytest.raises(ValueError, match='unlisted'):
        policy.write_verified_zip(tmp_path / 'extra.zip', root, inventory, 'materials/')
