"""Observe one Windows child using DEBUG_ONLY_THIS_PROCESS for diagnostics.

This tool attaches only to the requested child. Child stdout/stderr and
debugger observations go to separate files.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time


if os.name != "nt":
    raise SystemExit("Windows only")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)
dbghelp = ctypes.WinDLL("dbghelp", use_last_error=True)

DWORD = wintypes.DWORD
WORD = wintypes.WORD
BOOL = wintypes.BOOL
HANDLE = wintypes.HANDLE
ULONG_PTR = ctypes.c_size_t
LPVOID = wintypes.LPVOID

DEBUG_ONLY_THIS_PROCESS = 0x00000002
CREATE_NO_WINDOW = 0x08000000
STARTF_USESTDHANDLES = 0x00000100
CREATE_ALWAYS = 2
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
FILE_ATTRIBUTE_NORMAL = 0x00000080
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

EXCEPTION_DEBUG_EVENT = 1
CREATE_PROCESS_DEBUG_EVENT = 3
LOAD_DLL_DEBUG_EVENT = 6
EXIT_PROCESS_DEBUG_EVENT = 5
DBG_CONTINUE = 0x00010002
DBG_EXCEPTION_NOT_HANDLED = 0x80010001
EXCEPTION_BREAKPOINT = 0x80000003
EXCEPTION_ACCESS_VIOLATION = 0xC0000005
WAIT_OBJECT_0 = 0x00000000
WAIT_TIMEOUT = 0x00000102

LIST_MODULES_ALL = 0x03
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
MINIDUMP_WITH_INDIRECTLY_REFERENCED_MEMORY = 0x00000040
MINIDUMP_WITH_UNLOADED_MODULES = 0x00000020
MINIDUMP_WITH_THREAD_INFO = 0x00001000


class EXCEPTION_RECORD(ctypes.Structure):
    pass


EXCEPTION_RECORD._fields_ = [
    ("ExceptionCode", DWORD),
    ("ExceptionFlags", DWORD),
    ("ExceptionRecord", ctypes.POINTER(EXCEPTION_RECORD)),
    ("ExceptionAddress", LPVOID),
    ("NumberParameters", DWORD),
    ("_alignment", DWORD),
    ("ExceptionInformation", ULONG_PTR * 15),
]


class EXCEPTION_DEBUG_INFO(ctypes.Structure):
    _fields_ = [("ExceptionRecord", EXCEPTION_RECORD), ("dwFirstChance", DWORD)]


class DEBUG_EVENT_UNION(ctypes.Union):
    _fields_ = [
        ("Exception", EXCEPTION_DEBUG_INFO),
        ("alignment", ctypes.c_void_p),
        ("raw", ctypes.c_byte * 160),
    ]


class DEBUG_EVENT(ctypes.Structure):
    _fields_ = [
        ("dwDebugEventCode", DWORD),
        ("dwProcessId", DWORD),
        ("dwThreadId", DWORD),
        ("u", DEBUG_EVENT_UNION),
    ]


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ("cb", DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", DWORD), ("dwY", DWORD),
        ("dwXSize", DWORD), ("dwYSize", DWORD),
        ("dwXCountChars", DWORD), ("dwYCountChars", DWORD),
        ("dwFillAttribute", DWORD), ("dwFlags", DWORD),
        ("wShowWindow", WORD), ("cbReserved2", WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_ubyte)),
        ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", HANDLE), ("hThread", HANDLE),
        ("dwProcessId", DWORD), ("dwThreadId", DWORD),
    ]


class SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("nLength", DWORD), ("lpSecurityDescriptor", LPVOID), ("bInheritHandle", BOOL),
    ]


class MODULEINFO(ctypes.Structure):
    _fields_ = [("lpBaseOfDll", LPVOID), ("SizeOfImage", DWORD), ("EntryPoint", LPVOID)]


class M128A(ctypes.Structure):
    _fields_ = [("Low", ctypes.c_ulonglong), ("High", ctypes.c_longlong)]


class XMM_SAVE_AREA32(ctypes.Structure):
    _fields_ = [
        ("ControlWord", WORD), ("StatusWord", WORD),
        ("TagWord", ctypes.c_ubyte), ("Reserved1", ctypes.c_ubyte),
        ("ErrorOpcode", WORD), ("ErrorOffset", DWORD),
        ("ErrorSelector", WORD), ("Reserved2", WORD),
        ("DataOffset", DWORD), ("DataSelector", WORD), ("Reserved3", WORD),
        ("MxCsr", DWORD), ("MxCsr_Mask", DWORD),
        ("FloatRegisters", M128A * 8), ("XmmRegisters", M128A * 16),
        ("Reserved4", ctypes.c_ubyte * 96),
    ]


class CONTEXT_AMD64(ctypes.Structure):
    # Windows SDK CONTEXT (AMD64), 1232 bytes. Buffer alignment is provided
    # separately because ctypes only guarantees pointer-size alignment.
    _fields_ = [
        ("P1Home", ctypes.c_ulonglong), ("P2Home", ctypes.c_ulonglong),
        ("P3Home", ctypes.c_ulonglong), ("P4Home", ctypes.c_ulonglong),
        ("P5Home", ctypes.c_ulonglong), ("P6Home", ctypes.c_ulonglong),
        ("ContextFlags", DWORD), ("MxCsr", DWORD),
        ("SegCs", WORD), ("SegDs", WORD), ("SegEs", WORD),
        ("SegFs", WORD), ("SegGs", WORD), ("SegSs", WORD), ("EFlags", DWORD),
        ("Dr0", ctypes.c_ulonglong), ("Dr1", ctypes.c_ulonglong),
        ("Dr2", ctypes.c_ulonglong), ("Dr3", ctypes.c_ulonglong),
        ("Dr6", ctypes.c_ulonglong), ("Dr7", ctypes.c_ulonglong),
        ("Rax", ctypes.c_ulonglong), ("Rcx", ctypes.c_ulonglong),
        ("Rdx", ctypes.c_ulonglong), ("Rbx", ctypes.c_ulonglong),
        ("Rsp", ctypes.c_ulonglong), ("Rbp", ctypes.c_ulonglong),
        ("Rsi", ctypes.c_ulonglong), ("Rdi", ctypes.c_ulonglong),
        ("R8", ctypes.c_ulonglong), ("R9", ctypes.c_ulonglong),
        ("R10", ctypes.c_ulonglong), ("R11", ctypes.c_ulonglong),
        ("R12", ctypes.c_ulonglong), ("R13", ctypes.c_ulonglong),
        ("R14", ctypes.c_ulonglong), ("R15", ctypes.c_ulonglong),
        ("Rip", ctypes.c_ulonglong),
        ("FltSave", XMM_SAVE_AREA32),
        ("VectorRegister", M128A * 26),
        ("VectorControl", ctypes.c_ulonglong),
        ("DebugControl", ctypes.c_ulonglong),
        ("LastBranchToRip", ctypes.c_ulonglong),
        ("LastBranchFromRip", ctypes.c_ulonglong),
        ("LastExceptionToRip", ctypes.c_ulonglong),
        ("LastExceptionFromRip", ctypes.c_ulonglong),
    ]


class EXCEPTION_POINTERS(ctypes.Structure):
    _fields_ = [
        ("ExceptionRecord", ctypes.POINTER(EXCEPTION_RECORD)),
        ("ContextRecord", ctypes.POINTER(CONTEXT_AMD64)),
    ]


class MINIDUMP_EXCEPTION_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("ThreadId", DWORD),
        ("ExceptionPointers", ctypes.POINTER(EXCEPTION_POINTERS)),
        ("ClientPointers", BOOL),
    ]


kernel32.CreateFileW.argtypes = [
    wintypes.LPCWSTR, DWORD, DWORD, ctypes.POINTER(SECURITY_ATTRIBUTES),
    DWORD, DWORD, HANDLE,
]
kernel32.CreateFileW.restype = HANDLE
kernel32.CreateProcessW.argtypes = [
    wintypes.LPCWSTR, wintypes.LPWSTR, LPVOID, LPVOID, BOOL, DWORD, LPVOID,
    wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW), ctypes.POINTER(PROCESS_INFORMATION),
]
kernel32.CreateProcessW.restype = BOOL
kernel32.WaitForDebugEvent.argtypes = [ctypes.POINTER(DEBUG_EVENT), DWORD]
kernel32.WaitForDebugEvent.restype = BOOL
kernel32.ContinueDebugEvent.argtypes = [DWORD, DWORD, DWORD]
kernel32.ContinueDebugEvent.restype = BOOL
kernel32.CloseHandle.argtypes = [HANDLE]
kernel32.CloseHandle.restype = BOOL
kernel32.TerminateProcess.argtypes = [HANDLE, wintypes.UINT]
kernel32.TerminateProcess.restype = BOOL
kernel32.OpenThread.argtypes = [DWORD, BOOL, DWORD]
kernel32.OpenThread.restype = HANDLE
kernel32.GetThreadContext.argtypes = [HANDLE, ctypes.POINTER(CONTEXT_AMD64)]
kernel32.GetThreadContext.restype = BOOL
kernel32.ReadProcessMemory.argtypes = [HANDLE, LPVOID, LPVOID, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.restype = BOOL
kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
kernel32.GetExitCodeProcess.restype = BOOL
kernel32.WaitForSingleObject.argtypes = [HANDLE, DWORD]
kernel32.WaitForSingleObject.restype = DWORD
psapi.EnumProcessModulesEx.argtypes = [HANDLE, ctypes.POINTER(HANDLE), DWORD,
                                        ctypes.POINTER(DWORD), DWORD]
psapi.EnumProcessModulesEx.restype = BOOL
psapi.GetModuleInformation.argtypes = [HANDLE, HANDLE, ctypes.POINTER(MODULEINFO), DWORD]
psapi.GetModuleInformation.restype = BOOL
psapi.GetModuleFileNameExW.argtypes = [HANDLE, HANDLE, wintypes.LPWSTR, DWORD]
psapi.GetModuleFileNameExW.restype = DWORD
dbghelp.MiniDumpWriteDump.argtypes = [HANDLE, DWORD, HANDLE, DWORD, LPVOID, LPVOID, LPVOID]
dbghelp.MiniDumpWriteDump.restype = BOOL


def winerror(operation: str) -> OSError:
    return ctypes.WinError(ctypes.get_last_error(), operation)


def write_event(log, **values) -> None:
    log.write(json.dumps(values, ensure_ascii=False, sort_keys=True) + "\n")
    log.flush()


def module_for_address(process_handle, address: int) -> dict | None:
    modules = (HANDLE * 2048)()
    bytes_needed = DWORD()
    if not psapi.EnumProcessModulesEx(
        process_handle, modules, ctypes.sizeof(modules), ctypes.byref(bytes_needed), LIST_MODULES_ALL
    ):
        return {"module_lookup_error": ctypes.get_last_error()}
    count = min(bytes_needed.value // ctypes.sizeof(HANDLE), len(modules))
    for module in modules[:count]:
        info = MODULEINFO()
        if not psapi.GetModuleInformation(process_handle, module, ctypes.byref(info), ctypes.sizeof(info)):
            continue
        base = ctypes.cast(info.lpBaseOfDll, ctypes.c_void_p).value or 0
        end = base + info.SizeOfImage
        if base <= address < end:
            path_buffer = ctypes.create_unicode_buffer(32768)
            psapi.GetModuleFileNameExW(process_handle, module, path_buffer, len(path_buffer))
            return {
                "module": path_buffer.value,
                "module_base": hex(base),
                "module_offset": hex(address - base),
                "module_size": hex(info.SizeOfImage),
            }
    return None


def capture_amd64_context(process_handle, thread_id: int, log):
    if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(CONTEXT_AMD64) != 1232:
        write_event(log, event="context_unavailable", reason="debugger is not AMD64",
                    pointer_size=ctypes.sizeof(ctypes.c_void_p),
                    context_size=ctypes.sizeof(CONTEXT_AMD64))
        return None
    thread = kernel32.OpenThread(0x0008 | 0x0040, False, thread_id)  # GET_CONTEXT | QUERY_INFO
    if not thread:
        write_event(log, event="context_error", operation="OpenThread",
                    error=ctypes.get_last_error(), tid=thread_id)
        return None
    storage = ctypes.create_string_buffer(ctypes.sizeof(CONTEXT_AMD64) + 15)
    aligned_address = (ctypes.addressof(storage) + 15) & ~0xF
    context_ptr = ctypes.cast(aligned_address, ctypes.POINTER(CONTEXT_AMD64))
    ctypes.memset(aligned_address, 0, ctypes.sizeof(CONTEXT_AMD64))
    context_ptr.contents.ContextFlags = 0x0010000B  # AMD64 | CONTROL | INTEGER | FLOATING_POINT
    try:
        if not kernel32.GetThreadContext(thread, context_ptr):
            write_event(log, event="context_error", operation="GetThreadContext",
                        error=ctypes.get_last_error(), tid=thread_id)
            return None
        context = context_ptr.contents
        registers = {
            name: hex(getattr(context, name))
            for name in ("Rip", "Rsp", "Rbp", "Rax", "Rbx", "Rcx", "Rdx", "Rsi", "Rdi",
                         "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15", "EFlags")
        }
        stack_preview = b""
        stack_error = None
        bytes_read = ctypes.c_size_t()
        remote_stack = ctypes.c_void_p(context.Rsp)
        stack_buffer = ctypes.create_string_buffer(4096)
        if kernel32.ReadProcessMemory(process_handle, remote_stack, stack_buffer,
                                      len(stack_buffer), ctypes.byref(bytes_read)):
            stack_preview = stack_buffer.raw[:min(bytes_read.value, 256)]
        else:
            stack_error = ctypes.get_last_error()
        context_info = {
            "registers": registers,
            "rip_module": module_for_address(process_handle, context.Rip),
            "stack_address": hex(context.Rsp),
            "stack_bytes_read": bytes_read.value,
            "stack_preview_hex": stack_preview.hex(),
        }
        if stack_error is not None:
            context_info["stack_read_error"] = stack_error
        write_event(log, event="exception_context", tid=thread_id, **context_info)
        # Keep both storage and aligned pointer alive through MiniDumpWriteDump.
        return storage, context_ptr, context_info
    finally:
        kernel32.CloseHandle(thread)


def minidump_stream_types(dump_path: Path) -> list[int]:
    # MINIDUMP_HEADER followed by NumberOfStreams 12-byte directories.
    with dump_path.open("rb") as dump_file:
        header = dump_file.read(32)
        if len(header) < 32:
            return []
        signature, _version, stream_count, directory_rva = struct.unpack_from("<IIII", header)
        if signature != 0x504D444D:  # "MDMP"
            return []
        dump_file.seek(directory_rva)
        directories = dump_file.read(stream_count * 12)
        if len(directories) != stream_count * 12:
            return []
        return [struct.unpack_from("<I", directories, offset)[0]
                for offset in range(0, len(directories), 12)]


def write_minidump(process_handle, pid: int, dump_path: Path, log, *, thread_id: int,
                   exception_record: EXCEPTION_RECORD, context_capture) -> None:
    security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, False)
    dump_handle = kernel32.CreateFileW(
        str(dump_path), GENERIC_WRITE, FILE_SHARE_READ, ctypes.byref(security),
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, None,
    )
    if dump_handle == INVALID_HANDLE_VALUE:
        write_event(log, event="dump_error", error=ctypes.get_last_error(), path=str(dump_path))
        return
    dump_type = (MINIDUMP_WITH_INDIRECTLY_REFERENCED_MEMORY |
                 MINIDUMP_WITH_UNLOADED_MODULES | MINIDUMP_WITH_THREAD_INFO)
    try:
        context_ptr = context_capture[1] if context_capture is not None else None
        local_record = exception_record
        local_record_pointer = ctypes.pointer(local_record)
        exception_pointers = None
        exception_information = None
        if context_ptr is not None:
            exception_pointers = EXCEPTION_POINTERS(local_record_pointer, context_ptr)
            exception_information = MINIDUMP_EXCEPTION_INFORMATION(
                thread_id, ctypes.pointer(exception_pointers), False
            )
        ok = dbghelp.MiniDumpWriteDump(
            process_handle, pid, dump_handle, dump_type,
            ctypes.byref(exception_information) if exception_information is not None else None,
            None, None,
        )
        if ok:
            streams = minidump_stream_types(dump_path)
            write_event(log, event="minidump_written", path=str(dump_path), flags=hex(dump_type),
                        exception_info=exception_information is not None,
                        exception_stream_present=6 in streams, stream_types=streams)
        else:
            write_event(log, event="dump_error", error=ctypes.get_last_error(), path=str(dump_path))
    finally:
        kernel32.CloseHandle(dump_handle)


def run(child_args: list[str], cwd: Path, log_path: Path, dump_path: Path | None,
        timeout_seconds: int) -> int:
    if not child_args:
        raise ValueError("pass the child command after --")
    exe = str(Path(child_args[0]).resolve())
    if not Path(exe).is_file():
        raise FileNotFoundError(exe)
    cwd = cwd.resolve()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    child_output = log_path.with_suffix(".child.log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    security = SECURITY_ATTRIBUTES(ctypes.sizeof(SECURITY_ATTRIBUTES), None, True)
    output_handle = kernel32.CreateFileW(
        str(child_output), GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
        ctypes.byref(security), CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, None,
    )
    if output_handle == INVALID_HANDLE_VALUE:
        raise winerror("CreateFileW(child output)")

    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(startup)
    startup.dwFlags = STARTF_USESTDHANDLES
    startup.hStdInput = None
    startup.hStdOutput = output_handle
    startup.hStdError = output_handle
    process = PROCESS_INFORMATION()
    command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(child_args))
    flags = DEBUG_ONLY_THIS_PROCESS | CREATE_NO_WINDOW

    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        write_event(log, event="start", exe=exe, args=child_args[1:], cwd=str(cwd),
                    pid_hint="assigned-by-CreateProcess", flags=hex(flags),
                    child_output=str(child_output))
        try:
            created = kernel32.CreateProcessW(
                exe, command_line, None, None, True, flags, None, str(cwd),
                ctypes.byref(startup), ctypes.byref(process),
            )
            if not created:
                raise winerror("CreateProcessW")
        finally:
            kernel32.CloseHandle(output_handle)
        write_event(log, event="created", pid=process.dwProcessId, tid=process.dwThreadId)

        start = time.monotonic()
        saw_exit = False
        dumped_chances = set()
        exit_code = 1
        try:
            while time.monotonic() - start < timeout_seconds:
                event = DEBUG_EVENT()
                if not kernel32.WaitForDebugEvent(ctypes.byref(event), 1000):
                    error = ctypes.get_last_error()
                    if error == 121:  # WAIT_TIMEOUT
                        continue
                    write_event(log, event="wait_error", error=error)
                    break

                code = event.dwDebugEventCode
                continuation = DBG_CONTINUE
                if code == EXCEPTION_DEBUG_EVENT:
                    exception = event.u.Exception
                    record = exception.ExceptionRecord
                    exc_code = record.ExceptionCode
                    address = ctypes.cast(record.ExceptionAddress, ctypes.c_void_p).value or 0
                    data = {
                        "event": "exception",
                        "pid": event.dwProcessId,
                        "tid": event.dwThreadId,
                        "code": hex(exc_code),
                        "address": hex(address),
                        "first_chance": bool(exception.dwFirstChance),
                    }
                    if exc_code == EXCEPTION_ACCESS_VIOLATION:
                        data.update(module_for_address(process.hProcess, address) or {})
                        parameter_count = min(record.NumberParameters, 15)
                        data["exception_parameters"] = [
                            hex(record.ExceptionInformation[index]) for index in range(parameter_count)
                        ]
                        if parameter_count >= 2:
                            data["access_type"] = {
                                0: "read", 1: "write", 8: "execute"
                            }.get(record.ExceptionInformation[0], "unknown")
                            data["access_target"] = hex(record.ExceptionInformation[1])
                        write_event(log, **data)
                        context_capture = capture_amd64_context(
                            process.hProcess, event.dwThreadId, log
                        )
                        chance = "first" if exception.dwFirstChance else "second"
                        # Keep at most one real AV dump per chance, in the task's
                        # output folder. The exception stream contains the local
                        # exception record and stopped thread context.
                        if dump_path is not None and chance not in dumped_chances:
                            chance_path = dump_path.with_name(
                                f"{dump_path.stem}-{chance}-chance{dump_path.suffix}"
                            )
                            write_minidump(
                                process.hProcess, process.dwProcessId, chance_path, log,
                                thread_id=event.dwThreadId,
                                exception_record=record,
                                context_capture=context_capture,
                            )
                            dumped_chances.add(chance)
                    elif exc_code == EXCEPTION_BREAKPOINT:
                        write_event(log, **data)
                    continuation = DBG_EXCEPTION_NOT_HANDLED
                elif code == CREATE_PROCESS_DEBUG_EVENT:
                    write_event(log, event="create_process_debug_event")
                    # The first field of CREATE_PROCESS_DEBUG_INFO is hFile.
                    file_handle = ctypes.cast(event.u.raw, ctypes.POINTER(HANDLE))[0]
                    if file_handle:
                        kernel32.CloseHandle(file_handle)
                elif code == LOAD_DLL_DEBUG_EVENT:
                    # The first field of LOAD_DLL_DEBUG_INFO is hFile.
                    file_handle = ctypes.cast(event.u.raw, ctypes.POINTER(HANDLE))[0]
                    if file_handle:
                        kernel32.CloseHandle(file_handle)
                elif code == EXIT_PROCESS_DEBUG_EVENT:
                    exit_code = ctypes.cast(event.u.raw, ctypes.POINTER(DWORD))[0]
                    write_event(log, event="exit", code=exit_code)
                    saw_exit = True

                if not kernel32.ContinueDebugEvent(
                    event.dwProcessId, event.dwThreadId, continuation
                ):
                    write_event(log, event="continue_error", error=ctypes.get_last_error(),
                                code=code, continuation=hex(continuation))
                    break
                if saw_exit:
                    break
            else:
                write_event(log, event="timeout", seconds=timeout_seconds)
                kernel32.TerminateProcess(process.hProcess, 124)
                exit_code = 124
        finally:
            if not saw_exit:
                code = wintypes.DWORD()
                queried = kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code))
                if not queried:
                    write_event(log, event="cleanup_exit_code_error",
                                error=ctypes.get_last_error())
                elif code.value == 259:  # STILL_ACTIVE
                    if kernel32.TerminateProcess(process.hProcess, 124):
                        write_event(log, event="cleanup_terminate_requested", code=124)
                    else:
                        write_event(log, event="cleanup_terminate_error",
                                    error=ctypes.get_last_error())

                # A debuggee's process handle may not become signaled until its
                # EXIT_PROCESS_DEBUG_EVENT has been continued. Drain pending
                # events after termination before waiting on the process handle.
                drain_deadline = time.monotonic() + 10
                while not saw_exit and time.monotonic() < drain_deadline:
                    debug_event = DEBUG_EVENT()
                    if not kernel32.WaitForDebugEvent(
                        ctypes.byref(debug_event), 1000
                    ):
                        error = ctypes.get_last_error()
                        if error == 121:  # WAIT_TIMEOUT
                            continue
                        write_event(log, event="cleanup_debug_event_error", error=error)
                        break

                    debug_code = debug_event.dwDebugEventCode
                    continuation = DBG_CONTINUE
                    if debug_code == EXIT_PROCESS_DEBUG_EVENT:
                        exit_code = ctypes.cast(
                            debug_event.u.raw, ctypes.POINTER(DWORD)
                        )[0]
                        write_event(log, event="cleanup_exit_event", exit_code=exit_code)
                        saw_exit = True
                    elif debug_code == EXCEPTION_DEBUG_EVENT:
                        exception = debug_event.u.Exception.ExceptionRecord
                        write_event(log, event="cleanup_exception_event",
                                    code=hex(exception.ExceptionCode),
                                    first_chance=bool(
                                        debug_event.u.Exception.dwFirstChance
                                    ))
                        continuation = DBG_EXCEPTION_NOT_HANDLED
                    elif debug_code == CREATE_PROCESS_DEBUG_EVENT:
                        file_handle = ctypes.cast(
                            debug_event.u.raw, ctypes.POINTER(HANDLE)
                        )[0]
                        if file_handle:
                            kernel32.CloseHandle(file_handle)
                    elif debug_code == LOAD_DLL_DEBUG_EVENT:
                        file_handle = ctypes.cast(
                            debug_event.u.raw, ctypes.POINTER(HANDLE)
                        )[0]
                        if file_handle:
                            kernel32.CloseHandle(file_handle)

                    if not kernel32.ContinueDebugEvent(
                        debug_event.dwProcessId, debug_event.dwThreadId, continuation
                    ):
                        write_event(log, event="cleanup_continue_error",
                                    error=ctypes.get_last_error(), code=debug_code)
                        break

                if not saw_exit:
                    write_event(log, event="cleanup_exit_event_timeout",
                                seconds=10)

                wait_result = kernel32.WaitForSingleObject(process.hProcess, 10000)
                if wait_result == WAIT_OBJECT_0:
                    if kernel32.GetExitCodeProcess(process.hProcess, ctypes.byref(code)):
                        exit_code = code.value
                    write_event(log, event="cleanup_process_signaled", exit_code=exit_code)
                elif wait_result == WAIT_TIMEOUT:
                    write_event(log, event="cleanup_wait_timeout", milliseconds=10000)
                else:
                    write_event(log, event="cleanup_wait_error",
                                result=hex(wait_result), error=ctypes.get_last_error())
            kernel32.CloseHandle(process.hThread)
            kernel32.CloseHandle(process.hProcess)
        write_event(log, event="result", exit_code=exit_code, saw_exit=saw_exit)
    return int(exit_code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--dump", type=Path)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("child", nargs=argparse.REMAINDER)
    parsed = parser.parse_args()
    child = parsed.child
    if child and child[0] == "--":
        child = child[1:]
    return run(child, parsed.cwd, parsed.log, parsed.dump, parsed.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
