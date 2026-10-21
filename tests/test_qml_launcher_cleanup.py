"""Direct self-test profiles must be removable on Windows, even on failure."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest


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
