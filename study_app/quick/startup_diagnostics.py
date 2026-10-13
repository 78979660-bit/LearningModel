"""Bounded checks for Qt imports, Chinese catalogs and actual image decoding."""
from __future__ import annotations

from PySide6.QtCore import QEventLoop, QLibraryInfo, QTimer, QTranslator, QUrl
from PySide6.QtGui import QImageReader
from PySide6.QtQml import QQmlComponent

from study_app.quick.resources import qml_root, validate_resources


REQUIRED_IMPORTS = (
    ("QtQuick", "Item"),
    ("QtQuick.Controls", "Button"),
    ("QtQuick.Layouts", "RowLayout"),
    ("QtQuick.Dialogs", "FileDialog"),
    ("QtQuick.Window", "Window"),
    ("QtQuick.Shapes", "Shape"),
)
TRANSLATION_CATALOGS = ("qtbase_zh_CN", "qtdeclarative_zh_CN")


def install_translations(application) -> list[str]:
    if application is None:
        raise RuntimeError("Qt application must exist before installing translations")
    retained = getattr(application, "_quick_translators", None)
    if retained is None:
        retained = {}
        directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        for name in TRANSLATION_CATALOGS:
            translator = QTranslator(application)
            if translator.load(name, directory):
                application.installTranslator(translator)
                retained[name] = translator
        application._quick_translators = retained
    return list(retained)


def inspect_runtime(engine, application) -> dict:
    imports = []
    for module, type_name in REQUIRED_IMPORTS:
        component = QQmlComponent(engine)
        source = f"import QtQuick\n{'' if module == 'QtQuick' else 'import ' + module}\n{type_name} {{}}".encode("utf-8")
        component.setData(source, QUrl.fromLocalFile(str(qml_root() / "startup-probe.qml")))
        if component.isLoading():
            loop = QEventLoop()
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            component.statusChanged.connect(lambda status: loop.quit() if not component.isLoading() else None)
            deadline.start(15000)
            loop.exec()
            deadline.stop()
        if not component.isReady():
            detail = " | ".join(error.toString() for error in component.errors())
            raise RuntimeError(f"Required QML import unavailable: {module}: {detail or component.status().name}")
        instance = component.create()
        if instance is None or component.isError():
            detail = " | ".join(error.toString() for error in component.errors())
            raise RuntimeError(f"Required QML import unavailable: {module}: {detail}")
        imports.append(module)
        instance.deleteLater()
        component.deleteLater()
    images = []
    for relative in validate_resources():
        if not relative.endswith((".svg", ".png", ".jpg", ".webp")):
            continue
        reader = QImageReader(str(qml_root() / relative))
        decoded = reader.read()
        if decoded.isNull():
            raise RuntimeError("QML image cannot be decoded: " + relative)
        images.append({"resource": relative, "width": decoded.width(), "height": decoded.height()})
    translations = install_translations(application)
    if set(translations) != set(TRANSLATION_CATALOGS):
        raise RuntimeError("Required Chinese Qt catalogs are unavailable")
    return {"status": "ok", "imports": imports, "images": images, "translations": translations}
