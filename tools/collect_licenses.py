"""Collect license texts from the exact distributions used by a release build.

The output is evidence for release review, not a legal conclusion. In
particular, collecting PyMuPDF's license does not resolve the project's public
redistribution blocker.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
from importlib import metadata
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys


RUNTIME_DISTRIBUTIONS = (
    "PySide6_Essentials",
    "shiboken6",
    "PyMuPDF",
    "pytesseract",
    "Pillow",
    "reportlab",
    "pypdf",
    "packaging",
    "charset-normalizer",
)
BUILD_DISTRIBUTIONS = (
    "PyInstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "pefile",
    "pywin32-ctypes",
    "setuptools",
    "pytest",
    "colorama",
    "iniconfig",
    "pluggy",
    "Pygments",
    "pip",
)
LICENSE_BASENAME = re.compile(
    r"^(license|licence|copying|notice|copyright|authors?)([._-].*)?$",
    re.IGNORECASE,
)
PINNED_REQUIREMENT = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9_.-]*)==([^;\s]+)\s*$"
)


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).casefold()


def parse_lock_file(path: Path) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = PINNED_REQUIREMENT.fullmatch(line)
        if match is None:
            raise ValueError(
                f"{path}:{line_number}: only exact NAME==VERSION pins are allowed"
            )
        name, version = match.groups()
        canonical = canonical_name(name)
        if canonical in pins:
            raise ValueError(f"duplicate locked distribution: {name}")
        pins[canonical] = (name, version)
    return pins


def is_license_path(path: PurePosixPath) -> bool:
    lowered_parts = {part.casefold() for part in path.parts}
    return "licenses" in lowered_parts or LICENSE_BASENAME.match(path.name) is not None


def relative_license_path(path: PurePosixPath) -> Path:
    parts = list(path.parts)
    for index, part in enumerate(parts):
        if part.casefold().endswith(".dist-info"):
            parts = parts[index + 1 :]
            break
    while parts and parts[0] in {".", ".."}:
        parts.pop(0)
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return Path(path.name)
    return Path(*parts)


def license_description(distribution: metadata.Distribution) -> str:
    expression = distribution.metadata.get("License-Expression", "").strip()
    if expression:
        return expression
    legacy = distribution.metadata.get("License", "").strip()
    if legacy and legacy.casefold() != "unknown":
        return legacy
    classifiers = distribution.metadata.get_all("Classifier") or []
    license_classifiers = [
        item.removeprefix("License :: ")
        for item in classifiers
        if item.startswith("License :: ")
    ]
    return "; ".join(license_classifiers) or "UNDECLARED - manual review required"


def collect_distribution(
    requested_name: str,
    locked_version: str,
    output_root: Path,
    *,
    scope: str,
) -> dict[str, object]:
    distribution = metadata.distribution(requested_name)
    installed_version = distribution.version
    if installed_version != locked_version:
        raise RuntimeError(
            f"{requested_name}: installed {installed_version}, lock requires {locked_version}"
        )

    package_dir = output_root / f"{requested_name}-{installed_version}"
    package_dir.mkdir(parents=True, exist_ok=False)
    copied: list[str] = []
    seen_destinations: set[str] = set()
    for item in sorted(distribution.files or (), key=lambda value: str(value).casefold()):
        pure_path = PurePosixPath(str(item).replace("\\", "/"))
        if not is_license_path(pure_path):
            continue
        source = Path(distribution.locate_file(item)).resolve()
        if not source.is_file():
            raise RuntimeError(f'{requested_name}: recorded license file is missing')
        if item.hash is None or item.hash.mode != 'sha256':
            raise RuntimeError(f'{requested_name}: license file lacks SHA256 wheel RECORD evidence')
        expected = base64.urlsafe_b64decode(item.hash.value + '=' * (-len(item.hash.value) % 4)).hex()
        if hashlib.sha256(source.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f'{requested_name}: license file differs from wheel RECORD')
        relative = relative_license_path(pure_path)
        destination = package_dir / relative
        destination_key = str(destination).casefold()
        if destination_key in seen_destinations:
            destination = destination.with_name(
                f"{destination.stem}-{len(copied) + 1}{destination.suffix}"
            )
            destination_key = str(destination).casefold()
        seen_destinations.add(destination_key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(destination.relative_to(output_root).as_posix())

    if not copied:
        raise RuntimeError(
            f"{requested_name}: installed wheel contains no discoverable license file"
        )

    return {
        "name": distribution.metadata.get("Name", requested_name),
        "version": installed_version,
        "scope": scope,
        "declared_license": license_description(distribution),
        "homepage": distribution.metadata.get("Home-page", ""),
        "license_files": copied,
        "license_file_sha256": {
            name: hashlib.sha256((output_root / name).read_bytes()).hexdigest()
            for name in copied
        },
    }


def parse_arguments(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock-file",
        type=Path,
        default=Path("requirements-build.lock.txt"),
        help="exact dependency lock file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new, empty destination directory",
    )
    parser.add_argument(
        "--include-build-tools",
        action="store_true",
        help="also collect PyInstaller and pytest license files",
    )
    parser.add_argument('--distribution-policy', type=Path)
    parser.add_argument('--project-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--source-archives', type=Path)
    parser.add_argument('--source-notices', type=Path)
    parser.add_argument('--project-source', type=Path)
    parser.add_argument('--require-release-materials', action='store_true',
                        help='fail if the explicit policy and corresponding materials cannot be verified')
    parser.add_argument('--reviewed-commit', help='explicitly reviewed repository source commit')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    options = parse_arguments(sys.argv[1:] if argv is None else argv)
    lock_file = options.lock_file.resolve()
    output_root = options.output.resolve()
    if not lock_file.is_file():
        raise FileNotFoundError(f"lock file not found: {lock_file}")
    if output_root.exists() and any(output_root.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty output: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)

    pins = parse_lock_file(lock_file)
    requested: list[tuple[str, str]] = [
        (name, "runtime") for name in RUNTIME_DISTRIBUTIONS
    ]
    if options.include_build_tools:
        requested.extend((name, "build-only") for name in BUILD_DISTRIBUTIONS)

    records: list[dict[str, object]] = []
    for name, scope in requested:
        canonical = canonical_name(name)
        if canonical not in pins:
            raise RuntimeError(f"{name}: distribution is not pinned in {lock_file}")
        _, version = pins[canonical]
        records.append(
            collect_distribution(name, version, output_root, scope=scope)
        )

    if __package__:
        from .release_material_policy import evaluate_materials
    else:
        from release_material_policy import evaluate_materials
    policy_path = options.distribution_policy or options.project_root / 'release/distribution-policy.json'
    assessment = evaluate_materials(
        project_root=options.project_root, lock_file=lock_file, policy_path=policy_path,
        license_root=output_root, distributions=records,
        source_archives=options.source_archives, source_notices=options.source_notices,
        project_source=options.project_source,
        reviewed_commit=options.reviewed_commit,
    )
    manifest = {
        "format_version": 1,
        "source_lock": lock_file.name,
        "distributions": records,
        "distribution_assessment": assessment,
        "release_blockers": [
            {"component": "distribution materials", "status": "blocked", "reason": reason}
            for reason in assessment['reasons']
        ],
    }
    manifest_path = output_root / "LICENSE-MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Collected {len(records)} distributions into {output_root}")
    if assessment['status'] != 'materials_verified':
        print('PUBLIC RELEASE MATERIALS BLOCKED: ' + '; '.join(assessment['reasons']))
        if options.require_release_materials:
            return 1
    else:
        print('Corresponding materials verified under documented AGPL route; not a legal attestation.')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
