from __future__ import annotations

import json
from pathlib import Path
import pytest

from tools import finalize_release_0_1_1 as finalizer


def build_fixture(tmp_path, monkeypatch, skipped=0, native_changed=False):
    root = tmp_path / 'source'
    artifacts = root / 'release/artifacts'
    (artifacts / 'verification').mkdir(parents=True)
    (root / 'build').mkdir()
    (root / 'docs').mkdir()
    (root / 'dist/LearningModel/_internal').mkdir(parents=True)
    (artifacts / 'LearningModel-Setup-0.1.1-x64.exe').write_bytes(b'installer fixture')
    (artifacts / 'verification/build-record.json').write_text(json.dumps({
        'version': '0.1.1', 'tests_skipped': False, 'installer_skipped': False}))
    (root / 'build/release-tests.xml').write_text(
        f'<testsuites><testsuite tests="1" failures="0" errors="0" skipped="{skipped}"><testcase classname="tests.test_x" name="test_one"/></testsuite></testsuites>')
    if native_changed:
        (root / 'dist/LearningModel/_internal/test.dll').write_bytes(b'new native bytes')
        old = [{'path': 'test.dll', 'sha256': '0' * 64}]
    else:
        old = []
    (root / 'docs/native-binary-inventory.json').write_text(json.dumps({'files': old}))
    monkeypatch.setattr(finalizer, 'ROOT', root)
    monkeypatch.setattr(finalizer, 'OUT', tmp_path / 'historical-output')
    monkeypatch.setattr(finalizer.subprocess, 'check_output', lambda *a, **k: b'')
    return root


@pytest.mark.parametrize('mode', ['historical', 'revision'])
def test_zero_skip_gate_remains_mandatory_in_both_modes(tmp_path, monkeypatch, mode):
    build_fixture(tmp_path, monkeypatch, skipped=11)
    arguments = ['finalizer', '--mode', mode]
    if mode == 'revision':
        arguments += ['--output', str(tmp_path / 'revision-output'), '--reviewed-commit', 'a' * 40]
        monkeypatch.setattr(finalizer, 'verify_revision_regression', lambda root, policy, commit, report, *args: finalizer.validate_regression_report(report))
    monkeypatch.setattr(finalizer.sys, 'argv', arguments)
    with pytest.raises(SystemExit, match='Regression gate failed'):
        finalizer.main()


def test_historical_native_baseline_is_not_relaxed(tmp_path, monkeypatch):
    build_fixture(tmp_path, monkeypatch, native_changed=True)
    monkeypatch.setattr(finalizer.sys, 'argv', ['finalizer', '--mode', 'historical'])
    with pytest.raises(SystemExit, match='Native dependencies changed'):
        finalizer.main()


def test_revision_requires_clean_reviewed_source(tmp_path, monkeypatch):
    build_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(finalizer.subprocess, 'check_output', lambda *a, **k: b'M source.py')
    monkeypatch.setattr(finalizer.sys, 'argv', ['finalizer', '--mode', 'revision', '--output', str(tmp_path / 'new'), '--reviewed-commit', 'a' * 40])
    with pytest.raises(SystemExit, match='clean reviewed commit'):
        finalizer.main()


@pytest.mark.parametrize('contents', [
    '<testsuites/>',
    '<testsuites><testsuite tests="100" failures="0" errors="0" skipped="0"/></testsuites>',
    '<testsuites><testsuite tests="1"><testcase classname="tests.test_x" name="test_one"><failure/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite tests="1"><testcase classname="tests.test_x" name="test_one"><error/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite tests="1"><testcase classname="tests.test_x" name="test_one"><skipped/></testcase></testsuite></testsuites>',
    '<testsuites><testsuite tests="1"><testcase classname="tests.test_x" name="test_one"/></testsuite></testsuites>',
    '<testsuites><testsuite tests="2"><testcase classname="tests.test_x" name="test_one"/><testcase classname="tests.test_x" name="test_one"/></testsuite></testsuites>',
])
def test_empty_partial_duplicate_or_hidden_failed_cases_are_rejected(tmp_path, contents):
    report = tmp_path / 'report.xml'
    report.write_text(contents)
    with pytest.raises(SystemExit, match='Regression gate failed'):
        finalizer.validate_regression_report(report, {('tests.test_x', 'test_one'), ('tests.test_x', 'test_two')})


def regression_evidence(tmp_path, monkeypatch):
    from tools import release_material_policy
    root = tmp_path / 'repo'
    (root / 'release').mkdir(parents=True)
    report = root / 'report.xml'
    report.write_text('<testsuites><testsuite tests="2"><testcase classname="tests.test_x" name="test_one"/><testcase classname="tests.test_x" name="test_two"/></testsuite></testsuites>')
    snapshot = {'tests/test_x.py': 'b' * 64, 'release/distribution-policy.json': 'c' * 64}
    evidence = {'schema': 1, 'source_files': snapshot, 'report_sha256': finalizer.digest(report),
                'python_version': '.'.join(map(str, finalizer.sys.version_info[:3])),
                'pointer_bits': finalizer.struct.calcsize('P') * 8,
                'command': ['-m', 'pytest', 'tests'], 'passed_subtests': 0,
                'nodeids': ['tests/test_x.py::test_one', 'tests/test_x.py::test_two']}
    path = root / 'release/regression-evidence.json'
    path.write_text(json.dumps(evidence))
    trusted = {**snapshot, 'release/regression-evidence.json': finalizer.digest(path)}
    monkeypatch.setattr(release_material_policy, 'trusted_project_files', lambda *args: trusted)
    from types import SimpleNamespace
    monkeypatch.setattr(finalizer.subprocess, 'run', lambda *args, **kw: SimpleNamespace(
        returncode=0, stdout='tests/test_x.py::test_one\ntests/test_x.py::test_two\n\n2 tests collected'))
    return root, report, path, evidence, trusted


def test_reviewed_complete_report_passes(tmp_path, monkeypatch):
    root, report, *_ = regression_evidence(tmp_path, monkeypatch)
    assert finalizer.verify_revision_regression(root, root / 'release/distribution-policy.json', 'a' * 40, report)['tests'] == 2


@pytest.mark.parametrize('attack', ['missing', 'unreviewed_rehash', 'source_changed', 'report_changed', 'python_changed', 'partial_collection', 'summary_lies'])
def test_revision_test_evidence_cannot_authorize_itself(tmp_path, monkeypatch, attack):
    root, report, path, evidence, trusted = regression_evidence(tmp_path, monkeypatch)
    if attack == 'missing': path.unlink()
    elif attack == 'report_changed': report.write_text('<testsuites/>')
    else:
        if attack in {'source_changed', 'unreviewed_rehash'}: evidence['source_files'] = {'tests/test_x.py': 'd' * 64}
        elif attack == 'python_changed': evidence['python_version'] = '0.0.0'
        elif attack == 'partial_collection': evidence['nodeids'] = ['tests/test_x.py::test_one']
        elif attack == 'summary_lies':
            report.write_text('<testsuites><testsuite tests="2"><testcase classname="tests.test_x" name="test_one"><failure/></testcase><testcase classname="tests.test_x" name="test_two"/></testsuite></testsuites>')
            evidence['report_sha256'] = finalizer.digest(report)
        path.write_text(json.dumps(evidence))
        if attack != 'unreviewed_rehash': trusted['release/regression-evidence.json'] = finalizer.digest(path)
    with pytest.raises(SystemExit, match='Regression gate failed'):
        finalizer.verify_revision_regression(root, root / 'release/distribution-policy.json', 'a' * 40, report)


@pytest.mark.parametrize('legacy', [True, False])
def test_historical_build_record_roundtrip_preserves_real_subtest_counts(tmp_path, monkeypatch, legacy):
    root = build_fixture(tmp_path, monkeypatch, native_changed=True)
    report = root / 'build/release-tests.xml'
    report.write_text('<testsuites><testsuite tests="1420" failures="0" errors="0" skipped="0">' +
                      ''.join(f'<testcase classname="tests.test_history" name="test_{i}"/>' for i in range(1141)) +
                      '</testsuite></testsuites>')
    original = report.read_bytes()
    record_path = root / 'release/artifacts/verification/build-record.json'
    record = json.loads(record_path.read_text())
    if not legacy:
        # Use exactly the producer invoked by build_release.ps1, then serialize
        # and consume its fields through the real historical finalizer path.
        fields = finalizer.regression_build_fields(report)
        assert fields['passed_subtests'] == 279 and fields['primary_test_cases'] == 1141
        record.update(fields)
        record_path.write_text(json.dumps(record))
    monkeypatch.setattr(finalizer.sys, 'argv', ['finalizer', '--mode', 'historical'])
    with pytest.raises(SystemExit, match='Native dependencies changed'):
        finalizer.main()
    assert report.read_bytes() == original


def test_historical_build_record_rejects_modified_report(tmp_path, monkeypatch):
    root = build_fixture(tmp_path, monkeypatch)
    path = root / 'release/artifacts/verification/build-record.json'
    record = json.loads(path.read_text())
    record.update(finalizer.regression_build_fields(root / 'build/release-tests.xml'))
    path.write_text(json.dumps(record))
    with (root / 'build/release-tests.xml').open('a') as stream: stream.write(' ')
    monkeypatch.setattr(finalizer.sys, 'argv', ['finalizer', '--mode', 'historical'])
    with pytest.raises(SystemExit, match='report hash changed'):
        finalizer.main()


def specialist_fixture(tmp_path):
    from tools.release_material_policy import read_pins
    root = tmp_path / 'repo'
    materials = tmp_path / 'original-specialist'
    materials.mkdir(parents=True)
    for name in finalizer.SPECIALIST_INPUTS:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('unchanged tested input')
    (root / 'requirements-build.lock.txt').write_text('Pillow==12.3.0\n')
    cases = ''.join(f'<testcase classname="{classname}" name="{name}"/>' for classname, name in sorted(finalizer.SYMLINK_CASES))
    (materials / 'symlink-admin.xml').write_text('<testsuites><testsuite tests="11" failures="0" errors="0" skipped="0">' + cases + '</testsuite></testsuites>')
    (materials / 'symlink-admin.log').write_text('Original process log: 11 passed')
    test_hash = finalizer.digest(root / 'tests/test_source_notices_output_paths.py')
    (materials / 'run-admin-symlink-once.ps1').write_text(test_hash.upper() + '\nAuthorized test file changed; stopped')
    process = {'administrator': True, 'status': 'completed', 'exit_code': 0, 'scope': '11 real symlink tests only',
               'started_utc': '2026-10-03T13:20:20Z', 'finished_utc': '2026-10-03T13:20:23Z',
               'test_arguments': ['-m', 'pytest', '-q', '-ra', 'tests/test_source_notices_output_paths.py', '-k',
                                  'rejects_output_symlinks or rejects_linked_output_ancestor', '--junitxml=C:/original/symlink-admin.xml']}
    launch = {'status': 'exited', 'exit_code': 0, 'scope': 'one administrator PowerShell for 11 symlink tests only',
              'process_id': 68224, 'requested_utc': '2026-10-03T13:20:19Z', 'finished_utc': '2026-10-03T13:20:24Z'}
    (materials / 'admin-symlink-process.json').write_text(json.dumps(process))
    (materials / 'admin-symlink-uac-launch.json').write_text(json.dumps(launch))
    evidence = {'reviewed_reuse': True, 'missing_contemporaneous_implementation_hashes': True,
                'reuse_reason': 'Independent review accepted unchanged implementation and conftest; original launch guarded test hash.',
                'files': {name: finalizer.digest(materials / name) for name in finalizer.SPECIALIST_FILES},
                'source_files': {name: finalizer.digest(root / name) for name in finalizer.SPECIALIST_INPUTS},
                'case_ids': sorted('::'.join(key) for key in finalizer.SYMLINK_CASES),
                'python_version': '.'.join(map(str, finalizer.sys.version_info[:3])),
                'pointer_bits': finalizer.struct.calcsize('P') * 8, 'dependency_pins': read_pins(root / 'requirements-build.lock.txt')}
    return root, materials, evidence


def test_fixed_specialist_run_covers_exactly_eleven_skips(tmp_path):
    root, materials, evidence = specialist_fixture(tmp_path)
    original = (materials / 'symlink-admin.xml').read_bytes()
    result = finalizer.verify_specialist_supplement(root, materials, evidence, finalizer.SYMLINK_CASES)
    assert result['covered_full_run_skips'] == 11 and result['uncovered_skips'] == 0
    assert (materials / 'symlink-admin.xml').read_bytes() == original


@pytest.mark.parametrize('attack', ['subset', 'other_skip', 'duplicate', 'extra_case', 'actual_failure',
                                  'actual_skip', 'rehashed_log', 'changed_input', 'changed_pins',
                                  'process_failure', 'launch_failure', 'expanded_arguments', 'no_guard',
                                  'no_review', 'missing_hash_limit_concealed', 'bad_chronology'])
def test_specialist_evidence_rejects_invalid_scope_artifacts_or_provenance(tmp_path, attack):
    root, materials, evidence = specialist_fixture(tmp_path)
    skipped = set(finalizer.SYMLINK_CASES)
    xml = materials / 'symlink-admin.xml'
    if attack == 'subset': skipped.pop()
    elif attack == 'other_skip': skipped.add(('tests.other', 'test_other'))
    elif attack in {'duplicate', 'extra_case', 'actual_failure', 'actual_skip'}:
        import xml.etree.ElementTree as ET
        tree = ET.parse(xml)
        suite = tree.find('.//testsuite')
        if attack == 'duplicate':
            import copy
            suite.append(copy.deepcopy(suite[0]))
            suite.set('tests', '12')
        elif attack == 'extra_case':
            ET.SubElement(suite, 'testcase', {'classname': 'tests.other', 'name': 'test_other'})
            suite.set('tests', '12')
        else: ET.SubElement(suite[0], 'failure' if attack == 'actual_failure' else 'skipped')
        tree.write(xml)
        # Even a separately reviewed artifact hash cannot waive case integrity.
        evidence['files'][xml.name] = finalizer.digest(xml)
    elif attack == 'rehashed_log': (materials / 'symlink-admin.log').write_text('replacement log')
    elif attack == 'changed_input': (root / 'tools/extract_source_notices.py').write_text('changed')
    elif attack == 'changed_pins': evidence['dependency_pins']['pillow'] = '0.0.0'
    elif attack in {'process_failure', 'expanded_arguments', 'launch_failure', 'bad_chronology'}:
        path = materials / ('admin-symlink-uac-launch.json' if attack == 'launch_failure' else 'admin-symlink-process.json')
        value = json.loads(path.read_text())
        if attack == 'expanded_arguments': value['test_arguments'][4] = 'tests'
        elif attack == 'bad_chronology': value['started_utc'] = '2026-10-03T13:30:00Z'
        else: value['exit_code'] = 1
        path.write_text(json.dumps(value))
        evidence['files'][path.name] = finalizer.digest(path)
    elif attack == 'no_guard':
        path = materials / 'run-admin-symlink-once.ps1'
        path.write_text('unguarded')
        evidence['files'][path.name] = finalizer.digest(path)
    elif attack == 'no_review': evidence['reviewed_reuse'] = False
    elif attack == 'missing_hash_limit_concealed': evidence['missing_contemporaneous_implementation_hashes'] = False
    with pytest.raises(SystemExit, match='Regression gate failed'):
        finalizer.verify_specialist_supplement(root, materials, evidence, skipped)


@pytest.mark.parametrize('attack', [None, 'wrong_reason', 'other_skip', 'duplicate_full_case', 'full_hidden_failure'])
def test_revision_merges_actual_full_and_specialist_sets_without_rewriting_xml(tmp_path, monkeypatch, attack):
    from types import SimpleNamespace
    root, report, evidence_path, evidence, trusted = regression_evidence(tmp_path, monkeypatch)
    specialist_root, materials, specialist = specialist_fixture(tmp_path / 'specialist')
    for name in [*finalizer.SPECIALIST_INPUTS, 'requirements-build.lock.txt']:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((specialist_root / name).read_bytes())
        trusted[name] = finalizer.digest(path)
    evidence['source_files'] = {name: sha for name, sha in trusted.items() if name != 'release/regression-evidence.json'}
    nodes = ['tests/test_source_notices_output_paths.py::' + name for _, name in sorted(finalizer.SYMLINK_CASES)]
    evidence['nodeids'] = sorted([*evidence['nodeids'], *nodes])
    evidence['specialist'] = specialist
    import xml.etree.ElementTree as ET
    tree = ET.parse(report)
    suite = tree.find('.//testsuite')
    suite.set('tests', '13')
    suite.set('skipped', '11')
    for classname, name in sorted(finalizer.SYMLINK_CASES):
        case = ET.SubElement(suite, 'testcase', {'classname': classname, 'name': name})
        ET.SubElement(case, 'skipped', {'message': 'Symlinks unavailable: [WinError 1314]'})
    if attack == 'wrong_reason': suite[-1].find('skipped').set('message', 'unrelated platform skip')
    elif attack == 'other_skip': ET.SubElement(suite[0], 'skipped', {'message': '[WinError 1314]'})
    elif attack == 'duplicate_full_case':
        import copy
        suite.append(copy.deepcopy(suite[-1]))
    elif attack == 'full_hidden_failure': ET.SubElement(suite[0], 'failure')
    tree.write(report)
    original = report.read_bytes()
    evidence['report_sha256'] = finalizer.digest(report)
    evidence_path.write_text(json.dumps(evidence))
    trusted['release/regression-evidence.json'] = finalizer.digest(evidence_path)
    monkeypatch.setattr(finalizer.subprocess, 'run', lambda *args, **kw: SimpleNamespace(
        returncode=0, stdout='\n'.join(evidence['nodeids'])))
    if attack:
        with pytest.raises(SystemExit, match='Regression gate failed'):
            finalizer.verify_revision_regression(root, root / 'release/distribution-policy.json', 'a' * 40, report, materials)
    else:
        result = finalizer.verify_revision_regression(root, root / 'release/distribution-policy.json', 'a' * 40, report, materials)
        assert result['skipped'] == 11 and result['specialist_passed'] == 11 and result['uncovered_skips'] == 0
    assert report.read_bytes() == original


def test_preserving_original_specialist_files_is_byte_exact_and_does_not_rewrite_source(tmp_path):
    _, materials, evidence = specialist_fixture(tmp_path)
    before = {name: ((materials / name).read_bytes(), (materials / name).stat().st_mtime_ns) for name in finalizer.SPECIALIST_FILES}
    saved = tmp_path / 'verification'
    finalizer.preserve_specialist_originals(materials, saved, evidence['files'])
    for name, (data, timestamp) in before.items():
        assert (saved / name).read_bytes() == data
        assert (materials / name).read_bytes() == data and (materials / name).stat().st_mtime_ns == timestamp
    finalizer.preserve_specialist_originals(materials, materials, evidence['files'])
    assert all((materials / name).stat().st_mtime_ns == timestamp for name, (_, timestamp) in before.items())
    (materials / 'symlink-admin.xml').write_text('changed after verification')
    with pytest.raises(SystemExit, match='changed after verification'):
        finalizer.preserve_specialist_originals(materials, saved, evidence['files'])
