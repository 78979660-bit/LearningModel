"""Wind must stop offscreen and leave interactive geometry unchanged."""
from PySide6.QtQml import QQmlEngine, QQmlExpression
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from study_app.quick.self_test import wait_until
from test_qml_window import quick_window, find_item, visual_items


def evaluate(window, code):
    expression = QQmlExpression(QQmlEngine.contextForObject(window), window, code)
    value, undefined = expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    return value


def test_wind_pauses_when_hidden_and_resumes_without_reset(quick_window):
    _, window, _, engine, *_ = quick_window
    window.requestActivate()
    wait_until(lambda: evaluate(window, "Theme.windActive"))
    start = evaluate(window, "Theme.windPhase")
    QTest.qWait(150)
    assert evaluate(window, "Theme.windPhase") > start
    window.hide()
    QTest.qWait(60)
    stopped = evaluate(window, "Theme.windPhase")
    QTest.qWait(180)
    assert not evaluate(window, "Theme.windActive")
    assert evaluate(window, "Theme.windPhase") == stopped
    window.show()
    window.requestActivate()
    wait_until(lambda: evaluate(window, "Theme.windActive"))
    QTest.qWait(100)
    assert evaluate(window, "Theme.windPhase") > stopped
    assert not engine._qml_warnings


def test_wind_switch_and_peak_curl_preserve_input_and_small_layout(quick_window):
    _, window, bridge, engine, *_ = quick_window
    bridge.navigate("plan")
    budget = find_item(window, "budgetMinutes")
    budget.setProperty("text", "73")
    menu = window.findChild(QObject, "ambientWindToggle")
    assert menu is not None
    action = QQmlExpression(QQmlEngine.contextForObject(menu), menu, "triggered()")
    action.evaluate()
    assert not action.hasError()
    assert not evaluate(window, "Theme.windEnabled")
    assert not find_item(window, "windPetals").isVisible()
    assert evaluate(window, "Theme.windSway") == 0
    evaluate(window, "Theme.windPhase = 0.05")
    assert evaluate(window, "Theme.paperLift") == 0
    evaluate(window, "Theme.windEnabled = true")
    # Freeze only the clock to inspect the strongest fold in a deterministic frame.
    window.findChild(QObject, "ambientWindClock").setProperty("paused", True)
    evaluate(window, "Theme.windPhase = 0.05")
    geometry = (budget.x(), budget.y(), budget.width(), budget.height())
    QTest.qWait(80)
    assert evaluate(window, "Theme.paperLift") > 0.95
    assert (budget.x(), budget.y(), budget.width(), budget.height()) == geometry
    assert budget.property("text") == "73"
    corners = [item for item in visual_items(window.contentItem())
               if item.objectName() == "windPaperCorner"]
    assert any(corner.isVisible() for corner in corners)
    bridge.navigate("assistant")
    composer = find_item(window, "assistantComposer")
    window.resize(800, 600)
    QTest.qWait(150)
    assert composer.isVisible() and composer.isEnabled()
    assert not any(corner.isVisible() for corner in corners
                   if not corner.parentItem().isVisible())
    assert not engine._qml_warnings


def test_gust_has_quiet_gaps_and_petals_accelerate_in_the_wind(quick_window):
    _, window, _, engine, *_ = quick_window
    window.findChild(QObject, "ambientWindClock").setProperty("paused", True)
    window.resize(1280, 820)
    QTest.qWait(80)
    stream = find_item(window, "windPetals")
    header = find_item(window, "windHeaderPetals")
    petals = [item for container in (stream, header) for item in container.childItems()
              if item.property("progress") is not None]
    assert len(petals) == 34
    # Quiet periods return to rest, rather than bending in the opposite direction.
    for phase in (0.18, 0.45, 0.90):
        evaluate(window, f"Theme.windPhase = {phase}")
        assert evaluate(window, "Theme.windSway") == 0
        assert evaluate(window, "Theme.bambooSway") == 0
        assert evaluate(window, "Theme.paperLift") == 0
    for phase in (0.04, 0.07, 0.29, 0.34, 0.69, 0.74):
        evaluate(window, f"Theme.windPhase = {phase}")
        assert evaluate(window, "Theme.windSway") > 0
        assert evaluate(window, "Theme.bambooSway") > 0
    speeds = []
    for phase in (0.18, 0.037):
        evaluate(window, f"Theme.windPhase = {phase}")
        first = petals[0].property("progress")
        evaluate(window, f"Theme.windPhase = {phase + 0.001}")
        speeds.append((petals[0].property("progress") - first) % 1)
    assert speeds[1] > speeds[0] * 2
    window.resize(800, 600)
    QTest.qWait(80)
    assert sum(item.property("progress") is not None
               for container in (stream, header) for item in container.childItems()) == 15
    assert not engine._qml_warnings
