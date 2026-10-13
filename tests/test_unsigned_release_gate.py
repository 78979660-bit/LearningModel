"""Release preparation must fail closed on stale binaries, skipped checks and altered evidence."""
from pathlib import Path
import json
import zipfile
import pytest
from tools.prepare_unsigned_release import REQUIRED_GATES, ReleaseGateError, digest, validate


@pytest.fixture
def candidate(tmp_path):
    project = tmp_path / 'project'
    build = project / 'output/candidate'
    evidence = tmp_path / 'evidence'
    evidence.mkdir()

    def put(root, name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, dict):
            value = json.dumps(value)
        path.write_text(value, encoding='utf-8')
        return {'path': name, 'sha256': digest(path)}

    source = put(project, 'app.py', 'runtime')
    executable = put(build, 'dist/LearningModel/LearningModel.exe', 'synthetic-exe')
    setup = put(build, 'installer/LearningModel-Setup-0.1.2-x64.exe', 'synthetic-installer')
    record = {'status': 'passed', 'mode': 'release-runtime-reproduction', 'version': '0.1.2',
              'inputs': {'runtime': source}, 'executable': executable['path'],
              'executableSha256': executable['sha256'], 'distDirectory': 'dist/LearningModel'}
    for key, name, rows in (('sourceInputs', 'source.json', [source]), ('distInputs', 'dist.json', [executable])):
        record[key] = put(build, name, {'files': rows}) | {'fileCount': len(rows)}
    put(build, 'metadata/run-record.json', record)
    put(build, 'installer/installer-record.json', {'status': 'built-and-verified', 'version': '0.1.2',
        'signed': False, 'defaultEntry': 'qml', 'installerSha256': setup['sha256']})
    regression = put(evidence, 'regression.xml', '<testsuites><testsuite tests="1" errors="0" failures="0" skipped="0"><testcase name="works"/></testsuite></testsuites>')
    proof = put(evidence, 'proof.json', {'status': 'passed'})
    acceptance = {'status': 'passed', 'version': '0.1.2', 'release_mode': 'unsigned', 'uploaded': False,
                  'installer_sha256': setup['sha256'], 'exe_sha256': executable['sha256'],
                  'signature_status': {'application': 'NotSigned', 'installer': 'NotSigned'},
                  'regression': regression,
                  'gates': {name: {'status': 'passed', 'evidence': [proof]} for name in REQUIRED_GATES}}
    acceptance['materials'] = {}
    for key, suffix in (('source', 'source'), ('third_party_sources', 'third-party-sources'), ('third_party_notices', 'third-party-notices')):
        name = f'LearningModel-0.1.2-{suffix}.zip'
        with zipfile.ZipFile(evidence / name, 'w') as archive:
            if key == 'source': archive.writestr('LearningModel-0.1.2/app.py', 'runtime')
            else: archive.writestr('manifest.json', '{}')
        acceptance['materials'][key] = {'path': name, 'sha256': digest(evidence / name)}
    put(evidence, 'acceptance.json', acceptance)
    return project, build, evidence / 'acceptance.json', acceptance, put


def test_complete_bound_candidate_passes_without_writing(candidate):
    project, build, path, _, _ = candidate
    before = {p: digest(p) for p in project.rglob('*') if p.is_file()}
    result = validate(project, build, path)
    assert result['version'] == '0.1.2'
    assert before == {p: digest(p) for p in project.rglob('*') if p.is_file()}


@pytest.mark.parametrize('mutation', ['missing', 'changed', 'source-drift'])
def test_matching_distribution_materials_required(candidate, mutation):
    project, build, path, acceptance, put = candidate
    if mutation == 'missing': del acceptance['materials']['source']
    elif mutation == 'changed': (path.parent / acceptance['materials']['source']['path']).write_bytes(b'changed')
    else:
        item = acceptance['materials']['source']
        with zipfile.ZipFile(path.parent / item['path'], 'w') as archive:
            archive.writestr('LearningModel-0.1.2/app.py', 'another runtime')
        item['sha256'] = digest(path.parent / item['path'])
    put(path.parent, path.name, acceptance)
    with pytest.raises(ReleaseGateError): validate(project, build, path)


@pytest.mark.parametrize('mutation', ['other-installer', 'other-exe', 'pending-gate', 'missing-gate', 'uploaded', 'signed-mode', 'unsigned-unrecorded', 'wrong-version'])
def test_incomplete_or_unbound_acceptance_rejected(candidate, mutation):
    project, build, path, acceptance, put = candidate
    if mutation == 'other-installer': acceptance['installer_sha256'] = '0' * 64
    elif mutation == 'other-exe': acceptance['exe_sha256'] = '0' * 64
    elif mutation == 'pending-gate': acceptance['gates']['ocr']['status'] = 'pending'
    elif mutation == 'missing-gate': del acceptance['gates']['dpi_visual']
    elif mutation == 'uploaded': acceptance['uploaded'] = True
    elif mutation == 'signed-mode': acceptance['release_mode'] = 'signed'
    elif mutation == 'unsigned-unrecorded': del acceptance['signature_status']
    elif mutation == 'wrong-version': acceptance['version'] = '0.1.1'
    put(path.parent, path.name, acceptance)
    with pytest.raises(ReleaseGateError): validate(project, build, path)


@pytest.mark.parametrize('target', ['source', 'executable', 'proof', 'extra-file'])
def test_source_binary_and_evidence_drift_rejected(candidate, target):
    project, build, path, _, put = candidate
    root, relative = {'source': (project, 'app.py'), 'executable': (build, 'dist/LearningModel/LearningModel.exe'),
                      'proof': (path.parent, 'proof.json'), 'extra-file': (build, 'dist/LearningModel/private.db')}[target]
    put(root, relative, 'changed')
    with pytest.raises(ReleaseGateError): validate(project, build, path)


def test_skipped_regression_requires_matching_native_pass(candidate):
    project, build, path, acceptance, put = candidate
    acceptance['regression'] = put(path.parent, 'regression.xml', '<testsuites><testsuite tests="1" skipped="1"><testcase name="native-tray"><skipped/></testcase></testsuite></testsuites>')
    acceptance['native_regression'] = put(path.parent, 'native.xml', '<testsuites><testsuite tests="1" failures="1"><testcase name="native-tray"><failure/></testcase></testsuite></testsuites>')
    put(path.parent, path.name, acceptance)
    with pytest.raises(ReleaseGateError): validate(project, build, path)
    acceptance['native_regression'] = put(path.parent, 'native.xml', '<testsuites><testsuite tests="1"><testcase name="native-tray"/></testsuite></testsuites>')
    put(path.parent, path.name, acceptance)
    assert validate(project, build, path)['regression']['skipped'] == 1


def test_evidence_cannot_escape_its_root(candidate):
    project, build, path, acceptance, put = candidate
    acceptance['gates']['ocr']['evidence'][0] = {'path': '../private.json', 'sha256': '0' * 64}
    put(path.parent, path.name, acceptance)
    with pytest.raises(ReleaseGateError): validate(project, build, path)
