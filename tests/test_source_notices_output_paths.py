"""Extraction must never overwrite files outside the requested output tree."""

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
from types import SimpleNamespace
import zipfile

import pytest


spec = importlib.util.spec_from_file_location(
    'source_notices_output_paths',
    Path(__file__).resolve().parents[1] / 'tools' / 'extract_source_notices.py',
)
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)

PAYLOAD = b'license\r\n\x00original bytes\xff\n'
SENTINEL = b'outside file: do not change\n'


def write_manifest(archives, filename, digest='0' * 64):
    (archives / 'SOURCE-MANIFEST.json').write_text(json.dumps({'archives': [
        {'filename': filename, 'sha256': digest},
    ]}), encoding='utf-8')


def make_archive(archives, filename='package.zip', member='pkg/LICENSE', kind='zip'):
    archives.mkdir(exist_ok=True)
    path = archives / filename
    if kind == 'zip':
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr(member, PAYLOAD)
    else:
        with tarfile.open(path, 'w:gz') as archive:
            root = tarfile.TarInfo('.')
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            info = tarfile.TarInfo(member)
            info.size = len(PAYLOAD)
            archive.addfile(info, io.BytesIO(PAYLOAD))
    write_manifest(archives, filename, hashlib.sha256(path.read_bytes()).hexdigest())
    return path


@pytest.mark.parametrize('extension', ['.zip', '.tar.gz', '.tar.xz', '.tar.bz2', '.tgz'])
@pytest.mark.parametrize('prefix', ['', '.', '..', '...', 'package.', 'package '])
def test_rejects_unsafe_derived_prefix_before_open(tmp_path, monkeypatch, extension, prefix):
    archives = tmp_path / 'archives'
    archives.mkdir()
    write_manifest(archives, prefix + extension)
    outside = tmp_path / 'LICENSE'
    outside.write_bytes(SENTINEL)
    original_open = Path.open

    def guarded_open(path, mode='r', *args, **kwargs):
        if mode == 'rb':
            pytest.fail('Opened archive before validating its output prefix')
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, 'open', guarded_open)
        with pytest.raises(ValueError, match='Unsafe'):
            notices.collect(archives, tmp_path / 'notices')
    assert outside.read_bytes() == SENTINEL


@pytest.mark.parametrize('kind,filename', [('zip', '...zip'), ('tar', '...tar.gz')])
def test_dotdot_prefix_cannot_overwrite_existing_outside_file(tmp_path, kind, filename):
    archives = tmp_path / 'archives'
    make_archive(archives, filename, 'LICENSE', kind)
    outside = tmp_path / 'LICENSE'
    outside.write_bytes(SENTINEL)
    with pytest.raises(ValueError, match='Unsafe'):
        notices.collect(archives, tmp_path / 'notices')
    assert outside.read_bytes() == SENTINEL


@pytest.mark.parametrize('filename', [
    'NUL.zip', 'con.tar.gz', 'AUX.txt.zip', 'COM1.zip', 'LPT9.tgz', 'COM\u00b9.zip',
    'LPT\u00b2.zip', 'CONIN$.zip', 'CONOUT$.zip', 'CON .zip', 'package.zip.',
    'package.zip ', 'bad?.zip', 'bad*.zip', 'bad|.zip', 'bad<.zip', 'bad>.zip',
    'bad".zip', 'bad\x00.zip', 'bad\x1f.zip', 'file.zip:stream',
])
def test_rejects_nonportable_manifest_names_without_open(tmp_path, filename):
    archives = tmp_path / 'archives'
    archives.mkdir()
    write_manifest(archives, filename)
    with pytest.raises(ValueError, match='Unsafe archive filename'):
        notices.collect(archives, tmp_path / 'notices')


@pytest.mark.parametrize('member', [
    '../LICENSE', '/LICENSE', 'C:/LICENSE', r'C:\LICENSE', r'\\host\share\LICENSE',
    'pkg/../LICENSE', 'pkg/.. /LICENSE', 'pkg/.../LICENSE', 'pkg./LICENSE',
    'pkg /LICENSE', 'NUL/LICENSE', 'pkg/CON.txt', 'pkg/COM\u00b9/LICENSE',
    'pkg/CONOUT$/LICENSE', 'pkg/LICENSE:stream', 'pkg/LICENSE.', 'pkg/LICENSE ',
    'pkg/LICEN\x1fSE', 'pkg/LICEN?SE', 'pkg/LICEN*SE', 'pkg/LICEN|SE',
    'pkg/LICEN<SE', 'pkg/LICEN>SE', 'pkg/LICEN"SE',
])
@pytest.mark.parametrize('kind,filename', [('zip', 'package.zip'), ('tar', 'package.tar.gz')])
def test_rejects_unsafe_member_components(tmp_path, member, kind, filename):
    archives = tmp_path / 'archives'
    make_archive(archives, filename, member, kind)
    outside = tmp_path / 'LICENSE'
    outside.write_bytes(SENTINEL)
    with pytest.raises(ValueError, match='Unsafe archive path'):
        notices.collect(archives, tmp_path / 'notices')
    assert outside.read_bytes() == SENTINEL


def make_symlink(link, target, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'Symlinks unavailable on this platform: {error}')


@pytest.mark.parametrize('location', ['root', 'prefix', 'nested', 'leaf', 'manifest'])
@pytest.mark.parametrize('broken', [False, True])
def test_rejects_output_symlinks_without_changing_outside_files(tmp_path, location, broken):
    archives = tmp_path / 'archives'
    make_archive(archives)
    output = tmp_path / 'notices'
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'LICENSE'
    sentinel.write_bytes(SENTINEL)
    destinations = {
        'root': output,
        'prefix': output / 'package',
        'nested': output / 'package/pkg',
        'leaf': output / 'package/pkg/LICENSE',
        'manifest': output / 'NOTICE-MANIFEST.json',
    }
    link = destinations[location]
    link.parent.mkdir(parents=True, exist_ok=True)
    directory = location in {'root', 'prefix', 'nested'}
    target = outside / 'missing' if broken else (outside if directory else sentinel)
    make_symlink(link, target, directory)
    with pytest.raises(ValueError, match='Unsafe output'):
        notices.collect(archives, output)
    assert sentinel.read_bytes() == SENTINEL
    assert list(outside.iterdir()) == [sentinel]
    assert link.is_symlink()


def test_rejects_linked_output_ancestor(tmp_path):
    archives = tmp_path / 'archives'
    make_archive(archives)
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'LICENSE'
    sentinel.write_bytes(SENTINEL)
    ancestor = tmp_path / 'linked-parent'
    make_symlink(ancestor, outside, directory=True)
    with pytest.raises(ValueError, match='Unsafe output'):
        notices.collect(archives, ancestor / 'notices')
    assert list(outside.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == SENTINEL


@pytest.mark.parametrize('relative', ['package/pkg/LICENSE', 'NOTICE-MANIFEST.json'])
def test_rejects_hardlinks_without_changing_outside_files(tmp_path, relative):
    archives = tmp_path / 'archives'
    make_archive(archives)
    outside = tmp_path / 'LICENSE'
    outside.write_bytes(SENTINEL)
    output = tmp_path / 'notices'
    destination = output / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(outside, destination)
    except (OSError, NotImplementedError) as error:
        pytest.skip(f'Hardlinks unavailable on this platform: {error}')
    with pytest.raises(ValueError, match='Unsafe output file'):
        notices.collect(archives, output)
    assert outside.read_bytes() == SENTINEL
    assert destination.read_bytes() == SENTINEL


@pytest.mark.parametrize('relative', ['package/pkg/LICENSE', 'NOTICE-MANIFEST.json'])
def test_rejects_nonregular_output_files(tmp_path, relative):
    archives = tmp_path / 'archives'
    make_archive(archives)
    output = tmp_path / 'notices'
    destination = output / relative
    destination.mkdir(parents=True)
    with pytest.raises(ValueError, match='Unsafe output file'):
        notices.collect(archives, output)
    assert destination.is_dir()


@pytest.mark.parametrize('kind,filename,prefix', [
    ('zip', 'package.zip', 'package'),
    ('zip', '.package.zip', '.package'),
    ('tar', 'package.tar.gz', 'package'),
    ('tar', 'package.tgz', 'package'),
])
def test_valid_archives_preserve_bytes_manifest_and_safe_reruns(tmp_path, kind, filename, prefix):
    archives = tmp_path / 'archives'
    make_archive(archives, filename, './pkg//LICENSE', kind)
    output = tmp_path / 'notices'
    for _ in range(2):
        notices.collect(archives, output)
        destination = output / prefix / 'pkg/LICENSE'
        assert destination.read_bytes() == PAYLOAD
        records = json.loads((output / 'NOTICE-MANIFEST.json').read_text('utf-8'))
        assert records == [{'archive': filename, 'member': './pkg//LICENSE',
                            'path': f'{prefix}/pkg/LICENSE',
                            'sha256': hashlib.sha256(PAYLOAD).hexdigest()}]
        assert not list(output.rglob('.notice-*'))


def test_tar_links_are_not_extracted(tmp_path):
    archives = tmp_path / 'archives'
    archives.mkdir()
    archive_path = archives / 'package.tar.gz'
    outside = tmp_path / 'LICENSE'
    outside.write_bytes(SENTINEL)
    with tarfile.open(archive_path, 'w:gz') as archive:
        for name, kind in [('pkg/LICENSE', tarfile.SYMTYPE), ('pkg/NOTICE', tarfile.LNKTYPE)]:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.linkname = str(outside)
            archive.addfile(info)
    write_manifest(archives, archive_path.name, hashlib.sha256(archive_path.read_bytes()).hexdigest())
    output = tmp_path / 'notices'
    notices.collect(archives, output)
    assert outside.read_bytes() == SENTINEL
    assert not (output / 'package').exists()
    assert json.loads((output / 'NOTICE-MANIFEST.json').read_text('utf-8')) == []


def test_reparse_attribute_is_rejected_on_every_platform(tmp_path, monkeypatch):
    archives = tmp_path / 'archives'
    make_archive(archives)
    output = tmp_path / 'notices'
    prefix = output / 'package'
    prefix.mkdir(parents=True)
    original_lstat = Path.lstat

    def reparse_stat(path, *args, **kwargs):
        info = original_lstat(path, *args, **kwargs)
        if path == prefix:
            return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT)
        return info

    monkeypatch.setattr(Path, 'lstat', reparse_stat)
    with pytest.raises(ValueError, match='Unsafe output link'):
        notices.collect(archives, output)
    assert list(prefix.iterdir()) == []


@pytest.mark.skipif(os.name != 'nt', reason='Requires native Windows junction semantics')
def test_windows_junction_cannot_redirect_notice_writes(tmp_path):
    archives = tmp_path / 'archives'
    make_archive(archives)
    output = tmp_path / 'notices'
    output.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    sentinel = outside / 'LICENSE'
    sentinel.write_bytes(SENTINEL)
    junction = output / 'package'
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(outside)],
                            capture_output=True, text=True)
    if result.returncode:
        pytest.skip('Junction creation unavailable: ' + result.stderr)
    try:
        with pytest.raises(ValueError, match='Unsafe output'):
            notices.collect(archives, output)
        assert sentinel.read_bytes() == SENTINEL
        assert list(outside.iterdir()) == [sentinel]
    finally:
        junction.rmdir()
