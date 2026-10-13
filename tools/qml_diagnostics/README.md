# QML native AV diagnostics

These helpers are for targeted Windows diagnosis and are not part of the release. `native_av_capture.c` installs a process-local vectored exception handler for `0xC0000005`, writes a minidump plus a `.meta.txt` register/module record, then continues normal exception processing. It does not change system-wide WER settings. `capture_child.py` is a separate debugger-based observer for one child process; debugger timing can affect intermittent failures.

The synthetic self-check deliberately writes to address `0x1`. Its expected process exit status is `0xC0000005`; its fault module is the self-check executable and says nothing about Qt.

From the repository root, compile with MinGW-w64 GCC (adjust `$gcc` if GCC is elsewhere):

```powershell
$gcc = 'F:\mingw64\bin\gcc.exe'
New-Item -ItemType Directory -Force -Path 'output' | Out-Null
& $gcc -std=c11 -O2 -Wall -Wextra -shared -o 'output\qml_av_veh_capture.dll' 'tools\qml_diagnostics\native_av_capture.c'
if ($LASTEXITCODE -ne 0) { throw 'VEH DLL build failed' }
& $gcc -std=c11 -O2 -Wall -Wextra -o 'output\qml_veh_av_selfcheck.exe' 'tools\qml_diagnostics\synthetic_selfcheck.c'
if ($LASTEXITCODE -ne 0) { throw 'self-check build failed' }
```

Run `output\qml_veh_av_selfcheck.exe` from the repository root to exercise the handler. It writes `output\qml-phase2-veh-selfcheck.dmp` and its sidecar. The validated dump has an ExceptionStream (stream type 6), `0xC0000005`, a context record with 1232 bytes, nonzero RSP, and an exception address matching the context RIP; the exception parameters identify a write to `0x1`.

To capture the original combined pytest sequence, use the versioned launcher. Every run needs a new output directory:

```powershell
$probeArgs = @('-B', '-u', 'tools\qml_diagnostics\run_window_probe.py',
    '--platform', 'windows', '--mode', 'original',
    '--capture-dll', 'output\qml_av_veh_capture.dll',
    '--output-dir', 'output\qml-original-next')
& '.venv-qml\Scripts\python.exe' @probeArgs
```

The launcher sets isolated data paths before importing application modules and also preserves the original pytest fixtures. Its `run.json` records versions, rendering environment and source hashes. A crash may leave only the initial report; the operating-system exit code and dump determine failure. No dump is evidence only that this run did not hit a captured access violation; it does not resolve earlier failures. If an AV occurs, inspect the sidecar for the exception module and offset, then analyze the dump with matching symbols. The dump can contain process memory, so review it before sharing. The debugger helper's minidump path is best-effort; use the VEH dump for exception-stream capture.

`capture_child.py` can observe a child without installing a handler in it. Its timeout terminates only its own child, acknowledges the debugger exit event and waits for process exit before returning. It does not modify WER or terminate unrelated applications. Debugger timing can change reproduction rates.

## Original window functions under GC pressure

Stress mode calls the original `quick_window` fixture and original collapse, refresh, resize and repeated-route tests. It retains their temporary `QQmlExpression` objects, item traversal and assertions. Each case gets a new engine and real isolated database. `--collect-garbage` adds Qt and Python collection before mouse clicks, so it is an additional pressure condition rather than the untouched historical pytest sequence.

```powershell
$probeArgs = @('-B', '-u', 'tools\qml_diagnostics\run_window_probe.py',
    '--platform', 'windows', '--mode', 'stress', '--cycles', '10',
    '--prefix', '--collect-garbage', '--trace-navigation',
    '--capture-dll', 'output\qml_av_veh_stack_capture.dll',
    '--output-dir', 'output\qml-stress-next')
& '.venv-qml\Scripts\python.exe' @probeArgs
```

Run offscreen separately with `--platform offscreen`. Navigation tracing logs click targets and route keys; optional property tracing logs getter names without DTO values. Both tracing and capture can alter timing. `--dto-metatype QVariant` compiles alternate DTO property declarations only in the diagnostic child. `--dto-metatype QVariantMap` is the matching compile/import-order control; neither option changes production files. Passing either arm is not evidence of a fix.

A real AV was captured on 2026-10-04 by native GC pressure in `output/qml-native-gc-diagnostics-20261004/`. The exception is a write in `QtQuickControls2.pyd + 0x871d`, in the registered Python-dict-to-`QVariantMap` conversion path. Offline DbgEng analysis recovered the QML property-read and Loader stack. The converter destination was a one-byte bool slot, proving a cached-property type mismatch. Earlier manual stack analysis used a ThreadList descriptor with `Rva=0`; valid stack bytes come from MemoryList. Its GPU return-address interpretation was invalid.

## Offline dump inspection

`inspect_dump.cpp` uses the installed Windows `dbgeng.dll` and the MinGW SDK. It opens an existing dump, uses only local symbols, and runs fixed read-only commands. It has no process attach/launch interface. Optional numeric arguments select a frame and storage/code addresses. There is no matching PDB for the installed PySide extension; large offsets from nearby exports are address labels, not exact function names.

```powershell
& 'F:\mingw64\bin\g++.exe' -std=c++17 -O2 -Wall -Wextra -municode -static-libgcc -static-libstdc++ 'tools\qml_diagnostics\inspect_dump.cpp' -ldbgeng -lole32 -luuid -o 'output\qml_inspect_dump.exe'
if ($LASTEXITCODE -ne 0) { throw 'dump inspector build failed' }
& 'output\qml_inspect_dump.exe' 'output\qml-native-gc-diagnostics-20261004\access-violation.dmp' 7 3b8fbe3d08 7ffe33788a91
```

Those frame/addresses belong to that specific retained dump. Omit them for the generic exception-context/stack report on another dump.

## Property-cache cause and safe regression

`metaobject_cache_probe.py` constructs the actual DashboardBridge and WorkspaceBridge declarations in an isolated child, evaluates `QtObject.target.busy`, deletes the objects/engine, and runs GC. It reads no business data. The default sentinel records any unexpected map getter and returns `None`, allowing cache misrouting to be observed without a native map write. False/true busy values are checked. This is a type/lifetime probe, not a substitute for real window tests.

```powershell
& '.venv-qml\Scripts\python.exe' -B -u 'tools\qml_diagnostics\metaobject_cache_probe.py' --cycles 400
# Restores the old missing-slot declarations only inside this diagnostic child.
# Expected failure: an old DashboardBridge metaobject address is reused by
# WorkspaceBridge, and busy is misrouted to practiceData.
& '.venv-qml\Scripts\python.exe' -B -u 'tools\qml_diagnostics\metaobject_cache_probe.py' --dynamic --cycles 400
```

The safe controls reproduced misrouting in rounds 15 and 5. DashboardBridge.busy is bool at property index 4; WorkspaceBridge.practiceData is QVariantMap at index 4. PySide's instance builder is freed, but the QML global pointer-keyed cache may still hold the old layout. Declaring `_poll` with `@Slot()` avoids the per-instance builder; the fixed control passed 400 rounds without cross-class address reuse or incorrect getter calls. `tests/test_qml_bridge.py` runs this safe regression in a subprocess.

The optional `--map-result dict` is a fault control that can crash the isolated child. With `--dynamic`, it reproduced native heap corruption and an AV; the AV is in Qt6Core during cleanup, a different site from the original window AV. `--capture-dll <dll> --dump <new-path>` retains a native dump. Do not use this fault control in the normal pytest host. The fixed dict control also passed 400 rounds. See the acceptance document for the real-window stress, full regression and source-based cause analysis.

## Native stack unwinder variant

`native_av_capture_stack.c` is a separate build that preserves a copy of the exception context, records up to 32 frames with `RtlLookupFunctionEntry` / `RtlVirtualUnwind`, and snapshots each recovered return slot before and after `MiniDumpWriteDump`. It checks each frame's RIP and RSP with `VirtualQuery`. It leaves the original `EXCEPTION_POINTERS` untouched and returns `EXCEPTION_CONTINUE_SEARCH`.

Build and run its non-Qt synthetic check from the repository root. The helper deliberately writes to address `0x1`; it should exit with `0xC0000005` after writing the named dump and sidecar. Use a new dump basename for each run to preserve prior evidence.

```powershell
& $gcc -std=c11 -O2 -Wall -Wextra -shared -o 'output\qml_av_veh_stack_capture.dll' 'tools\qml_diagnostics\native_av_capture_stack.c'
if ($LASTEXITCODE -ne 0) { throw 'stack VEH DLL build failed' }
& $gcc -std=c11 -O2 -Wall -Wextra -o 'output\qml_veh_stack_selfcheck.exe' 'tools\qml_diagnostics\synthetic_stack_selfcheck.c'
if ($LASTEXITCODE -ne 0) { throw 'stack self-check build failed' }
& 'output\qml_veh_stack_selfcheck.exe' 'output\qml-phase2-stack-veh-selfcheck.dmp'
```

The self-check writes `output\qml-phase2-stack-veh-selfcheck.dmp` and its `.meta.txt` sidecar. The synthetic frames validate the unwinder and minidump capture path only; they provide no evidence about Qt or the intermittent application crash.
