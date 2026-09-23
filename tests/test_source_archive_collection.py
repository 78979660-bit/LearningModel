import hashlib
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('source_collector', Path(__file__).resolve().parents[1] / 'tools' / 'collect_source_archives.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_bad_upstream_digest_is_not_published(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, 'read_url', lambda url: io.BytesIO(b'changed archive'))
    with pytest.raises(ValueError, match='checksum mismatch'):
        collector.download({'filename': 'source.tar.gz', 'url': 'https://example.invalid/source', 'upstream_sha256': '0' * 64}, tmp_path)
    assert not (tmp_path / 'source.tar.gz').exists()


def test_matching_cached_file_does_not_contact_network(tmp_path, monkeypatch):
    content = b'verified source archive'
    (tmp_path / 'source.tar.gz').write_bytes(content)
    monkeypatch.setattr(collector, 'read_url', lambda url: pytest.fail('unexpected network request'))
    digest = hashlib.sha256(content).hexdigest()
    result = collector.download({'filename': 'source.tar.gz', 'url': 'https://example.invalid/source', 'upstream_sha256': digest}, tmp_path)
    assert result['sha256'] == digest


@pytest.mark.parametrize('filename', ['../escape.tar.gz', '..\\escape.tar.gz', 'C:\\escape.tar.gz', '/escape.tar.gz'])
def test_rejects_path_traversal(tmp_path, filename):
    with pytest.raises(ValueError, match='Unsafe'):
        collector.download({'filename': filename, 'url': 'https://example.invalid/source'}, tmp_path)
