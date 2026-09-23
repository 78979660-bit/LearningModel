"""Collect license texts from the exact distributions used by a release build.

The output is evidence for release review, not a legal conclusion. In
particular, collecting PyMuPDF's license does not resolve the project's public
redistribution blocker.
"""

from __future__ import annotations

import argparse
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
            continue
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

    manifest = {
        "format_version": 1,
        "source_lock": lock_file.name,
        "distributions": records,
        "release_blockers": [
            {
                "component": "PyMuPDF",
                "status": "unresolved",
                "reason": (
                    "Public redistribution requires a documented AGPL compliance "
                    "decision or an applicable Artifex commercial license."
                ),
            }
        ],
    }
    manifest_path = output_root / "LICENSE-MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Collected {len(records)} distributions into {output_root}")
    print("PUBLIC RELEASE BLOCKED: PyMuPDF licensing decision is unresolved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
