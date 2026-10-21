from __future__ import annotations

from pathlib import Path
import pytest

from tools.release_material_policy import digest, verify_native_inputs


@pytest.fixture
def native_build(tmp_path):
    runtime = tmp_path / 'runtime'
    (runtime / 'PIL').mkdir(parents=True)
    wheel = tmp_path / 'installed-wheel.pyd'
    wheel.write_bytes(b'verified Pillow 12.3.0 native bytes')
    packaged = runtime / 'PIL/_imaging.pyd'
    packaged.write_bytes(wheel.read_bytes())
    analysis = [('PIL\\_imaging.pyd', str(wheel), 'EXTENSION')]
    records = {wheel.resolve(): {'package': 'pillow', 'version': '12.3.0', 'sha256': digest(wheel)}}
    return runtime, wheel, packaged, analysis, records


def test_upgraded_native_input_is_verified_against_actual_wheel_record(native_build):
    runtime, wheel, packaged, analysis, records = native_build
    result = verify_native_inputs(runtime, analysis, records, ())
    assert result == [{'path': 'PIL/_imaging.pyd', 'sha256': digest(wheel), 'package': 'pillow',
                       'version': '12.3.0', 'wheel_record_verified': True}]


@pytest.mark.parametrize('change', ['frozen_tampered', 'wheel_tampered', 'extra_native',
                                    'missing_native', 'unrecognized_source', 'duplicate_analysis'])
def test_native_integrity_mismatches_fail_closed(native_build, change):
    runtime, wheel, packaged, analysis, records = native_build
    if change == 'frozen_tampered': packaged.write_bytes(b'tampered')
    elif change == 'wheel_tampered':
        wheel.write_bytes(b'tampered')
        packaged.write_bytes(wheel.read_bytes())
    elif change == 'extra_native': (runtime / 'unknown.dll').write_bytes(b'unknown')
    elif change == 'missing_native': packaged.unlink()
    elif change == 'unrecognized_source': records = {}
    else: analysis.append(analysis[0])
    with pytest.raises(ValueError): verify_native_inputs(runtime, analysis, records, ())


def test_platform_input_requires_explicit_existing_root(tmp_path):
    runtime = tmp_path / 'runtime'
    platform = tmp_path / 'interpreter-DLLs'
    runtime.mkdir()
    platform.mkdir()
    source = platform / 'python.dll'
    source.write_bytes(b'CPython input')
    (runtime / 'python.dll').write_bytes(source.read_bytes())
    result = verify_native_inputs(runtime, [('python.dll', str(source), 'BINARY')], {}, (platform,))
    assert result[0]['platform_input'] == 'python.dll'
    assert result[0]['wheel_record_verified'] is False
