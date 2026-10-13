#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <dbgeng.h>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cwchar>

// Offline only: no attach/launch interface, remote symbols or dump mutation.
class DumpOutput final : public IDebugOutputCallbacks {
    LONG references = 1;
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void **out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (IsEqualIID(iid, __uuidof(IUnknown)) ||
            IsEqualIID(iid, __uuidof(IDebugOutputCallbacks))) {
            *out = this;
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&references); }
    ULONG STDMETHODCALLTYPE Release() override { return InterlockedDecrement(&references); }
    HRESULT STDMETHODCALLTYPE Output(ULONG, PCSTR text) override {
        std::fputs(text, stdout);
        std::fflush(stdout);
        return S_OK;
    }
};

int wmain(int argc, wchar_t **argv) {
    if (argc != 2 && argc != 5) {
        std::fprintf(stderr, "usage: inspect_dump.exe <existing dump> [frame-decimal storage-hex code-hex]\n");
        return 2;
    }
    unsigned long long values[3] = {};
    if (argc == 5) {
        for (int i = 0; i < 3; ++i) {
            wchar_t *end = nullptr;
            errno = 0;
            values[i] = std::wcstoull(argv[i + 2], &end, i == 0 ? 10 : 16);
            if (errno || end == argv[i + 2] || *end || (i == 0 && values[i] > 255)) {
                std::fprintf(stderr, "invalid numeric inspection argument\n");
                return 2;
            }
        }
    }
    char commands[512] = ".ecxr; r; kP 40; lmv m Qt6Qml";
    if (argc == 5)
        std::snprintf(commands, sizeof(commands),
            ".ecxr; r; kP 40; lmv m Qt6Qml; .frame /r %llu; "
            "dq %016llx L8; u %016llx L40", values[0], values[1], values[2]);
    IDebugClient4 *client = nullptr;
    IDebugControl *control = nullptr;
    IDebugSymbols *symbols = nullptr;
    DumpOutput output;
    HRESULT result = DebugCreate(__uuidof(IDebugClient4), reinterpret_cast<void **>(&client));
    if (FAILED(result)) {
        std::fprintf(stderr, "DebugCreate: 0x%08lx\n", static_cast<unsigned long>(result));
        return 1;
    }
    client->SetOutputCallbacks(&output);
    result = client->QueryInterface(__uuidof(IDebugControl), reinterpret_cast<void **>(&control));
    if (SUCCEEDED(result))
        result = client->QueryInterface(__uuidof(IDebugSymbols), reinterpret_cast<void **>(&symbols));
    if (SUCCEEDED(result)) result = symbols->SetSymbolPath(".");
    if (SUCCEEDED(result)) result = client->OpenDumpFileWide(argv[1], 0);
    if (SUCCEEDED(result)) result = control->WaitForEvent(DEBUG_WAIT_DEFAULT, 10000);
    if (SUCCEEDED(result))
        result = control->Execute(DEBUG_OUTCTL_THIS_CLIENT, commands, DEBUG_EXECUTE_DEFAULT);
    std::fprintf(stderr, "inspection_hresult=0x%08lx\n", static_cast<unsigned long>(result));
    if (symbols) symbols->Release();
    if (control) control->Release();
    client->SetOutputCallbacks(nullptr);
    client->Release();
    return FAILED(result) ? 1 : 0;
}
