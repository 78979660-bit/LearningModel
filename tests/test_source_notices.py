import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('source_notices', Path(__file__).resolve().parents[1] / 'tools' / 'extract_source_notices.py')
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


def test_prefixed_font_license_is_selected():
    assert notices.selected('reportlab/fonts/bitstream-vera-license.txt')
    assert not notices.selected('reportlab/fonts/regular.ttf')


def make_archive(root, name):
    archive_path = root / 'sample.tar.gz'
    with tarfile.open(archive_path, 'w:gz') as archive:
        info = tarfile.TarInfo(name)
        info.size = 12
        archive.addfile(info, io.BytesIO(b'license text'))
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (root / 'SOURCE-MANIFEST.json').write_text(json.dumps({'archives': [{'filename': archive_path.name, 'sha256': digest}]}), encoding='utf-8')
    return archive_path


def test_preserves_license_bytes(tmp_path):
    make_archive(tmp_path, 'project/LICENSE')
    output = tmp_path / 'notices'
    notices.collect(tmp_path, output)
    assert (output / 'sample/project/LICENSE').read_bytes() == b'license text'


def test_refuses_unverified_archive(tmp_path):
    archive = make_archive(tmp_path, 'project/LICENSE')
    archive.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='hash mismatch'):
        notices.collect(tmp_path, tmp_path / 'notices')


def test_rejects_archive_path_escape(tmp_path):
    make_archive(tmp_path, '../LICENSE')
    with pytest.raises(ValueError, match='Unsafe'):
        notices.collect(tmp_path, tmp_path / 'notices')
    assert not (tmp_path / 'LICENSE').exists()
