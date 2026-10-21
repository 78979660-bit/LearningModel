#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

typedef int (__cdecl *install_capture_fn)(const wchar_t *dump_path);

static volatile int g_keep_call_frames;

__attribute__((noinline)) static void trigger_synthetic_access_violation(void) {
    volatile uintptr_t invalid_address = (uintptr_t)0x1;
    volatile uint32_t *invalid = (volatile uint32_t *)invalid_address;
    *invalid = 0x51A7;
}

__attribute__((noinline)) static void middle_frame(void) {
    trigger_synthetic_access_violation();
    g_keep_call_frames += 1;
}

__attribute__((noinline)) static void outer_frame(void) {
    middle_frame();
    g_keep_call_frames += 2;
}

int main(int argc, char **argv) {
    wchar_t dump_path[2048];
    const char *dump_path_arg = argc > 1
        ? argv[1] : "output\\qml-phase2-stack-veh-selfcheck.dmp";
    if (argc > 2 || MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS,
            dump_path_arg, -1, dump_path,
            (int)(sizeof(dump_path) / sizeof(dump_path[0]))) <= 0) {
        printf("usage: qml_veh_stack_selfcheck.exe [dump-base-path]\n");
        return 2;
    }
    HMODULE library = LoadLibraryW(L"output\\qml_av_veh_stack_capture.dll");
    if (!library) {
        printf("LoadLibraryW failed: %lu\n", GetLastError());
        return 2;
    }
    FARPROC install_symbol = GetProcAddress(library, "install_capture");
    install_capture_fn install = NULL;
    if (sizeof(install_symbol) != sizeof(install)) {
        printf("unexpected function pointer size\n");
        return 3;
    }
    memcpy(&install, &install_symbol, sizeof(install));
    if (!install) {
        printf("GetProcAddress failed: %lu\n", GetLastError());
        return 3;
    }
    int installed = install(dump_path);
    printf("capture_installed=%d\n", installed);
    fflush(stdout);
    if (!installed) return 4;

    outer_frame();
    return 0;
}
