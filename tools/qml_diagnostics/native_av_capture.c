#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dbghelp.h>
#include <stdint.h>
#include <stdio.h>
#include <wchar.h>

#define MAX_CAPTURE_PATH 2048
#define MAX_CAPTURE_COUNT 8

typedef BOOL (WINAPI *MiniDumpWriteDumpFn)(HANDLE, DWORD, HANDLE, MINIDUMP_TYPE,
    PMINIDUMP_EXCEPTION_INFORMATION, PMINIDUMP_USER_STREAM_INFORMATION,
    PMINIDUMP_CALLBACK_INFORMATION);

static HMODULE g_dbghelp;
static MiniDumpWriteDumpFn g_write_dump;
static PVOID g_handler;
static volatile LONG g_dumping;
static volatile LONG g_capture_count;
static wchar_t g_base_path[MAX_CAPTURE_PATH];

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

    wchar_t metadata[4096];
    format_metadata(metadata, sizeof(metadata) / sizeof(metadata[0]), exception,
                    index, dump_ok, dump_error);
    write_utf8_text(metadata_path, metadata);
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
    g_write_dump = (MiniDumpWriteDumpFn)GetProcAddress(g_dbghelp, "MiniDumpWriteDump");
    if (!g_write_dump) return 0;

    g_handler = AddVectoredExceptionHandler(1, capture_access_violation);
    return g_handler ? 1 : 0;
}

__declspec(dllexport) int __cdecl capture_count(void) {
    return (int)g_capture_count;
}
