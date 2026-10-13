"""Explicit public resource allowlist shared by runtime and release packaging."""
from pathlib import Path

QML_RESOURCE_FILES = (
    "assets/fonts/NotoSerifSC[wght].ttf", "assets/fonts/OFL.txt",
    "Main.qml", "FloatWindow.qml", "qmldir", "Theme.qml",
    "components/PaperCard.qml", "components/ActionButton.qml",
    "components/StatusPill.qml", "components/AppIcon.qml",
    "components/RouteSidebar.qml", "pages/HomePage.qml", "pages/MigrationPage.qml",
    "components/RetainedLoader.qml",
    "components/StartupPreloader.qml",
    "components/SnapshotView.qml",
    "components/BodyText.qml", "components/FormField.qml", "components/FormChoice.qml", "components/FormArea.qml",
    "pages/PlanningEstimates.qml", "pages/DetailedPlanPane.qml",
    "pages/PlanPage.qml", "pages/PlanningPane.qml", "pages/RecordsPane.qml", "pages/OJPane.qml", "pages/PracticePane.qml",
    "pages/AssistantPage.qml", "pages/KnowledgePage.qml", "pages/GraphPane.qml", "pages/AlertsPane.qml",
    "pages/SettingsPage.qml", "pages/PreferencesPane.qml", "pages/SubjectsPane.qml", "pages/HomeSummaryPane.qml",
    "assets/paper-texture.svg", "assets/sakura-branch.svg", "assets/sakura-corner.png", "assets/bamboo-leaves.svg",
    "assets/icons/menu.svg", "assets/icons/home.svg", "assets/icons/plan.svg",
    "assets/icons/assistant.svg", "assets/icons/knowledge.svg", "assets/icons/settings.svg",
    "assets/icons/refresh.svg",
)


def qml_root() -> Path:
    from study_app.paths import RESOURCE_ROOT

    return RESOURCE_ROOT / "study_app" / "quick" / "qml"


def validate_resources() -> tuple[str, ...]:
    root = qml_root()
    missing = [name for name in QML_RESOURCE_FILES if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError("Missing QML resources: " + ", ".join(missing))
    return QML_RESOURCE_FILES
