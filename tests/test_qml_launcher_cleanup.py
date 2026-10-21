"""Launcher routing and removable direct self-test profiles on Windows."""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import subprocess
import sys
from types import ModuleType
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("arguments, expected", [([], "qml"), (["--qml"], "qml"), (["--widgets"], "widgets")])
def test_launcher_defaults_to_qml_and_keeps_explicit_widget_comparison(monkeypatch, arguments, expected):
    launcher = Path(__file__).resolve().parents[1] / "run_study_app.pyw"
    namespace = runpy.run_path(str(launcher), run_name="launcher_routing_test")
    main = namespace["_main"]
    monkeypatch.setitem(main.__globals__, "_configure_runtime", lambda: None)
    qml = ModuleType("study_app.quick.runtime")
    widgets = ModuleType("study_app.ui.app_runtime")
    qml.run_app = Mock(return_value=7)
    widgets.run_app = Mock()
    monkeypatch.setitem(sys.modules, qml.__name__, qml)
    monkeypatch.setitem(sys.modules, widgets.__name__, widgets)
    monkeypatch.setattr(sys, "argv", [str(launcher), *arguments])
    if expected == "qml":
        with pytest.raises(SystemExit) as result:
            main()
        assert result.value.code == 7
        qml.run_app.assert_called_once_with()
        widgets.run_app.assert_not_called()
    else:
        main()
        widgets.run_app.assert_called_once_with()
        qml.run_app.assert_not_called()


@pytest.mark.parametrize("arguments", [[], ["--qml"], ["--widgets"]])
def test_self_test_takes_priority_over_window_selection(monkeypatch, tmp_path, arguments):
    launcher = Path(__file__).resolve().parents[1] / "run_study_app.pyw"
    namespace = runpy.run_path(str(launcher), run_name="launcher_probe_test")
    main = namespace["_main"]
    monkeypatch.setitem(main.__globals__, "_configure_runtime", lambda: None)
    probe = Mock()
    monkeypatch.setitem(main.__globals__, "_run_self_test", probe)
    qml = ModuleType("study_app.quick.runtime")
    widgets = ModuleType("study_app.ui.app_runtime")
    qml.run_app = Mock()
    widgets.run_app = Mock()
    monkeypatch.setitem(sys.modules, qml.__name__, qml)
    monkeypatch.setitem(sys.modules, widgets.__name__, widgets)
    destination = tmp_path / "probe.json"
    monkeypatch.setattr(sys, "argv", [str(launcher), *arguments, "--self-test", str(destination)])
    main()
    probe.assert_called_once_with(destination.resolve())
    qml.run_app.assert_not_called()
    widgets.run_app.assert_not_called()


@pytest.mark.parametrize("fail", [False, True], ids=["success", "failure"])
def test_direct_self_test_closes_log_before_removing_profile(tmp_path, fail):
    launcher = Path(__file__).resolve().parents[1] / "run_study_app.pyw"
    # Isolate logging.shutdown from pytest's own handlers. A real FileHandler
    # reproduces the Windows sharing violation; mocking close would miss it.
    script = r'''
import logging
import os
from pathlib import Path
import runpy
import sys

namespace = runpy.run_path(sys.argv[1], run_name="launcher_test")
main = namespace["main"]
scope = main.__globals__
fail = sys.argv[2] == "True"
state = {}
original_shutdown = logging.shutdown

def tracked_shutdown():
    assert state["root"].exists(), "shutdown must precede directory cleanup"
    original_shutdown()
    state["shutdown"] = True

def run_fixture():
    root = Path(os.environ["LEARNINGMODEL_DATA_ROOT"])
    state["root"] = root.parent
    root.mkdir(parents=True)
    handler = logging.FileHandler(root / "study_app.log", encoding="utf-8")
    state["handler"] = handler
    logger = logging.getLogger("direct-selftest-fixture")
    logger.addHandler(handler)
    logger.warning("isolated fixture")
    if fail:
        raise RuntimeError("fixture self-test failure")

scope["_main"] = run_fixture
logging.shutdown = tracked_shutdown
sys.argv = [sys.argv[1], "--self-test", sys.argv[3]]
try:
    main()
except RuntimeError as error:
    assert fail and str(error) == "fixture self-test failure"
else:
    assert not fail
assert state.get("shutdown"), "file handlers were not closed explicitly"
assert state["handler"].stream is None
assert not state["root"].exists(), "temporary profile survived cleanup"
print("self-test log closed; temporary profile removed")
'''
    environment = os.environ.copy()
    for name in ("LEARNINGMODEL_DATA_ROOT", "LEARNINGMODEL_LEGACY_ROOT", "STUDY_APP_PERF_LOG"):
        environment.pop(name, None)
    environment.update(TEMP=str(tmp_path), TMP=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", script, str(launcher), str(fail), str(tmp_path / "receipt.json")],
        env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "temporary profile removed" in result.stdout
    assert not list(tmp_path.glob("learning-release-selftest-*"))
