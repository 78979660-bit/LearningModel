"""Reproduce the original QML tests with isolated profiles and optional native AV capture.

No rendering backend, production routing, or original test assertions are replaced.
The stress mode calls the original fixture/functions, including their early-exit
item traversal and temporary QQmlExpression objects, with default phase-one data.
"""
from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import faulthandler
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
from types import SimpleNamespace

PROJECT = Path(__file__).resolve().parents[2]
ORIGINAL_MODULES = (
    "tests/test_qml_bridge.py", "tests/test_qml_runtime.py",
    "tests/test_qml_self_test.py", "tests/test_qml_window.py",
    "tests/test_qml_workspace_bridge.py", "tests/test_qml_workspace_window.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", choices=("windows", "offscreen"), default="offscreen")
    parser.add_argument("--mode", choices=("original", "stress"), default="original")
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--prefix", action="store_true", help="Include original collapse/refresh/resize tests in stress mode")
    parser.add_argument("--collect-garbage", action="store_true", help="Force Qt/Python GC before stress-mode mouse clicks")
    parser.add_argument("--capture-dll", type=Path)
    parser.add_argument("--trace-properties", action="store_true", help="Record DTO getter names and dispatch calls, without data values")
    parser.add_argument("--trace-navigation", action="store_true", help="Record click target and route before Qt event dispatch")
    parser.add_argument("--dto-metatype", choices=("QVariantMap", "QVariant"), default=None,
                        help="Diagnostic only: compile bridge DTO properties with the alternate Qt metatype in this child")
    parser.add_argument("--output-dir", type=Path)
    options = parser.parse_args()
    if options.capture_dll:
        options.capture_dll = options.capture_dll.resolve()
    if not 1 <= options.cycles <= 100:
        parser.error("--cycles must be between 1 and 100")
    if options.mode == "original" and (options.collect_garbage or options.prefix):
        parser.error("--collect-garbage and --prefix require --mode stress")
    if options.capture_dll and (os.name != "nt" or platform.machine().lower() not in ("amd64", "x86_64")):
        parser.error("The native capture DLL requires Windows x64")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    result_dir = (options.output_dir or PROJECT / "output/qml-diagnostics" / f"{stamp}-{os.getpid()}").resolve()
    # A run never replaces earlier logs, metadata, or crash dumps.
    result_dir.mkdir(parents=True, exist_ok=False)
    os.environ["QT_QPA_PLATFORM"] = options.platform
    os.chdir(PROJECT)
    sys.path[:0] = [str(PROJECT), str(PROJECT / "tests")]
    faulthandler.enable()
    sys.stdout.reconfigure(encoding="utf-8")
    if os.name == "nt":
        # Process-local: avoid an OS error dialog keeping a failed probe alive.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetErrorMode.argtypes = [ctypes.c_uint]
        kernel.SetErrorMode.restype = ctypes.c_uint
        kernel.SetErrorMode(0x0001 | 0x0002)
    library = None
    if options.capture_dll:
        library = ctypes.CDLL(str(options.capture_dll.resolve()))
        library.install_capture.argtypes = [ctypes.c_wchar_p]
        library.install_capture.restype = ctypes.c_int
        library.capture_count.restype = ctypes.c_int
        if not library.install_capture(str(result_dir / "access-violation.dmp")):
            raise RuntimeError("Could not install native AV capture")

    sources = ["study_app/quick/bridge.py", "study_app/quick/runtime.py",
               "study_app/quick/workspace_bridge.py", "study_app/quick/qml/Main.qml",
               "study_app/quick/qml/pages/PlanPage.qml", "study_app/quick/qml/pages/PlanningPane.qml",
               "study_app/quick/qml/components/RouteSidebar.qml", "tests/test_qml_window.py"]
    import PySide6
    from PySide6.QtCore import qVersion
    report = {
        "started_utc": stamp, "pid": os.getpid(), "python": sys.version, "executable": sys.executable,
        "pyside": PySide6.__version__, "qt": qVersion(),
        "platform": options.platform, "mode": options.mode,
        "cycles": options.cycles if options.mode == "stress" else None,
        "prefix": options.prefix, "collect_garbage": options.collect_garbage,
        "trace_properties": options.trace_properties,
        "trace_navigation": options.trace_navigation,
        "dto_metatype": options.dto_metatype,
        "environment": {key: os.environ.get(key) for key in (
            "QT_SCALE_FACTOR", "QSG_RHI_BACKEND", "QSG_RENDER_LOOP", "QV4_FORCE_INTERPRETER")},
        "source_sha256": {name: hashlib.sha256((PROJECT / name).read_bytes()).hexdigest() for name in sources},
    }
    report_path = result_dir / "run.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"diagnostics={result_dir}", flush=True)
    trace_stream = None
    if options.trace_properties:
        trace_stream = (result_dir / "properties.jsonl").open("w", encoding="utf-8", buffering=1)
        property_names = {"snapshot", "planData", "recordsData", "ojData", "practiceData",
                          "practiceOptions", "lastResult", "fileSearch", "budgetPreview",
                          "planPreview", "recordPreview", "practicePreview", "run", "navigate"}
        def trace_property(frame, event, value):
            code = frame.f_code
            if (event in ("call", "return") and code.co_name in property_names
                    and Path(code.co_filename).name in ("bridge.py", "workspace_bridge.py")):
                receiver = frame.f_locals.get("self")
                trace_stream.write(json.dumps({"t": time.perf_counter(), "event": event,
                    "method": code.co_name, "receiver": id(receiver),
                    "page": getattr(receiver, "_page", None),
                    "action": frame.f_locals.get("action"), "result_type": type(value).__name__}) + "\n")
        sys.setprofile(trace_property)

    with tempfile.TemporaryDirectory(prefix="qml-diagnostic-profile-") as profile_dir:
        profile = Path(profile_dir)
        (profile / "legacy").mkdir()
        # Set before importing application paths. Pytest additionally applies its
        # existing conftest isolation before collecting the original modules.
        os.environ["LEARNINGMODEL_DATA_ROOT"] = str(profile / "data")
        os.environ["LEARNINGMODEL_LEGACY_ROOT"] = str(profile / "legacy")
        if options.dto_metatype is not None:
            report["diagnostic_module_sha256"] = {}
            for name in ("workspace_bridge", "bridge"):
                module_name = "study_app.quick." + name
                module_path = PROJECT / "study_app/quick" / (name + ".py")
                source = module_path.read_text(encoding="utf-8").replace(
                    '@Property("QVariantMap",', '@Property("' + options.dto_metatype + '",')
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                exec(compile(source, str(module_path), "exec"), module.__dict__)
                report["diagnostic_module_sha256"][module_name] = hashlib.sha256(source.encode()).hexdigest()
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if options.mode == "original":
            import pytest
            result = pytest.main(["-q", "-p", "no:cacheprovider", *ORIGINAL_MODULES])
        else:
            import test_qml_window as original
            cases = []
            if options.prefix:
                cases.extend([
                    (original.test_first_collapse_reversal_routes_and_real_data_refresh, {}),
                    (original.test_keyboard_focus_shortcuts_and_window_resize, {}),
                ])
            cases.extend([
                (original.test_current_route_stays_highlighted_on_repeated_activation, {"collapsed": False}),
                (original.test_current_route_stays_highlighted_on_repeated_activation, {"collapsed": True}),
            ])
            original_click = original.click
            navigation_stream = None
            if options.trace_navigation:
                navigation_stream = (result_dir / "navigation.jsonl").open("w", encoding="utf-8", buffering=1)
            completed = 0
            for cycle in range(options.cycles):
                for test, arguments in cases:
                    print(json.dumps({"cycle": cycle, "test": test.__name__, **arguments}), flush=True)
                    with tempfile.TemporaryDirectory(prefix="qml-diagnostic-case-") as case_dir:
                        fixture = original.quick_window.__wrapped__(Path(case_dir), SimpleNamespace(param=None))
                        data = None
                        try:
                            data = next(fixture)
                            if options.collect_garbage or options.trace_navigation:
                                def gc_click(window, item):
                                    if navigation_stream:
                                        navigation_stream.write(json.dumps({"cycle": cycle, "test": test.__name__,
                                            "target": item.objectName(), "page": data[2]._page}) + "\n")
                                    if options.collect_garbage:
                                        data[3].collectGarbage()
                                        gc.collect()
                                    original_click(window, item)
                                original.click = gc_click
                            test(data, **arguments)
                            completed += 1
                        finally:
                            original.click = original_click
                            fixture.close()  # Original fixture drains workers before directory cleanup.
                            data = None
            report["passed_cases"] = completed
            if navigation_stream:
                navigation_stream.close()
            result = 0
    report["exit_code"] = int(result)
    sys.setprofile(None)
    if trace_stream:
        trace_stream.close()
    report["captured_access_violations"] = library.capture_count() if library else None
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"exit_code": int(result), "captured_access_violations": report["captured_access_violations"]}), flush=True)
    return int(result)


if __name__ == "__main__":
    raise SystemExit(main())
