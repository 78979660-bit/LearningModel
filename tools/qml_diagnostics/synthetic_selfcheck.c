#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdint.h>
#include <stdio.h>

typedef int (__cdecl *install_capture_fn)(const wchar_t *dump_path);

int main(void) {
    HMODULE library = LoadLibraryW(L"output\\qml_av_veh_capture.dll");
    if (!library) {
        printf("LoadLibraryW failed: %lu\n", GetLastError());
        return 2;
    }
    install_capture_fn install = (install_capture_fn)GetProcAddress(library, "install_capture");
    if (!install) {
        printf("GetProcAddress failed: %lu\n", GetLastError());
        return 3;
    }
    int installed = install(L"output\\qml-phase2-veh-selfcheck.dmp");
    printf("capture_installed=%d\n", installed);
    fflush(stdout);
    if (!installed) return 4;

    volatile uint32_t *invalid = (volatile uint32_t *)(uintptr_t)0x1;
    *invalid = 0x51A7;
    return 0;
}
