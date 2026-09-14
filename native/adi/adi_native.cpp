#include "elfloader.h"
#include <cstdio>
#include <cstdint>
#include <string>

// sysv abi pointers for the 11 obfuscated exports
typedef int (__attribute__((sysv_abi)) *fn_LoadLibraryWithPath)(const char*);
typedef int (__attribute__((sysv_abi)) *fn_SetProvisioningPath)(const char*);
typedef int (__attribute__((sysv_abi)) *fn_SetIdentifier)(const char*, uint32_t);
typedef int (__attribute__((sysv_abi)) *fn_Start)(uint64_t, uint8_t*, uint32_t, uint8_t**, uint32_t*, uint32_t*);
typedef int (__attribute__((sysv_abi)) *fn_End)(uint32_t, uint8_t*, uint32_t, uint8_t*, uint32_t);
typedef int (__attribute__((sysv_abi)) *fn_GetLoginCode)(uint64_t);
typedef int (__attribute__((sysv_abi)) *fn_RequestOTP)(uint64_t, uint8_t**, uint32_t*, uint8_t**, uint32_t*);
typedef int (__attribute__((sysv_abi)) *fn_Synchronize)(uint64_t, uint8_t*, uint32_t, uint8_t**, uint32_t*, uint8_t**, uint32_t*);
typedef int (__attribute__((sysv_abi)) *fn_Erase)(uint64_t);
typedef int (__attribute__((sysv_abi)) *fn_Destroy)(uint32_t);
typedef int (__attribute__((sysv_abi)) *fn_Dispose)(void*);

static fn_LoadLibraryWithPath g_pLoadLibraryWithPath = nullptr;
static fn_SetProvisioningPath g_pSetProvisioningPath = nullptr;
static fn_SetIdentifier       g_pSetIdentifier       = nullptr;
static fn_Start               g_pStart               = nullptr;
static fn_End                 g_pEnd                 = nullptr;
static fn_GetLoginCode        g_pGetLoginCode        = nullptr;
static fn_RequestOTP          g_pRequestOTP          = nullptr;
static fn_Synchronize         g_pSynchronize         = nullptr;
static fn_Erase               g_pErase               = nullptr;
static fn_Destroy             g_pDestroy             = nullptr;
static fn_Dispose             g_pDispose             = nullptr;

static ElfSharedObject* g_storeApi = nullptr;

extern "C" __declspec(dllexport) int adi_init(const char* dir_path) {
    if (!dir_path) {
        fprintf(stderr, "[ADI NATIVE] Invalid directory path\n");
        return -1;
    }

    g_storeApi = load_library_cached("libstoreapi.so", dir_path);
    if (!g_storeApi) {
        fprintf(stderr, "[ADI NATIVE] Failed to load libstoreapi.so from %s\n", dir_path);
        return -1;
    }

    // pull in any apple lib not already brought by DT_NEEDED
    const char* apple_libs[] = {
        "libCoreFP.so", "libCoreADI.so", "libCoreLSKD.so", "libFPDIFor3P.so"
    };
    for (const char* lib_name : apple_libs) {
        if (!find_loaded_library(lib_name)) {
            load_library_cached(lib_name, dir_path);
        }
    }

    auto resolve_export = [](const char* obfuscated_name, const char* readable_name) -> void* {
        void* ptr = g_storeApi->get_symbol(obfuscated_name);
        if (!ptr) {
            const char* apple_libs[] = {
                "libCoreADI.so", "libCoreFP.so", "libCoreLSKD.so", "libFPDIFor3P.so"
            };
            for (const char* lib_name : apple_libs) {
                ElfSharedObject* obj = find_loaded_library(lib_name);
                if (obj) {
                    ptr = obj->get_symbol(obfuscated_name);
                    if (ptr) break;
                }
            }
        }
        if (ptr) {
            printf("[ADI NATIVE] Resolved %-20s (\"%s\") -> 0x%p\n", readable_name, obfuscated_name, ptr);
        } else {
            fprintf(stderr, "[ADI NATIVE] FAILED to resolve %s (\"%s\")\n", readable_name, obfuscated_name);
        }
        return ptr;
    };

    int missing = 0;
    g_pLoadLibraryWithPath = (fn_LoadLibraryWithPath)resolve_export("kq56gsgHG6", "LoadLibraryWithPath");
    if (!g_pLoadLibraryWithPath) missing++;

    g_pSetProvisioningPath = (fn_SetProvisioningPath)resolve_export("nf92ngaK92", "SetProvisioningPath");
    if (!g_pSetProvisioningPath) missing++;

    g_pSetIdentifier       = (fn_SetIdentifier)resolve_export("Sph98paBcz", "SetIdentifier");
    if (!g_pSetIdentifier) missing++;

    g_pStart               = (fn_Start)resolve_export("rsegvyrt87", "Start");
    if (!g_pStart) missing++;

    g_pEnd                 = (fn_End)resolve_export("uv5t6nhkui", "End");
    if (!g_pEnd) missing++;

    g_pGetLoginCode        = (fn_GetLoginCode)resolve_export("aslgmuibau", "GetLoginCode");
    if (!g_pGetLoginCode) missing++;

    g_pRequestOTP          = (fn_RequestOTP)resolve_export("qi864985u0", "RequestOTP");
    if (!g_pRequestOTP) missing++;

    g_pSynchronize         = (fn_Synchronize)resolve_export("tn46gtiuhw", "Synchronize");
    if (!g_pSynchronize) missing++;

    g_pErase               = (fn_Erase)resolve_export("p435tmhbla", "Erase");
    if (!g_pErase) missing++;

    g_pDestroy             = (fn_Destroy)resolve_export("fy34trz2st", "Destroy");
    if (!g_pDestroy) missing++;

    g_pDispose             = (fn_Dispose)resolve_export("jk24uiwqrg", "Dispose");
    if (!g_pDispose) missing++;

    if (missing > 0) {
        fprintf(stderr, "[ADI NATIVE] Initialization incomplete: %d exports failed to resolve\n", missing);
        return -2;
    }

    printf("[ADI NATIVE] Initialization succeeded: all 11 exports resolved.\n");
    return 0;
}

// ms-abi wrappers for python ctypes

extern "C" __declspec(dllexport) int adi_LoadLibraryWithPath(const char* path) {
    if (!g_pLoadLibraryWithPath) return -1;
    return g_pLoadLibraryWithPath(path);
}

extern "C" __declspec(dllexport) int adi_SetProvisioningPath(const char* path) {
    if (!g_pSetProvisioningPath) return -1;
    return g_pSetProvisioningPath(path);
}

extern "C" __declspec(dllexport) int adi_SetIdentifier(const char* identifier, uint32_t len) {
    if (!g_pSetIdentifier) return -1;
    return g_pSetIdentifier(identifier, len);
}

extern "C" __declspec(dllexport) int adi_Start(uint64_t dsid, uint8_t* in, uint32_t in_len, uint8_t** out, uint32_t* out_len, uint32_t* session) {
    if (!g_pStart) return -1;
    return g_pStart(dsid, in, in_len, out, out_len, session);
}

extern "C" __declspec(dllexport) int adi_End(uint32_t session, uint8_t* in1, uint32_t in1_len, uint8_t* in2, uint32_t in2_len) {
    if (!g_pEnd) return -1;
    return g_pEnd(session, in1, in1_len, in2, in2_len);
}

extern "C" __declspec(dllexport) int adi_GetLoginCode(uint64_t dsid) {
    if (!g_pGetLoginCode) return -1;
    return g_pGetLoginCode(dsid);
}

extern "C" __declspec(dllexport) int adi_RequestOTP(uint64_t dsid, uint8_t** mid, uint32_t* mid_len, uint8_t** otp, uint32_t* otp_len) {
    if (!g_pRequestOTP) return -1;
    return g_pRequestOTP(dsid, mid, mid_len, otp, otp_len);
}

extern "C" __declspec(dllexport) int adi_Synchronize(uint64_t dsid, uint8_t* in, uint32_t in_len, uint8_t** mid, uint32_t* mid_len, uint8_t** srm, uint32_t* srm_len) {
    if (!g_pSynchronize) return -1;
    return g_pSynchronize(dsid, in, in_len, mid, mid_len, srm, srm_len);
}

extern "C" __declspec(dllexport) int adi_Erase(uint64_t dsid) {
    if (!g_pErase) return -1;
    return g_pErase(dsid);
}

extern "C" __declspec(dllexport) int adi_Destroy(uint32_t session) {
    if (!g_pDestroy) return -1;
    return g_pDestroy(session);
}

extern "C" __declspec(dllexport) int adi_Dispose(void* ptr) {
    if (!g_pDispose) return -1;
    return g_pDispose(ptr);
}

// diagnostics

extern "C" __declspec(dllexport) int adi_get_missing_symbols_count(void) {
    return (int)get_missing_symbols().size();
}

extern "C" __declspec(dllexport) const char* adi_get_missing_symbol(int index) {
    const auto& list = get_missing_symbols();
    if (index >= 0 && index < (int)list.size()) {
        return list[index].c_str();
    }
    return nullptr;
}

extern "C" __declspec(dllexport) int adi_get_stub_hit_count(void) {
    return get_stub_hit_count();
}
