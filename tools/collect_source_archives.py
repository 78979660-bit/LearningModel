"""Download upstream source archives without executing their build code.

PyPI source archives must match the digest from the version API. Qt/Python
archives come from their official HTTPS release servers; recorded local hashes
allow subsequent integrity checks. This inventory is not a legal attestation.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET


def read_url(url):
    request = urllib.request.Request(url, headers={"User-Agent": "LearningModel-source-collector/1.0"})
    return urllib.request.urlopen(request, timeout=90)


def download(item, output):
    filename = item['filename']
    if Path(filename).name != filename or '/' in filename or '\\' in filename or ':' in filename or filename in {'.', '..'}:
        raise ValueError('Unsafe archive filename')
    if item['url'].startswith('https://download.qt.io/'):
        with read_url(item['url'] + '.meta4') as response:
            metadata = ET.fromstring(response.read())
        digest = metadata.find('.//{urn:ietf:params:xml:ns:metalink}hash[@type="sha-256"]')
        if digest is None:
            raise ValueError('Qt upstream SHA-256 missing')
        item = {**item, 'upstream_sha256': digest.text.strip()}
    destination = output / item['filename']
    expected = item.get('upstream_sha256')
    if destination.exists() and expected and hashlib.file_digest(destination.open('rb'), 'sha256').hexdigest() == expected:
        pass
    elif destination.exists() and not expected:
        # Reuse only a prior manifest-verified download, never a partial file.
        old_manifest = output / 'SOURCE-MANIFEST.json'
        previous = json.loads(old_manifest.read_text('utf-8')) if old_manifest.exists() else {}
        found = next((x for x in previous.get('archives', []) if x['filename'] == item['filename'] and x['url'] == item['url']), None)
        if not found or hashlib.file_digest(destination.open('rb'), 'sha256').hexdigest() != found['sha256']:
            raise ValueError('Existing source archive has no verified manifest: ' + destination.name)
    else:
        partial = destination.with_name(destination.name + '.partial')
        with read_url(item['url']) as response, partial.open('wb') as target:
            while chunk := response.read(1024 * 1024):
                target.write(chunk)
        digest = hashlib.file_digest(partial.open('rb'), 'sha256').hexdigest()
        if expected and digest != expected:
            raise ValueError('Upstream checksum mismatch: ' + destination.name)
        partial.replace(destination)
    digest = hashlib.file_digest(destination.open('rb'), 'sha256').hexdigest()
    return {**item, 'sha256': digest, 'bytes': destination.stat().st_size}


def collect(lock_file, output):
    output.mkdir(parents=True, exist_ok=True)
    pins = []
    for line in lock_file.read_text('utf-8').splitlines():
        match = re.fullmatch(r'([\w.-]+)==([^\s]+)', line.strip())
        if match:
            pins.append(match.groups())
    queue, omissions, results = [], [], []
    for name, version in pins:
        if name.lower() in {'pyside6_essentials', 'shiboken6'}:
            continue
        with read_url(f'https://pypi.org/pypi/{name}/{version}/json') as response:
            metadata = json.load(response)
        sdists = [x for x in metadata['urls'] if x['packagetype'] == 'sdist']
        if not sdists:
            omissions.append({'component': name, 'version': version, 'reason': 'No sdist in PyPI version metadata'})
            continue
        entry = sdists[0]
        queue.append({'component': name, 'version': version, 'filename': entry['filename'], 'url': entry['url'], 'upstream_sha256': entry['digests']['sha256']})
    for name in ('qtbase', 'qtsvg', 'qtimageformats'):
        filename = f'{name}-everywhere-src-6.11.1.tar.xz'
        queue.append({'component': name, 'version': '6.11.1', 'filename': filename, 'url': f'https://download.qt.io/official_releases/qt/6.11/6.11.1/submodules/{filename}'})
    queue.extend([
        {'component': 'PySide6 and Shiboken6', 'version': '6.11.1', 'filename': 'pyside-setup-everywhere-src-6.11.1.tar.xz', 'url': 'https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.1-src/pyside-setup-everywhere-src-6.11.1.tar.xz'},
        {'component': 'CPython', 'version': '3.14.5', 'filename': 'Python-3.14.5.tar.xz', 'url': 'https://www.python.org/ftp/python/3.14.5/Python-3.14.5.tar.xz'},
        {'component': 'MuPDF', 'version': '1.27.2', 'filename': 'mupdf-1.27.2-source.tar.gz', 'url': 'https://github.com/ArtifexSoftware/mupdf-downloads/releases/download/1.27.2/mupdf-1.27.2-source.tar.gz', 'upstream_sha256': '553867b135303dc4c25ab67c5f234d8e900a0e36e66e8484d99adc05fe1e8737'},
        {'component': 'Mesa llvmpipe', 'version': '11.2.2', 'filename': 'mesa-11.2.2.tar.xz', 'url': 'https://archive.mesa3d.org/older-versions/11.x/11.2.2/mesa-11.2.2.tar.xz'},
        {'component': 'LLVM (Mesa renderer)', 'version': '3.6.2', 'filename': 'llvm-3.6.2.src.tar.xz', 'url': 'https://releases.llvm.org/3.6.2/llvm-3.6.2.src.tar.xz'},
    ])
    # Exact versions named by CPython 3.14.5 PCbuild/get_externals.bat.
    # Tk is not bundled by this application's PyInstaller configuration.
    for tag in ('bzip2-1.0.8', 'libffi-3.4.4', 'openssl-3.0.20', 'mpdecimal-4.0.0', 'sqlite-3.50.4.0', 'xz-5.2.5', 'zlib-ng-2.2.4', 'zstd-1.5.7'):
        queue.append({'component': 'CPython external: ' + tag, 'version': tag, 'filename': 'cpython-source-deps-' + tag + '.tar.gz', 'url': f'https://codeload.github.com/python/cpython-source-deps/tar.gz/refs/tags/{tag}'})
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = {pool.submit(download, item, output): item for item in queue}
        for future in as_completed(jobs):
            item = jobs[future]
            try:
                result = future.result()
                results.append(result)
                print(f"OK {item['filename']} {result['bytes']}", flush=True)
            except Exception as error:
                omissions.append({'component': item['component'], 'version': item['version'], 'reason': str(error)})
                print(f"FAILED {item['component']}: {type(error).__name__}", flush=True)
    manifest = {'format_version': 1, 'archives': sorted(results, key=lambda x: x['filename']), 'omissions': omissions}
    (output / 'SOURCE-MANIFEST.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    (output / 'SHA256SUMS.txt').write_text(''.join(f"{x['sha256']}  {x['filename']}\n" for x in manifest['archives']), encoding='utf-8')
    if omissions:
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--lock-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    collect(args.lock_file, args.output)
