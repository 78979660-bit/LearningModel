"""Check cross-class QML property-cache reuse without risking a native write.

The actual bridge declarations and timer connections are compiled unchanged,
except map getter bodies return a sentinel and record unexpected invocation.
The optional dynamic control removes only the two _poll Slot decorators.
"""
from pathlib import Path
import argparse, ctypes, faulthandler, gc, importlib.util, json, os, re, sys, tempfile

parser = argparse.ArgumentParser()
parser.add_argument('--dynamic', action='store_true')
parser.add_argument('--cycles', type=int, default=400)
parser.add_argument('--map-result', choices=('none', 'dict'), default='none',
    help='dict is an isolated fault control: it can trigger a real native AV')
parser.add_argument('--capture-dll', type=Path)
parser.add_argument('--dump', type=Path)
opts = parser.parse_args()
if not 1 <= opts.cycles <= 2000:
    parser.error('--cycles must be between 1 and 2000')
if bool(opts.capture_dll) != bool(opts.dump):
    parser.error('--capture-dll and --dump are required together')
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
faulthandler.enable()
capture = None
if os.name == 'nt':
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.SetErrorMode.argtypes = [ctypes.c_uint]
    kernel.SetErrorMode.restype = ctypes.c_uint
    kernel.SetErrorMode(0x0001 | 0x0002)
if opts.capture_dll:
    if opts.dump.exists():
        parser.error('dump path must be new')
    capture = ctypes.CDLL(str(opts.capture_dll.resolve()))
    capture.install_capture.argtypes = [ctypes.c_wchar_p]
    capture.install_capture.restype = ctypes.c_int
    assert capture.install_capture(str(opts.dump.resolve()))
# Imports can resolve profile paths, even though this probe never calls data
# services. Set isolated paths before application modules are loaded.
profile = tempfile.TemporaryDirectory(prefix='qml-meta-cache-probe-')
os.environ['LEARNINGMODEL_DATA_ROOT'] = str(Path(profile.name) / 'data')
os.environ['LEARNINGMODEL_LEGACY_ROOT'] = str(Path(profile.name) / 'legacy')
from PySide6.QtCore import QCoreApplication, QEvent, QUrl
from PySide6.QtQml import QQmlEngine, QQmlComponent
import PySide6.QtQuickControls2
import shiboken6

wrong = []
def unexpected_map(name):
    wrong.append(name)
    print(json.dumps({'unexpected_map_getter': name}), flush=True)
    return {} if opts.map_result == 'dict' else None

for name in ('workspace_bridge', 'bridge'):
    path = root / 'study_app/quick' / (name + '.py')
    src = path.read_text(encoding='utf-8')
    if opts.dynamic:
        src = src.replace('    @Slot()\n    def _poll(self):', '    def _poll(self):')
    # Safe sentinel: a misrouted map getter records the mismatch and returns
    # None, so we can observe it without writing a map into a one-byte slot.
    src = re.sub(r'(@Property\("QVariantMap",[^\n]*\)\n    def (\w+)\(self\):\n)        return [^\n]+',
        lambda match: match[1] + '        return unexpected_map("' + name + '.' + match[2] + '")', src)
    fullname = 'study_app.quick.' + name
    spec = importlib.util.spec_from_file_location(fullname, path)
    module = importlib.util.module_from_spec(spec)
    module.__dict__['unexpected_map'] = unexpected_map
    sys.modules[fullname] = module
    exec(compile(src, str(path), 'exec'), module.__dict__)

from study_app.quick.bridge import DashboardBridge
from study_app.quick.workspace_bridge import WorkspaceBridge
from study_app.core.async_tasks import AsyncTaskService

app = QCoreApplication(['meta-cache-probe'])
qml = b'import QtQml\nQtObject { required property var target; property bool marker: target.busy }'
seen = {}
reuse = 0
class Other:
    def request_cancel(self): pass
for cycle in range(opts.cycles):
    for typ in (DashboardBridge, WorkspaceBridge):
        tasks = AsyncTaskService(max_workers=1)
        target = (typ(object()) if typ is DashboardBridge else typ(Other(), Other(), Other(), Other(), tasks))
        if typ is DashboardBridge:
            tasks.close()
            tasks.wait_for_shutdown()
            tasks = target._tasks
        mo = target.metaObject()
        pointer = shiboken6.getCppPointer(mo)[0]
        schema = {'class': typ.__name__, 'busy_index': mo.indexOfProperty('busy'),
            'index4_name': mo.property(4).name(), 'index4_type': mo.property(4).typeName()}
        assert schema['busy_index'] == (4 if typ is DashboardBridge else 12)
        previous = seen.get(pointer)
        if previous and previous['class'] != typ.__name__:
            reuse += 1
            print(json.dumps({'cross_class_reuse': hex(pointer), 'old_schema': previous,
                'new_schema': schema, 'requested_property': 'busy'}), flush=True)
        seen[pointer] = schema
        del mo
        expected = bool(cycle % 2)
        target._busy = expected
        engine = QQmlEngine()
        warnings = []
        engine.warnings.connect(lambda errors: warnings.extend(error.toString() for error in errors))
        component = QQmlComponent(engine)
        component.setData(qml, QUrl('probe.qml'))
        obj = component.createWithInitialProperties({'target': target})
        assert obj, [error.toString() for error in component.errors()]
        if not wrong:
            assert obj.property('marker') is expected
        if wrong or warnings:
            print({'cycle': cycle, 'type': typ.__name__, 'meta': hex(pointer), 'previous': previous,
                'wrong_getters': wrong, 'warnings': warnings}, flush=True)
            raise SystemExit(1)
        obj.deleteLater()
        component.deleteLater()
        engine.deleteLater()
        target.close()
        tasks.wait_for_shutdown()
        target.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        del obj, component, engine, target, tasks
        gc.collect()
    if cycle % 50 == 0:
        print({'cycle': cycle, 'cross_class_meta_address_reuse': reuse}, flush=True)
print(json.dumps({'cycles': opts.cycles, 'wrong_getters': wrong,
    'cross_class_meta_address_reuse': reuse, 'dynamic_control': opts.dynamic}), flush=True)
profile.cleanup()
