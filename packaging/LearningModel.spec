# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir definition for the LearningModel Windows application.

Project files are deliberately admitted one by one. Never replace this list
with a Tree(project_root) or a recursive glob: the source checkout can contain
personal learning data that must not enter a release.
"""

from pathlib import Path


# SPEC is the complete spec-file path; the project root is one directory above
# its packaging/ parent. Using SPEC avoids ambiguity when the caller changes CWD.
project_root = Path(SPEC).resolve().parent.parent
entry_script = project_root / "run_study_app.pyw"
version_file = project_root / "packaging" / "version_info.txt"
icon_file = project_root / "study_app" / "ui" / "assets" / "app.ico"


def require_file(relative_path: str, destination: str) -> tuple[str, str]:
    source = (project_root / relative_path).resolve()
    try:
        source.relative_to(project_root)
    except ValueError as error:
        raise SystemExit(f"Release input escapes the project root: {source}") from error
    if not source.is_file():
        raise SystemExit(f"Required release input is missing: {source}")
    forbidden_parts = {
        "app_data",
        "tests",
        ".pytest_cache",
        ".git",
        "backups",
        "logs",
    }
    if any(part.casefold() in forbidden_parts for part in source.parts):
        raise SystemExit(f"Forbidden release input: {source}")
    if source.name.casefold() in {"learning_model_v1.json", "learning_records.json"}:
        raise SystemExit(f"Personal/legacy root data cannot be packaged: {source}")
    return str(source), destination


for required in (entry_script, version_file, icon_file):
    if not required.is_file():
        raise SystemExit(f"Required release input is missing: {required}")


# Strict project-data whitelist. Python imports are discovered from the single
# launcher entry point; non-code resources are the only manually admitted data.
datas = [
    require_file("study_app/data/schema.sql", "study_app/data"),
    require_file("study_app/data/f5_schema.sql", "study_app/data"),
    require_file("study_app/resources/default_model.json", "study_app/resources"),
    require_file("study_app/resources/default_records.json", "study_app/resources"),
    require_file("study_app/ui/assets/check.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/chevron-down.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/menu.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/nav-assistant.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/nav-graph.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/nav-home.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/nav-plan.svg", "study_app/ui/assets"),
    require_file("study_app/ui/assets/nav-settings.svg", "study_app/ui/assets"),
    require_file(
        "study_app/integrations/fill_chatgpt_prompt.ps1",
        "study_app/integrations",
    ),
    require_file(
        "study_app/integrations/run_chatgpt_pdf_workflow.ps1",
        "study_app/integrations",
    ),
    require_file(
        "study_app/integrations/capture_chatgpt_pdf.ps1",
        "study_app/integrations",
    ),
]


# These imports are optional at source level but are release-supported paths.
# Naming them explicitly avoids silently dropping a feature during analysis.
hiddenimports = [
    # Imported by the release self-test through importlib; PyInstaller cannot
    # discover this string-only import from the module graph.
    "study_app.core.subject_pdf_pipeline",
    "study_app.ui.main_window",
    "fitz",
    "pymupdf",
    "pytesseract",
    "PIL.Image",
    "PIL.ImageEnhance",
    "PIL.ImageFilter",
    "PIL.ImageOps",
    "pypdf",
    "reportlab.lib.colors",
    "reportlab.lib.enums",
    "reportlab.lib.pagesizes",
    "reportlab.lib.styles",
    "reportlab.lib.units",
    "reportlab.pdfbase.pdfmetrics",
    "reportlab.pdfbase.ttfonts",
    "reportlab.pdfgen.canvas",
    "reportlab.platypus",
]


analysis = Analysis(
    [str(entry_script)],
    pathex=[str(project_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "_pytest",
        "pytest",
        "tests",
        "test",
        "tkinter",
        "IPython",
        "jupyter",
        "notebook",
    ],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(analysis.pure)

executable = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LearningModel",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    # Windows architecture is enforced by tools/build_release.ps1 before the
    # build. target_arch is a macOS cross-build option and must stay unset.
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_file),
    version=str(version_file),
    uac_admin=False,
    uac_uiaccess=False,
    contents_directory="_internal",
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LearningModel",
)
