"""Copy license/attribution texts from hashed source archives, never execute them."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json
import re
import tarfile
import zipfile


def selected(name):
    path = PurePosixPath(name)
    return (any(p.lower() in {'licenses', 'licences'} for p in path.parts)
            or re.search(r'(?:^|[._-])(copying|license|licence|notice|copyright)(?:[._-]|$)', path.name, re.I)
            or path.name in {'qt_attribution.json', 'qt_attribution_test.json', 'qt_attributions.json'}
            or path.as_posix().endswith('/.reuse/dep5'))


def collect(archives, output):
    manifest = json.loads((archives / 'SOURCE-MANIFEST.json').read_text('utf-8'))
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for item in manifest['archives']:
        path = archives / item['filename']
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != item['sha256']:
                raise ValueError('Archive hash mismatch: ' + path.name)
        prefix = re.sub(r'\.(tar\.(gz|xz|bz2)|tgz|zip)$', '', path.name)
        archive = zipfile.ZipFile(path) if zipfile.is_zipfile(path) else tarfile.open(path)
        with archive:
            members = archive.infolist() if isinstance(archive, zipfile.ZipFile) else archive.getmembers()
            for member in members:
                name = member.filename if isinstance(archive, zipfile.ZipFile) else member.name
                relative = PurePosixPath(name)
                if relative.is_absolute() or '..' in relative.parts or ':' in name or '\\' in name:
                    raise ValueError('Unsafe archive path')
                is_file = not member.is_dir() if isinstance(archive, zipfile.ZipFile) else member.isfile()
                if not is_file or not selected(name):
                    continue
                size = member.file_size if isinstance(archive, zipfile.ZipFile) else member.size
                if size > 4 * 1024 * 1024:
                    raise ValueError('Unexpectedly large notice')
                contents = archive.read(member) if isinstance(archive, zipfile.ZipFile) else archive.extractfile(member).read()
                destination = output / prefix / Path(*relative.parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(contents)
                records.append({'archive': path.name, 'member': name, 'path': destination.relative_to(output).as_posix(), 'sha256': hashlib.sha256(contents).hexdigest()})
    (output / 'NOTICE-MANIFEST.json').write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Extracted {len(records)} notices from {len(manifest["archives"])} verified archives')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.archives, args.output)
