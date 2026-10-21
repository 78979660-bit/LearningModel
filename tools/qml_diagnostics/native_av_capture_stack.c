#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dbghelp.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

#define MAX_CAPTURE_PATH 2048
#define MAX_CAPTURE_COUNT 8
#define MAX_UNWIND_FRAMES 32
#define MAX_MODULE_PATH 1024

typedef BOOL (WINAPI *MiniDumpWriteDumpFn)(HANDLE, DWORD, HANDLE, MINIDUMP_TYPE,
    PMINIDUMP_EXCEPTION_INFORMATION, PMINIDUMP_USER_STREAM_INFORMATION,
    PMINIDUMP_CALLBACK_INFORMATION);
typedef PRUNTIME_FUNCTION (NTAPI *RtlLookupFunctionEntryFn)(DWORD64, PDWORD64,
    PUNWIND_HISTORY_TABLE);
typedef PEXCEPTION_ROUTINE (NTAPI *RtlVirtualUnwindFn)(DWORD, DWORD64, DWORD64,
    PRUNTIME_FUNCTION, PCONTEXT, PVOID *, PDWORD64,
    PKNONVOLATILE_CONTEXT_POINTERS);

typedef struct UnwindFrameInfo {
    DWORD64 rip;
    DWORD64 rsp;
    DWORD64 module_base;
    DWORD64 module_offset;
    DWORD64 establisher_frame;
    DWORD64 return_slot;
    DWORD64 return_value;
    DWORD64 return_value_after_dump;
    BOOL rip_readable;
    BOOL rsp_readable;
    BOOL return_slot_readable;
    BOOL return_value_after_dump_readable;
    BOOL return_value_changed;
    BOOL unwind_used_runtime_function;
    wchar_t module_path[MAX_MODULE_PATH];
} UnwindFrameInfo;

static HMODULE g_dbghelp;
static HMODULE g_ntdll;
static MiniDumpWriteDumpFn g_write_dump;
static RtlLookupFunctionEntryFn g_lookup_function_entry;
static RtlVirtualUnwindFn g_virtual_unwind;
static PVOID g_handler;
static volatile LONG g_dumping;
static volatile LONG g_capture_count;
static wchar_t g_base_path[MAX_CAPTURE_PATH];
static UnwindFrameInfo g_unwind_frames[MAX_UNWIND_FRAMES];
static DWORD g_unwind_frame_count;
static wchar_t g_unwind_stop_reason[128];

static void make_capture_path(wchar_t *out, size_t out_count, LONG index,
                              const wchar_t *suffix) {
    if (index == 1) {
        _snwprintf(out, out_count, L"%ls%ls", g_base_path, suffix);
    } else {
        _snwprintf(out, out_count, L"%ls-%ld%ls", g_base_path, index, suffix);
    }
    out[out_count - 1] = L'\0';
}

static void write_utf8_text(const wchar_t *path, const wchar_t *text) {
    HANDLE file = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    int required = WideCharToMultiByte(CP_UTF8, 0, text, -1, NULL, 0, NULL, NULL);
    if (required > 1) {
        char *utf8 = (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)required);
        if (utf8) {
            int written = WideCharToMultiByte(CP_UTF8, 0, text, -1, utf8,
                                              required, NULL, NULL);
            if (written > 1) {
                DWORD bytes_written = 0;
                WriteFile(file, utf8, (DWORD)(written - 1), &bytes_written, NULL);
                FlushFileBuffers(file);
            }
            HeapFree(GetProcessHeap(), 0, utf8);
        }
    }
    CloseHandle(file);
}

static BOOL is_readable_range(uintptr_t address, SIZE_T size) {
    if (!address || !size || address > UINTPTR_MAX - size) return FALSE;
    uintptr_t end = address + size;
    while (address < end) {
        MEMORY_BASIC_INFORMATION info;
        SIZE_T queried = VirtualQuery((LPCVOID)address, &info, sizeof(info));
        if (queried != sizeof(info) || info.State != MEM_COMMIT ||
            (info.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return FALSE;
        DWORD protection = info.Protect & 0xff;
        if (protection != PAGE_READONLY && protection != PAGE_READWRITE &&
            protection != PAGE_WRITECOPY && protection != PAGE_EXECUTE_READ &&
            protection != PAGE_EXECUTE_READWRITE &&
            protection != PAGE_EXECUTE_WRITECOPY) return FALSE;
        uintptr_t region_start = (uintptr_t)info.BaseAddress;
        uintptr_t region_end = region_start + info.RegionSize;
        if (region_end <= address) return FALSE;
        address = region_end < end ? region_end : end;
    }
    return TRUE;
}

static BOOL is_current_stack_range_readable(DWORD64 rsp, SIZE_T size) {
    PNT_TIB tib = (PNT_TIB)NtCurrentTeb();
    uintptr_t stack_limit = (uintptr_t)tib->StackLimit;
    uintptr_t stack_base = (uintptr_t)tib->StackBase;
    uintptr_t end = (uintptr_t)rsp + size;
    return rsp >= stack_limit && end >= rsp && end <= stack_base &&
           is_readable_range((uintptr_t)rsp, size);
}

static void describe_module(DWORD64 address, UnwindFrameInfo *frame) {
    HMODULE module = NULL;
    frame->module_path[0] = L'\0';
    frame->module_base = 0;
    frame->module_offset = 0;
    if (!address || !GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            (LPCWSTR)(uintptr_t)address, &module)) {
        wcscpy(frame->module_path, L"(unknown)");
        return;
    }
    DWORD length = GetModuleFileNameW(module, frame->module_path,
        (DWORD)(sizeof(frame->module_path) / sizeof(frame->module_path[0])));
    if (!length) wcscpy(frame->module_path, L"(module path unavailable)");
    else frame->module_path[(sizeof(frame->module_path) / sizeof(frame->module_path[0])) - 1] = L'\0';
    frame->module_base = (DWORD64)(uintptr_t)module;
    frame->module_offset = address - frame->module_base;
}

static void capture_native_frames(const CONTEXT *saved_context) {
    CONTEXT unwind_context __attribute__((aligned(16)));
    memcpy(&unwind_context, saved_context, sizeof(unwind_context));
    g_unwind_frame_count = 0;
    wcscpy(g_unwind_stop_reason, L"frame_limit");

    for (DWORD i = 0; i < MAX_UNWIND_FRAMES; ++i) {
        if (!unwind_context.Rip) {
            wcscpy(g_unwind_stop_reason, L"stop_null_rip");
            return;
        }
        if (!is_readable_range((uintptr_t)unwind_context.Rip, 1)) {
            wcscpy(g_unwind_stop_reason, L"stop_unreadable_rip");
            return;
        }
        if (!is_current_stack_range_readable(unwind_context.Rsp, sizeof(DWORD64))) {
            wcscpy(g_unwind_stop_reason, L"stop_unreadable_or_out_of_bounds_rsp");
            return;
        }

        UnwindFrameInfo *frame = &g_unwind_frames[g_unwind_frame_count++];
        memset(frame, 0, sizeof(*frame));
        frame->rip = unwind_context.Rip;
        frame->rsp = unwind_context.Rsp;
        frame->rip_readable = TRUE;
        frame->rsp_readable = TRUE;
        describe_module(frame->rip, frame);

        DWORD64 previous_rip = unwind_context.Rip;
        DWORD64 previous_rsp = unwind_context.Rsp;
        DWORD64 image_base = 0;
        PRUNTIME_FUNCTION function = g_lookup_function_entry
            ? g_lookup_function_entry(unwind_context.Rip, &image_base, NULL) : NULL;
        DWORD64 establisher = 0;
        if (function && g_virtual_unwind) {
            PVOID handler_data = NULL;
            frame->unwind_used_runtime_function = TRUE;
            g_virtual_unwind(UNW_FLAG_NHANDLER, image_base, unwind_context.Rip,
                function, &unwind_context, &handler_data, &establisher, NULL);
            frame->establisher_frame = establisher;
            if (unwind_context.Rsp >= sizeof(DWORD64))
                frame->return_slot = unwind_context.Rsp - sizeof(DWORD64);
        } else {
            DWORD64 return_address = 0;
            if (!is_current_stack_range_readable(unwind_context.Rsp, sizeof(return_address))) {
                wcscpy(g_unwind_stop_reason, L"stop_leaf_return_slot_unreadable");
                return;
            }
            frame->return_slot = unwind_context.Rsp;
            memcpy(&return_address, (const void *)(uintptr_t)unwind_context.Rsp,
                   sizeof(return_address));
            frame->return_value = return_address;
            frame->return_slot_readable = TRUE;
            unwind_context.Rip = return_address;
            unwind_context.Rsp += sizeof(DWORD64);
        }

        if (function && g_virtual_unwind) {
            if (frame->return_slot && is_current_stack_range_readable(
                    frame->return_slot, sizeof(DWORD64))) {
                memcpy(&frame->return_value,
                       (const void *)(uintptr_t)frame->return_slot,
                       sizeof(frame->return_value));
                frame->return_slot_readable = TRUE;
            }
        }
        if (!frame->return_slot_readable) {
            wcscpy(g_unwind_stop_reason, L"stop_unwind_return_slot_unreadable");
            return;
        }
        if (!unwind_context.Rip || unwind_context.Rsp <= previous_rsp ||
            unwind_context.Rip == previous_rip) {
            wcscpy(g_unwind_stop_reason, L"stop_unwind_did_not_progress");
            return;
        }
    }
}

static void snapshot_return_slots_after_dump(void) {
    for (DWORD i = 0; i < g_unwind_frame_count; ++i) {
        UnwindFrameInfo *frame = &g_unwind_frames[i];
        if (!frame->return_slot_readable || !frame->return_slot) continue;
        if (is_current_stack_range_readable(frame->return_slot, sizeof(DWORD64))) {
            memcpy(&frame->return_value_after_dump,
                   (const void *)(uintptr_t)frame->return_slot,
                   sizeof(frame->return_value_after_dump));
            frame->return_value_after_dump_readable = TRUE;
            frame->return_value_changed =
                frame->return_value_after_dump != frame->return_value;
        }
    }
}

static void append_utf8_line(HANDLE file, const wchar_t *line) {
    int required = WideCharToMultiByte(CP_UTF8, 0, line, -1, NULL, 0, NULL, NULL);
    if (required <= 1) return;
    char *utf8 = (char *)HeapAlloc(GetProcessHeap(), 0, (SIZE_T)required);
    if (!utf8) return;
    int written = WideCharToMultiByte(CP_UTF8, 0, line, -1, utf8,
                                      required, NULL, NULL);
    if (written > 1) {
        DWORD bytes_written = 0;
        WriteFile(file, utf8, (DWORD)(written - 1), &bytes_written, NULL);
    }
    HeapFree(GetProcessHeap(), 0, utf8);
}

static void append_unwind_metadata(const wchar_t *path) {
    HANDLE file = CreateFileW(path, FILE_APPEND_DATA, FILE_SHARE_READ, NULL,
        OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) return;
    wchar_t line[MAX_MODULE_PATH + 512];
    _snwprintf(line, sizeof(line) / sizeof(line[0]),
        L"unwind_frame_count=%lu\r\nunwind_stop_reason=%ls\r\n",
        g_unwind_frame_count, g_unwind_stop_reason);
    line[(sizeof(line) / sizeof(line[0])) - 1] = L'\0';
    append_utf8_line(file, line);
    for (DWORD i = 0; i < g_unwind_frame_count; ++i) {
        UnwindFrameInfo *frame = &g_unwind_frames[i];
        _snwprintf(line, sizeof(line) / sizeof(line[0]),
            L"frame[%lu].RIP=0x%llx RSP=0x%llx rip_readable=%d rsp_readable=%d "
            L"module=\"%ls\" module_base=0x%llx module_offset=0x%llx "
            L"unwind=%ls establisher=0x%llx return_slot=0x%llx "
            L"return_value=0x%llx return_slot_readable=%d "
            L"return_value_after_dump=0x%llx after_dump_readable=%d changed=%d\r\n",
            i, (unsigned long long)frame->rip, (unsigned long long)frame->rsp,
            frame->rip_readable, frame->rsp_readable, frame->module_path,
            (unsigned long long)frame->module_base,
            (unsigned long long)frame->module_offset,
            frame->unwind_used_runtime_function ? L"runtime_function" : L"leaf_fallback",
            (unsigned long long)frame->establisher_frame,
            (unsigned long long)frame->return_slot,
            (unsigned long long)frame->return_value, frame->return_slot_readable,
            (unsigned long long)frame->return_value_after_dump,
            frame->return_value_after_dump_readable, frame->return_value_changed);
        line[(sizeof(line) / sizeof(line[0])) - 1] = L'\0';
        append_utf8_line(file, line);
    }
    FlushFileBuffers(file);
    CloseHandle(file);
}

static void format_metadata(wchar_t *buffer, size_t buffer_count,
                            EXCEPTION_POINTERS *exception, LONG index,
                            BOOL dump_ok, DWORD dump_error) {
    EXCEPTION_RECORD *record = exception->ExceptionRecord;
    CONTEXT *context = exception->ContextRecord;
    HMODULE module = NULL;
    wchar_t module_path[MAX_PATH * 4] = L"(unknown)";
    uintptr_t module_base = 0;
    uintptr_t address = (uintptr_t)record->ExceptionAddress;
    if (address && GetModuleHandleExW(
            GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
            (LPCWSTR)address, &module)) {
        GetModuleFileNameW(module, module_path,
                           (DWORD)(sizeof(module_path) / sizeof(module_path[0])));
        module_base = (uintptr_t)module;
    }
    ULONG_PTR operation = 0;
    ULONG_PTR target = 0;
    if (record->NumberParameters >= 1) operation = record->ExceptionInformation[0];
    if (record->NumberParameters >= 2) target = record->ExceptionInformation[1];
    const wchar_t *access = operation == 0 ? L"read" :
                            operation == 1 ? L"write" :
                            operation == 8 ? L"execute" : L"unknown";
    _snwprintf(buffer, buffer_count,
        L"exception_code=0x%08lx\r\n"
        L"exception_flags=0x%08lx\r\n"
        L"exception_thread_id=%lu\r\n"
        L"exception_address=0x%p\r\n"
        L"access_type=%ls\r\n"
        L"access_target=0x%llx\r\n"
        L"exception_parameters=%lu\r\n"
        L"RIP=0x%llx\r\nRSP=0x%llx\r\nRBP=0x%llx\r\n"
        L"RAX=0x%llx\r\nRBX=0x%llx\r\nRCX=0x%llx\r\nRDX=0x%llx\r\n"
        L"RSI=0x%llx\r\nRDI=0x%llx\r\nR8=0x%llx\r\nR9=0x%llx\r\n"
        L"R10=0x%llx\r\nR11=0x%llx\r\nR12=0x%llx\r\nR13=0x%llx\r\n"
        L"R14=0x%llx\r\nR15=0x%llx\r\n"
        L"exception_module=%ls\r\nexception_module_base=0x%llx\r\n"
        L"exception_module_offset=0x%llx\r\n"
        L"capture_index=%ld\r\nminidump_ok=%d\r\nminidump_error=%lu\r\n",
        record->ExceptionCode, record->ExceptionFlags, GetCurrentThreadId(),
        record->ExceptionAddress, access, (unsigned long long)target,
        record->NumberParameters,
        (unsigned long long)context->Rip, (unsigned long long)context->Rsp,
        (unsigned long long)context->Rbp, (unsigned long long)context->Rax,
        (unsigned long long)context->Rbx, (unsigned long long)context->Rcx,
        (unsigned long long)context->Rdx, (unsigned long long)context->Rsi,
        (unsigned long long)context->Rdi, (unsigned long long)context->R8,
        (unsigned long long)context->R9, (unsigned long long)context->R10,
        (unsigned long long)context->R11, (unsigned long long)context->R12,
        (unsigned long long)context->R13, (unsigned long long)context->R14,
        (unsigned long long)context->R15, module_path,
        (unsigned long long)module_base,
        (unsigned long long)(module_base ? address - module_base : 0),
        index, dump_ok, dump_error);
    buffer[buffer_count - 1] = L'\0';
}

static LONG CALLBACK capture_access_violation(EXCEPTION_POINTERS *exception) {
    if (!exception || !exception->ExceptionRecord || !exception->ContextRecord ||
        exception->ExceptionRecord->ExceptionCode != EXCEPTION_ACCESS_VIOLATION) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    if (InterlockedCompareExchange(&g_dumping, 1, 0) != 0) {
        return EXCEPTION_CONTINUE_SEARCH;
    }
    LONG index = InterlockedIncrement(&g_capture_count);
    if (index > MAX_CAPTURE_COUNT) {
        InterlockedExchange(&g_dumping, 0);
        return EXCEPTION_CONTINUE_SEARCH;
    }

    /* Preserve the original exception context. Unwinding uses only this copy. */
    CONTEXT saved_context __attribute__((aligned(16)));
    memcpy(&saved_context, exception->ContextRecord, sizeof(saved_context));
    capture_native_frames(&saved_context);

    wchar_t dump_path[MAX_CAPTURE_PATH];
    wchar_t metadata_path[MAX_CAPTURE_PATH];
    make_capture_path(dump_path, MAX_CAPTURE_PATH, index, L"");
    make_capture_path(metadata_path, MAX_CAPTURE_PATH, index, L".meta.txt");
    HANDLE file = CreateFileW(dump_path, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                              CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    BOOL dump_ok = FALSE;
    DWORD dump_error = ERROR_SUCCESS;
    if (file != INVALID_HANDLE_VALUE && g_write_dump) {
        MINIDUMP_EXCEPTION_INFORMATION exception_info;
        exception_info.ThreadId = GetCurrentThreadId();
        exception_info.ExceptionPointers = exception;
        exception_info.ClientPointers = FALSE; /* EXCEPTION_POINTERS is local to this process. */
        MINIDUMP_TYPE flags = (MINIDUMP_TYPE)(MiniDumpWithIndirectlyReferencedMemory |
            MiniDumpWithUnloadedModules | MiniDumpWithThreadInfo);
        dump_ok = g_write_dump(GetCurrentProcess(), GetCurrentProcessId(), file,
                               flags, &exception_info, NULL, NULL);
        if (!dump_ok) dump_error = GetLastError();
        else FlushFileBuffers(file);
        CloseHandle(file);
    } else {
        dump_error = GetLastError();
        if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    }

    snapshot_return_slots_after_dump();
    wchar_t metadata[4096];
    format_metadata(metadata, sizeof(metadata) / sizeof(metadata[0]), exception,
                    index, dump_ok, dump_error);
    write_utf8_text(metadata_path, metadata);
    append_unwind_metadata(metadata_path);
    InterlockedExchange(&g_dumping, 0);
    return EXCEPTION_CONTINUE_SEARCH;
}

__declspec(dllexport) int __cdecl install_capture(const wchar_t *dump_path) {
    if (!dump_path || !dump_path[0]) return 0;
    if (wcslen(dump_path) >= MAX_CAPTURE_PATH) return 0;
    if (g_handler) return 1;
    wcscpy(g_base_path, dump_path);

    /* Load DbgHelp before exception handling so it is not first-loaded under AV. */
    g_dbghelp = GetModuleHandleW(L"dbghelp.dll");
    if (!g_dbghelp) g_dbghelp = LoadLibraryW(L"dbghelp.dll");
    if (!g_dbghelp) return 0;
    FARPROC write_dump_symbol = GetProcAddress(g_dbghelp, "MiniDumpWriteDump");
    if (!write_dump_symbol || sizeof(write_dump_symbol) != sizeof(g_write_dump)) return 0;
    memcpy(&g_write_dump, &write_dump_symbol, sizeof(g_write_dump));

    g_ntdll = GetModuleHandleW(L"ntdll.dll");
    if (!g_ntdll) return 0;
    FARPROC lookup_symbol = GetProcAddress(g_ntdll, "RtlLookupFunctionEntry");
    FARPROC unwind_symbol = GetProcAddress(g_ntdll, "RtlVirtualUnwind");
    if (sizeof(lookup_symbol) != sizeof(g_lookup_function_entry) ||
        sizeof(unwind_symbol) != sizeof(g_virtual_unwind)) return 0;
    memcpy(&g_lookup_function_entry, &lookup_symbol,
           sizeof(g_lookup_function_entry));
    memcpy(&g_virtual_unwind, &unwind_symbol, sizeof(g_virtual_unwind));
    if (!g_lookup_function_entry || !g_virtual_unwind) return 0;

    g_handler = AddVectoredExceptionHandler(1, capture_access_violation);
    return g_handler ? 1 : 0;
}

__declspec(dllexport) int __cdecl capture_count(void) {
    return (int)g_capture_count;
}
