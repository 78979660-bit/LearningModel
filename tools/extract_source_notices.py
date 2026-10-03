"""Copy license/attribution texts from hashed source archives, never execute them."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
import zipfile


def selected(name):
    path = PurePosixPath(name)
    return (any(p.lower() in {'licenses', 'licences'} for p in path.parts)
            or re.search(r'(?:^|[._-])(copying|license|licence|notice|copyright)(?:[._-]|$)', path.name, re.I)
            or path.name in {'qt_attribution.json', 'qt_attribution_test.json', 'qt_attributions.json'}
            or path.as_posix().endswith('/.reuse/dep5'))


def safe_component(name):
    """Use portable names even when validating Windows inputs on another OS."""
    if (not isinstance(name, str) or not name or name in {'.', '..'}
            or name.endswith(('.', ' '))
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)):
        return False
    # Windows also reserves device names with extensions and superscript digits.
    stem = name.split('.', 1)[0].rstrip(' ').upper()
    return not (stem in {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
                or re.fullmatch(r'(COM|LPT)[1-9\u00b9\u00b2\u00b3]', stem))


def checked_stat(path):
    """Inspect existing entries without following symlinks or Windows reparse points."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (stat.S_ISLNK(info.st_mode)
            or getattr(info, 'st_file_attributes', 0)
            & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
        raise ValueError('Unsafe output link: ' + str(path))
    return info


def check_output_parents(path):
    for parent in (*reversed(path.parents), path):
        info = checked_stat(parent)
        if info is not None and not stat.S_ISDIR(info.st_mode):
            raise ValueError('Unsafe output directory: ' + str(parent))


def checked_destination(output, relative):
    if (relative.is_absolute() or not relative.parts
            or not all(safe_component(part) for part in relative.parts)):
        raise ValueError('Unsafe output path')
    destination = output / relative
    check_output_parents(destination.parent)
    info = checked_stat(destination)
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise ValueError('Unsafe output file: ' + str(destination))
    if not destination.resolve().is_relative_to(output):
        raise ValueError('Output path escapes output directory')
    return destination


def write_output(output, relative, contents):
    """Never truncate an existing inode, including the notice manifest.

    The caller must own the output tree: cross-platform path checks cannot
    defend against another process concurrently replacing parent directories.
    """
    destination = checked_destination(output, relative)
    destination.parent.mkdir(parents=True, exist_ok=True)
    checked_destination(output, relative)
    descriptor, temporary = tempfile.mkstemp(prefix='.notice-', dir=destination.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(contents)
        checked_destination(output, relative)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def collect(archives, output):
    manifest = json.loads((archives / 'SOURCE-MANIFEST.json').read_text('utf-8'))
    output = output.absolute()
    check_output_parents(output)
    output.mkdir(parents=True, exist_ok=True)
    output = output.resolve(strict=True)
    records = []
    for item in manifest['archives']:
        filename = item['filename']
        if not safe_component(filename):
            raise ValueError('Unsafe archive filename')
        prefix = re.sub(r'\.(tar\.(gz|xz|bz2)|tgz|zip)$', '', filename)
        if not safe_component(prefix):
            raise ValueError('Unsafe archive output prefix')
        path = (archives / filename).resolve()
        if path.parent != archives.resolve():
            raise ValueError('Archive path escapes archive directory')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                raise ValueError('Archive hash mismatch: ' + path.name)
        archive = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else tarfile.open(path)
        with archive:
            members = archive.infolist() if isinstance(archive, zipfile.ZipFile) else archive.getmembers()
            for member in members:
                name = member.filename if isinstance(archive, zipfile.ZipFile) else member.name
                relative = PurePosixPath(name)
                is_file = not member.is_dir() if isinstance(archive, zipfile.ZipFile) else member.isfile()
                if name in {'.', './'} and not is_file:
                    continue
                if (relative.is_absolute() or not relative.parts
                        or not all(safe_component(part) for part in relative.parts)):
                    raise ValueError('Unsafe archive path')
                if not is_file or not selected(name):
                    continue
                size = member.file_size if isinstance(archive, zipfile.ZipFile) else member.size
                if size > 4 * 1024 * 1024:
                    raise ValueError('Unexpectedly large notice')
                contents = archive.read(member) if isinstance(archive, zipfile.ZipFile) else archive.extractfile(member).read()
                destination = write_output(output, Path(prefix, *relative.parts), contents)
                records.append({'archive': path.name, 'member': name, 'path': destination.relative_to(output).as_posix(), 'sha256': hashlib.sha256(contents).hexdigest()})
    write_output(output, Path('NOTICE-MANIFEST.json'),
                 (json.dumps(records, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))
    print(f'Extracted {len(records)} notices from {len(manifest["archives"])} verified archives')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.archives, args.output)
