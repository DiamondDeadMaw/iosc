#include "elfloader.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <fcntl.h>
#include <io.h>
#include <sys/stat.h>
#include <direct.h>
#include <wincrypt.h>
#include <iostream>
#include <fstream>
#include <algorithm>

static std::vector<ElfSharedObject*> g_loaded_libraries;
static std::vector<std::string> g_missing_symbols;
static int g_missing_stub_hits = 0;

struct StubMemory {
    uint8_t* memory;
    size_t capacity;
    size_t used;

    StubMemory() : memory(nullptr), capacity(131072), used(0) {
        memory = (uint8_t*)VirtualAlloc(nullptr, capacity, MEM_RESERVE | MEM_COMMIT, PAGE_EXECUTE_READWRITE);
    }
    ~StubMemory() {
        if (memory) {
            VirtualFree(memory, 0, MEM_RELEASE);
        }
    }
};
static StubMemory g_stub_mem;

extern "C" __attribute__((sysv_abi)) int64_t on_missing_stub_called(const char* name) {
    fprintf(stderr, "[ELF LOADER STUB HIT] Unimplemented symbol invoked: %s\n", name ? name : "<unknown>");
    g_missing_stub_hits++;
    return 0;
}

void* get_or_create_logging_stub(const char* name) {
    if (std::find(g_missing_symbols.begin(), g_missing_symbols.end(), name) == g_missing_symbols.end()) {
        g_missing_symbols.push_back(name);
    }

    if (!g_stub_mem.memory) {
        return nullptr;
    }

    size_t name_len = strlen(name) + 1;
    size_t stub_size = 32 + name_len;
    stub_size = (stub_size + 15) & ~15;

    if (g_stub_mem.used + stub_size > g_stub_mem.capacity) {
        return (void*)on_missing_stub_called;
    }

    uint8_t* stub_addr = g_stub_mem.memory + g_stub_mem.used;
    char* name_storage = (char*)(stub_addr + 24);
    memcpy(name_storage, name, name_len);

    // x86_64 sysv thunk
    // movabs rdi, name_storage
    // movabs rax, on_missing_stub_called
    // jmp rax
    uint8_t* p = stub_addr;
    p[0] = 0x48; p[1] = 0xbf;
    *(uint64_t*)(p + 2) = (uint64_t)name_storage;
    p[10] = 0x48; p[11] = 0xb8;
    *(uint64_t*)(p + 12) = (uint64_t)on_missing_stub_called;
    p[20] = 0xff; p[21] = 0xe0;

    g_stub_mem.used += stub_size;
    return (void*)stub_addr;
}

int get_stub_hit_count() {
    return g_missing_stub_hits;
}

const std::vector<std::string>& get_missing_symbols() {
    return g_missing_symbols;
}

ElfSharedObject* find_loaded_library(const std::string& name) {
    for (auto* lib : g_loaded_libraries) {
        if (lib->get_soname() == name) {
            return lib;
        }
    }
    return nullptr;
}

void register_loaded_library(ElfSharedObject* obj) {
    for (auto* lib : g_loaded_libraries) {
        if (lib == obj) return;
    }
    g_loaded_libraries.push_back(obj);
}

ElfSharedObject* load_library_cached(const std::string& lib_name, const std::string& dir) {
    ElfSharedObject* existing = find_loaded_library(lib_name);
    if (existing) {
        return existing;
    }

    std::string full_path = dir;
    if (!full_path.empty() && full_path.back() != '/' && full_path.back() != '\\') {
        full_path += "\\";
    }
    full_path += lib_name;

    ElfSharedObject* new_lib = new ElfSharedObject();
    new_lib->set_soname(lib_name);
    register_loaded_library(new_lib);

    if (!new_lib->load(full_path, dir)) {
        return nullptr;
    }
    return new_lib;
}

static std::string strip_version_tag(const char* sym_name) {
    if (!sym_name) return "";
    const char* at = strchr(sym_name, '@');
    if (at) {
        return std::string(sym_name, at - sym_name);
    }
    return std::string(sym_name);
}

// module 3, libc and posix shims

static std::string to_windows_path(const char* path) {
    if (!path) return "";
    std::string s(path);
    if (s.rfind("//?/", 0) == 0) {
        s = s.substr(4);
    }
    for (char& c : s) {
        if (c == '/') c = '\\';
    }
    return s;
}

static thread_local int s_tl_errno = 0;

extern "C" __attribute__((sysv_abi)) int* shim_errno() {
    return &s_tl_errno;
}

extern "C" __attribute__((sysv_abi)) void* shim_malloc(size_t size) {
    return ::malloc(size);
}

extern "C" __attribute__((sysv_abi)) void shim_free(void* ptr) {
    ::free(ptr);
}

extern "C" __attribute__((sysv_abi)) void* shim_calloc(size_t nmemb, size_t size) {
    return ::calloc(nmemb, size);
}

extern "C" __attribute__((sysv_abi)) void* shim_realloc(void* ptr, size_t size) {
    return ::realloc(ptr, size);
}

extern "C" __attribute__((sysv_abi)) int shim_posix_memalign(void** memptr, size_t alignment, size_t size) {
    if (!memptr) return 22; // EINVAL
    void* p = _aligned_malloc(size, alignment);
    if (!p) return 12; // ENOMEM
    *memptr = p;
    return 0;
}

extern "C" __attribute__((sysv_abi)) void* shim_memcpy(void* dest, const void* src, size_t n) {
    return ::memcpy(dest, src, n);
}

extern "C" __attribute__((sysv_abi)) void* shim_memmove(void* dest, const void* src, size_t n) {
    return ::memmove(dest, src, n);
}

extern "C" __attribute__((sysv_abi)) void* shim_memset(void* s, int c, size_t n) {
    return ::memset(s, c, n);
}

extern "C" __attribute__((sysv_abi)) int shim_memcmp(const void* s1, const void* s2, size_t n) {
    return ::memcmp(s1, s2, n);
}

extern "C" __attribute__((sysv_abi)) void* shim_memchr(const void* s, int c, size_t n) {
    return (void*)::memchr(s, c, n);
}

extern "C" __attribute__((sysv_abi)) size_t shim_strlen(const char* s) {
    return ::strlen(s);
}

extern "C" __attribute__((sysv_abi)) int shim_strcmp(const char* s1, const char* s2) {
    return ::strcmp(s1, s2);
}

extern "C" __attribute__((sysv_abi)) int shim_strncmp(const char* s1, const char* s2, size_t n) {
    return ::strncmp(s1, s2, n);
}

extern "C" __attribute__((sysv_abi)) char* shim_strcpy(char* dest, const char* src) {
    return ::strcpy(dest, src);
}

extern "C" __attribute__((sysv_abi)) char* shim_strncpy(char* dest, const char* src, size_t n) {
    return ::strncpy(dest, src, n);
}

extern "C" __attribute__((sysv_abi)) char* shim_strcat(char* dest, const char* src) {
    return ::strcat(dest, src);
}

extern "C" __attribute__((sysv_abi)) char* shim_strncat(char* dest, const char* src, size_t n) {
    return ::strncat(dest, src, n);
}

extern "C" __attribute__((sysv_abi)) char* shim_strchr(const char* s, int c) {
    return (char*)::strchr(s, c);
}

extern "C" __attribute__((sysv_abi)) char* shim_strrchr(const char* s, int c) {
    return (char*)::strrchr(s, c);
}

extern "C" __attribute__((sysv_abi)) char* shim_strstr(const char* haystack, const char* needle) {
    return (char*)::strstr(haystack, needle);
}

extern "C" __attribute__((sysv_abi)) char* shim_strerror(int errnum) {
    return ::strerror(errnum);
}

extern "C" __attribute__((sysv_abi)) int shim_atoi(const char* nptr) {
    return ::atoi(nptr);
}

extern "C" __attribute__((sysv_abi)) long long shim_atoll(const char* nptr) {
    return ::atoll(nptr);
}

extern "C" __attribute__((sysv_abi)) void shim_abort() {
    ::abort();
}

extern "C" __attribute__((sysv_abi)) void shim_stack_chk_fail() {
    fprintf(stderr, "[ELF LOADER FATAL] Stack smashing detected (__stack_chk_fail)\n");
    ::abort();
}

extern "C" __attribute__((sysv_abi)) int shim_cxa_atexit(void (*func)(void*), void* arg, void* dso_handle) {
    (void)func; (void)arg; (void)dso_handle;
    return 0;
}

extern "C" __attribute__((sysv_abi)) void shim_cxa_finalize(void* dso_handle) {
    (void)dso_handle;
}

extern "C" __attribute__((sysv_abi)) int shim_register_atfork(void (*prepare)(void), void (*parent)(void), void (*child)(void)) {
    (void)prepare; (void)parent; (void)child;
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_getpagesize() {
    return 4096;
}

extern "C" __attribute__((sysv_abi)) long shim_sysconf(int name) {
    (void)name;
    return 4096;
}

extern "C" __attribute__((sysv_abi)) int shim_open(const char* pathname, int flags, ...) {
    std::string win_path = to_windows_path(pathname);

    int win_flags = _O_BINARY;
    int acc = flags & 3;
    if (acc == 1) win_flags |= _O_WRONLY;
    else if (acc == 2) win_flags |= _O_RDWR;
    else win_flags |= _O_RDONLY;

    if (flags & 0100)  win_flags |= _O_CREAT;
    if (flags & 0200)  win_flags |= _O_EXCL;
    if (flags & 01000) win_flags |= _O_TRUNC;
    if (flags & 02000) win_flags |= _O_APPEND;

    int pmode = _S_IREAD | _S_IWRITE;
    int fd = _open(win_path.c_str(), win_flags, pmode);
    if (fd < 0) {
        s_tl_errno = errno;
    }
    return fd;
}

extern "C" __attribute__((sysv_abi)) int shim_close(int fd) {
    int ret = _close(fd);
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" __attribute__((sysv_abi)) int64_t shim_read(int fd, void* buf, size_t count) {
    int ret = _read(fd, buf, (unsigned int)count);
    if (ret < 0) {
        s_tl_errno = errno;
        return -1;
    }
    return ret;
}

extern "C" __attribute__((sysv_abi)) int64_t shim_write(int fd, const void* buf, size_t count) {
    int ret = _write(fd, buf, (unsigned int)count);
    if (ret < 0) {
        s_tl_errno = errno;
        return -1;
    }
    return ret;
}

extern "C" __attribute__((sysv_abi)) int shim_ftruncate(int fd, int64_t length) {
    int ret = _chsize(fd, (long)length);
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" __attribute__((sysv_abi)) int shim_mkdir(const char* pathname, int mode) {
    (void)mode;
    std::string win_path = to_windows_path(pathname);
    int ret = _mkdir(win_path.c_str());
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" __attribute__((sysv_abi)) int shim_chmod(const char* pathname, int mode) {
    std::string win_path = to_windows_path(pathname);
    int win_mode = 0;
    if (mode & 0400) win_mode |= _S_IREAD;
    if (mode & 0200) win_mode |= _S_IWRITE;
    int ret = _chmod(win_path.c_str(), win_mode);
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" __attribute__((sysv_abi)) int shim_umask(int mask) {
    return _umask(mask);
}

static void fill_linux_stat(const struct __stat64& win_st, struct linux_stat64* st) {
    memset(st, 0, sizeof(*st));
    st->st_dev = win_st.st_dev;
    st->st_ino = win_st.st_ino;
    st->st_nlink = win_st.st_nlink ? win_st.st_nlink : 1;

    uint32_t mode = 0555;
    if (win_st.st_mode & _S_IFDIR) {
        mode |= 0040000;
    } else {
        mode |= 0100000;
    }
    if (win_st.st_mode & _S_IWRITE) {
        mode |= 0200;
    }
    st->st_mode = mode;

    st->st_uid = win_st.st_uid;
    st->st_gid = win_st.st_gid;
    st->st_rdev = win_st.st_rdev;
    st->st_size = win_st.st_size;
    st->st_blksize = 4096;
    st->st_blocks = (win_st.st_size + 511) / 512;
    st->st_atime = win_st.st_atime;
    st->st_mtime = win_st.st_mtime;
    st->st_ctime = win_st.st_ctime;
}

extern "C" __attribute__((sysv_abi)) int shim_fstat(int fd, struct linux_stat64* buf) {
    if (!buf) {
        s_tl_errno = 22; // EINVAL
        return -1;
    }
    struct __stat64 win_st;
    int ret = _fstat64(fd, &win_st);
    if (ret < 0) {
        s_tl_errno = errno;
        return -1;
    }
    fill_linux_stat(win_st, buf);
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_lstat(const char* path, struct linux_stat64* buf) {
    if (!path || !buf) {
        s_tl_errno = 22; // EINVAL
        return -1;
    }
    std::string win_path = to_windows_path(path);
    struct __stat64 win_st;
    int ret = _stat64(win_path.c_str(), &win_st);
    if (ret < 0) {
        s_tl_errno = errno;
        return -1;
    }
    fill_linux_stat(win_st, buf);
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_gettimeofday(struct linux_timeval* tv, void* tz) {
    (void)tz;
    if (!tv) return -1;
    FILETIME ft;
    GetSystemTimeAsFileTime(&ft);
    uint64_t t = ((uint64_t)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    const uint64_t EPOCH_BIAS = 116444736000000000ULL;
    if (t >= EPOCH_BIAS) {
        t -= EPOCH_BIAS;
    }
    tv->tv_sec = (int64_t)(t / 10000000ULL);
    tv->tv_usec = (int64_t)((t % 10000000ULL) / 10ULL);
    return 0;
}

extern "C" __attribute__((sysv_abi)) uint32_t shim_arc4random() {
    uint32_t val = 0;
    HCRYPTPROV hProv = 0;
    if (CryptAcquireContextW(&hProv, NULL, NULL, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
        CryptGenRandom(hProv, sizeof(val), (BYTE*)&val);
        CryptReleaseContext(hProv, 0);
        return val;
    }
    return (uint32_t)GetTickCount64();
}

extern "C" __attribute__((sysv_abi)) int shim_system_property_get(const char* name, char* value) {
    (void)name;
    const char* default_val = "000000000000";
    if (value) {
        strcpy(value, default_val);
    }
    return (int)strlen(default_val);
}

extern "C" __attribute__((sysv_abi)) void* shim_dlopen(const char* filename, int flags) {
    (void)flags;
    if (!filename) {
        return (void*)1;
    }
    std::string name(filename);
    size_t slash = name.find_last_of("/\\");
    if (slash != std::string::npos) {
        name = name.substr(slash + 1);
    }

    ElfSharedObject* obj = find_loaded_library(name);
    if (obj) {
        return obj;
    }
    return (void*)1;
}

extern "C" __attribute__((sysv_abi)) void* shim_dlsym(void* handle, const char* symbol) {
    if (!symbol) return nullptr;

    if (handle && handle != (void*)1) {
        ElfSharedObject* obj = (ElfSharedObject*)handle;
        void* sym = obj->get_symbol(symbol);
        if (sym) return sym;
    }

    for (auto* lib : g_loaded_libraries) {
        void* sym = lib->get_symbol(symbol);
        if (sym) return sym;
    }

    void* shim = lookup_shim(symbol);
    if (shim) return shim;

    return nullptr;
}

extern "C" __attribute__((sysv_abi)) int shim_dlclose(void* handle) {
    (void)handle;
    return 0;
}

extern "C" __attribute__((sysv_abi)) void* shim_dlerror() {
    return nullptr;
}

extern "C" __attribute__((sysv_abi)) int shim_dl_iterate_phdr(void* callback, void* data) {
    (void)callback; (void)data;
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_empty_stub() {
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_pthread_once(int* once_control, void (__attribute__((sysv_abi)) *init_routine)(void)) {
    if (once_control && init_routine) {
        if (*once_control == 0) {
            *once_control = 1;
            init_routine();
        }
    }
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_pthread_key_create(uint32_t* key, void (*destructor)(void*)) {
    (void)destructor;
    DWORD k = TlsAlloc();
    if (k == TLS_OUT_OF_INDEXES) return 11;
    if (key) *key = (uint32_t)k;
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_pthread_key_delete(uint32_t key) {
    return TlsFree((DWORD)key) ? 0 : 22;
}

extern "C" __attribute__((sysv_abi)) int shim_pthread_setspecific(uint32_t key, const void* value) {
    return TlsSetValue((DWORD)key, (LPVOID)value) ? 0 : 22;
}

extern "C" __attribute__((sysv_abi)) void* shim_pthread_getspecific(uint32_t key) {
    return TlsGetValue((DWORD)key);
}

extern "C" __attribute__((sysv_abi)) uint64_t shim_pthread_self() {
    return (uint64_t)GetCurrentThreadId();
}

extern "C" __attribute__((sysv_abi)) int shim_pthread_equal(uint64_t t1, uint64_t t2) {
    return t1 == t2;
}

extern "C" __attribute__((sysv_abi)) int shim_android_log_print(int prio, const char* tag, const char* fmt, ...) {
    (void)prio; (void)tag; (void)fmt;
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_android_log_write(int prio, const char* tag, const char* text) {
    (void)prio; (void)tag; (void)text;
    return 0;
}

extern "C" __attribute__((sysv_abi)) int shim_android_log_vprint(int prio, const char* tag, const char* fmt, va_list ap) {
    (void)prio; (void)tag; (void)fmt; (void)ap;
    return 0;
}

static uint8_t s_bionic_sF[1024];

struct ShimEntry {
    const char* name;
    void* func;
};

static const ShimEntry g_shims[] = {
    {"open",                   (void*)shim_open},
    {"close",                  (void*)shim_close},
    {"read",                   (void*)shim_read},
    {"write",                  (void*)shim_write},
    {"lstat",                  (void*)shim_lstat},
    {"fstat",                  (void*)shim_fstat},
    {"ftruncate",              (void*)shim_ftruncate},
    {"mkdir",                  (void*)shim_mkdir},
    {"chmod",                  (void*)shim_chmod},
    {"umask",                  (void*)shim_umask},
    {"malloc",                 (void*)shim_malloc},
    {"free",                   (void*)shim_free},
    {"calloc",                 (void*)shim_calloc},
    {"realloc",                (void*)shim_realloc},
    {"posix_memalign",         (void*)shim_posix_memalign},
    {"memcpy",                 (void*)shim_memcpy},
    {"memmove",                (void*)shim_memmove},
    {"memset",                 (void*)shim_memset},
    {"memcmp",                 (void*)shim_memcmp},
    {"memchr",                 (void*)shim_memchr},
    {"strlen",                 (void*)shim_strlen},
    {"strcmp",                 (void*)shim_strcmp},
    {"strncmp",                (void*)shim_strncmp},
    {"strcpy",                 (void*)shim_strcpy},
    {"strncpy",                (void*)shim_strncpy},
    {"strcat",                 (void*)shim_strcat},
    {"strncat",                (void*)shim_strncat},
    {"strchr",                 (void*)shim_strchr},
    {"strrchr",                (void*)shim_strrchr},
    {"strstr",                 (void*)shim_strstr},
    {"strerror",               (void*)shim_strerror},
    {"atoi",                   (void*)shim_atoi},
    {"atoll",                  (void*)shim_atoll},
    {"abort",                  (void*)shim_abort},
    {"__stack_chk_fail",       (void*)shim_stack_chk_fail},
    {"__cxa_atexit",           (void*)shim_cxa_atexit},
    {"__cxa_finalize",         (void*)shim_cxa_finalize},
    {"__register_atfork",      (void*)shim_register_atfork},
    {"getpagesize",            (void*)shim_getpagesize},
    {"sysconf",                (void*)shim_sysconf},
    {"__sF",                   (void*)s_bionic_sF},
    {"__errno",                (void*)shim_errno},
    {"__errno_location",       (void*)shim_errno},
    {"gettimeofday",           (void*)shim_gettimeofday},
    {"arc4random",             (void*)shim_arc4random},
    {"__system_property_get",  (void*)shim_system_property_get},
    {"dlopen",                 (void*)shim_dlopen},
    {"dlsym",                  (void*)shim_dlsym},
    {"dlclose",                (void*)shim_dlclose},
    {"dlerror",                (void*)shim_dlerror},
    {"dl_iterate_phdr",        (void*)shim_dl_iterate_phdr},
    {"pthread_once",           (void*)shim_pthread_once},
    {"pthread_create",         (void*)shim_empty_stub},
    {"pthread_mutex_lock",     (void*)shim_empty_stub},
    {"pthread_mutex_unlock",   (void*)shim_empty_stub},
    {"pthread_rwlock_init",    (void*)shim_empty_stub},
    {"pthread_rwlock_destroy", (void*)shim_empty_stub},
    {"pthread_rwlock_rdlock",  (void*)shim_empty_stub},
    {"pthread_rwlock_wrlock",  (void*)shim_empty_stub},
    {"pthread_rwlock_unlock",  (void*)shim_empty_stub},
    {"pthread_cond_init",      (void*)shim_empty_stub},
    {"pthread_cond_destroy",   (void*)shim_empty_stub},
    {"pthread_cond_broadcast", (void*)shim_empty_stub},
    {"pthread_cond_signal",    (void*)shim_empty_stub},
    {"pthread_cond_wait",      (void*)shim_empty_stub},
    {"pthread_key_create",     (void*)shim_pthread_key_create},
    {"pthread_key_delete",     (void*)shim_pthread_key_delete},
    {"pthread_setspecific",    (void*)shim_pthread_setspecific},
    {"pthread_getspecific",    (void*)shim_pthread_getspecific},
    {"pthread_self",           (void*)shim_pthread_self},
    {"pthread_equal",          (void*)shim_pthread_equal},
    {"__android_log_print",    (void*)shim_android_log_print},
    {"__android_log_write",    (void*)shim_android_log_write},
    {"__android_log_vprint",   (void*)shim_android_log_vprint},
    {nullptr,                  nullptr}
};

void* lookup_shim(const char* name) {
    if (!name) return nullptr;

    for (int i = 0; g_shims[i].name != nullptr; ++i) {
        if (strcmp(g_shims[i].name, name) == 0) {
            return g_shims[i].func;
        }
    }

    std::string base = strip_version_tag(name);
    if (base != name) {
        for (int i = 0; g_shims[i].name != nullptr; ++i) {
            if (strcmp(g_shims[i].name, base.c_str()) == 0) {
                return g_shims[i].func;
            }
        }
    }

    return nullptr;
}

// module 2, elf shared object loader

ElfSharedObject::ElfSharedObject()
    : base_address(nullptr),
      total_span(0),
      symtab(nullptr),
      strtab(nullptr),
      strtab_size(0),
      sym_count(0),
      rela_dyn(nullptr),
      rela_dyn_count(0),
      rela_plt(nullptr),
      rela_plt_count(0),
      init_func_offset(0),
      init_array(nullptr),
      init_array_count(0) {}

ElfSharedObject::~ElfSharedObject() {
    if (base_address) {
        VirtualFree(base_address, 0, MEM_RELEASE);
        base_address = nullptr;
    }
}

bool ElfSharedObject::load(const std::string& filepath, const std::string& search_dir) {
    library_dir = search_dir;
    filename = filepath;

    std::ifstream file(filepath, std::ios::binary | std::ios::ate);
    if (!file.is_open()) {
        fprintf(stderr, "[ELF LOADER] Failed to open ELF file: %s\n", filepath.c_str());
        return false;
    }

    std::streamsize file_size = file.tellg();
    file.seekg(0, std::ios::beg);

    std::vector<uint8_t> file_data(file_size);
    if (!file.read((char*)file_data.data(), file_size)) {
        fprintf(stderr, "[ELF LOADER] Failed to read ELF file: %s\n", filepath.c_str());
        return false;
    }

    if (!parse_elf(file_data)) {
        return false;
    }

    // Process DT_NEEDED entries
    for (const auto& needed : needed_libs) {
        printf("[ELF LOADER] %s requires DT_NEEDED: %s\n", soname.c_str(), needed.c_str());
        if (needed == "libCoreFP.so" || needed == "libCoreADI.so" ||
            needed == "libCoreLSKD.so" || needed == "libFPDIFor3P.so") {
            ElfSharedObject* dep = load_library_cached(needed, library_dir);
            if (!dep) {
                fprintf(stderr, "[ELF LOADER] Failed to recursively load DT_NEEDED dependency: %s\n", needed.c_str());
                return false;
            }
        }
    }

    if (!apply_relocations()) {
        return false;
    }

    if (!set_page_protections()) {
        return false;
    }

    run_initializers();

    return true;
}

bool ElfSharedObject::parse_elf(const std::vector<uint8_t>& file_data) {
    if (file_data.size() < sizeof(Elf64_Ehdr)) {
        fprintf(stderr, "[ELF LOADER] File too small for ELF header\n");
        return false;
    }

    const Elf64_Ehdr* ehdr = (const Elf64_Ehdr*)file_data.data();
    if (memcmp(ehdr->e_ident, ELFMAG, SELFMAG) != 0) {
        fprintf(stderr, "[ELF LOADER] Invalid ELF magic\n");
        return false;
    }

    if (ehdr->e_ident[EI_CLASS] != ELFCLASS64) {
        fprintf(stderr, "[ELF LOADER] Not a 64-bit ELF\n");
        return false;
    }

    if (ehdr->e_ident[EI_DATA] != ELFDATA2LSB) {
        fprintf(stderr, "[ELF LOADER] Not little-endian ELF\n");
        return false;
    }

    if (ehdr->e_machine != EM_X86_64) {
        fprintf(stderr, "[ELF LOADER] Machine architecture is not x86_64\n");
        return false;
    }

    if (ehdr->e_type != ET_DYN) {
        fprintf(stderr, "[ELF LOADER] Object is not ET_DYN shared library\n");
        return false;
    }

    // Compute span from PT_LOAD program headers
    const Elf64_Phdr* phdrs = (const Elf64_Phdr*)(file_data.data() + ehdr->e_phoff);
    uint64_t min_vaddr = UINT64_MAX;
    uint64_t max_vaddr = 0;
    const Elf64_Phdr* dynamic_phdr = nullptr;

    for (Elf64_Half i = 0; i < ehdr->e_phnum; ++i) {
        const Elf64_Phdr& phdr = phdrs[i];
        if (phdr.p_type == PT_LOAD) {
            if (phdr.p_vaddr < min_vaddr) {
                min_vaddr = phdr.p_vaddr;
            }
            uint64_t end_vaddr = phdr.p_vaddr + phdr.p_memsz;
            if (end_vaddr > max_vaddr) {
                max_vaddr = end_vaddr;
            }
            load_segments.push_back(phdr);
        } else if (phdr.p_type == PT_DYNAMIC) {
            dynamic_phdr = &phdr;
        }
    }

    if (load_segments.empty()) {
        fprintf(stderr, "[ELF LOADER] No PT_LOAD segments found\n");
        return false;
    }

    uint64_t aligned_min = min_vaddr & ~0xfffULL;
    uint64_t aligned_max = (max_vaddr + 0xfffULL) & ~0xfffULL;
    total_span = (size_t)(aligned_max - aligned_min);

    base_address = (uint8_t*)VirtualAlloc(nullptr, total_span, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!base_address) {
        fprintf(stderr, "[ELF LOADER] VirtualAlloc failed to allocate span: %zu bytes\n", total_span);
        return false;
    }

    // Map each PT_LOAD segment
    for (const auto& phdr : load_segments) {
        uint8_t* dest = base_address + phdr.p_vaddr;
        if (phdr.p_filesz > 0) {
            memcpy(dest, file_data.data() + phdr.p_offset, phdr.p_filesz);
        }
        if (phdr.p_memsz > phdr.p_filesz) {
            memset(dest + phdr.p_filesz, 0, phdr.p_memsz - phdr.p_filesz);
        }
    }

    // Parse PT_DYNAMIC
    if (!dynamic_phdr) {
        fprintf(stderr, "[ELF LOADER] Missing PT_DYNAMIC segment\n");
        return false;
    }

    const Elf64_Dyn* dyn = (const Elf64_Dyn*)(base_address + dynamic_phdr->p_vaddr);
    for (; dyn->d_tag != DT_NULL; ++dyn) {
        switch (dyn->d_tag) {
            case DT_SYMTAB:
                symtab = (const Elf64_Sym*)(base_address + dyn->d_un.d_ptr);
                break;
            case DT_STRTAB:
                strtab = (const char*)(base_address + dyn->d_un.d_ptr);
                break;
            case DT_STRSZ:
                strtab_size = dyn->d_un.d_val;
                break;
            case DT_RELA:
                rela_dyn = (const Elf64_Rela*)(base_address + dyn->d_un.d_ptr);
                break;
            case DT_RELASZ:
                rela_dyn_count = dyn->d_un.d_val / sizeof(Elf64_Rela);
                break;
            case DT_JMPREL:
                rela_plt = (const Elf64_Rela*)(base_address + dyn->d_un.d_ptr);
                break;
            case DT_PLTRELSZ:
                rela_plt_count = dyn->d_un.d_val / sizeof(Elf64_Rela);
                break;
            case DT_INIT:
                init_func_offset = dyn->d_un.d_ptr;
                break;
            case DT_INIT_ARRAY:
                init_array = (const uint64_t*)(base_address + dyn->d_un.d_ptr);
                break;
            case DT_INIT_ARRAYSZ:
                init_array_count = dyn->d_un.d_val / sizeof(uint64_t);
                break;
            case DT_NEEDED:
                break;
            default:
                break;
        }
    }

    if (!strtab || !symtab) {
        fprintf(stderr, "[ELF LOADER] Missing DT_SYMTAB or DT_STRTAB\n");
        return false;
    }

    // collect DT_NEEDED and DT_SONAME strings
    dyn = (const Elf64_Dyn*)(base_address + dynamic_phdr->p_vaddr);
    for (; dyn->d_tag != DT_NULL; ++dyn) {
        if (dyn->d_tag == DT_NEEDED) {
            if (dyn->d_un.d_val < strtab_size) {
                needed_libs.push_back(strtab + dyn->d_un.d_val);
            }
        } else if (dyn->d_tag == DT_SONAME) {
            if (dyn->d_un.d_val < strtab_size) {
                soname = strtab + dyn->d_un.d_val;
            }
        }
    }
    if (soname.empty()) {
        size_t slash = filename.find_last_of("/\\");
        soname = (slash != std::string::npos) ? filename.substr(slash + 1) : filename;
    }

    // Determine symbol count
    if (ehdr->e_shoff != 0 && ehdr->e_shnum > 0) {
        const Elf64_Shdr* shdrs = (const Elf64_Shdr*)(file_data.data() + ehdr->e_shoff);
        for (Elf64_Half i = 0; i < ehdr->e_shnum; ++i) {
            if (shdrs[i].sh_type == 11 /* SHT_DYNSYM */) {
                sym_count = shdrs[i].sh_size / sizeof(Elf64_Sym);
                break;
            }
        }
    }

    if (sym_count == 0 && (uintptr_t)strtab > (uintptr_t)symtab) {
        sym_count = ((uintptr_t)strtab - (uintptr_t)symtab) / sizeof(Elf64_Sym);
    }
    if (sym_count == 0) {
        sym_count = 65536;
    }

    return true;
}

void* ElfSharedObject::resolve_symbol(const char* sym_name) {
    if (!sym_name || sym_name[0] == '\0') {
        return nullptr;
    }

    // 1. real apple libs first
    for (auto* lib : g_loaded_libraries) {
        if (lib == this) continue;
        void* sym = lib->get_symbol(sym_name);
        if (sym) {
            return sym;
        }
    }
    std::string stripped = strip_version_tag(sym_name);
    if (stripped != sym_name) {
        for (auto* lib : g_loaded_libraries) {
            if (lib == this) continue;
            void* sym = lib->get_symbol(stripped.c_str());
            if (sym) {
                return sym;
            }
        }
    }

    // 2. libc/pthread go to shims
    void* shim = lookup_shim(sym_name);
    if (shim) {
        return shim;
    }

    // 3. unresolved, log it and bind to a stub returning 0
    return get_or_create_logging_stub(sym_name);
}

bool ElfSharedObject::apply_relocations() {
    auto process_table = [&](const Elf64_Rela* relas, size_t count) -> bool {
        if (!relas) return true;
        for (size_t i = 0; i < count; ++i) {
            const Elf64_Rela& rela = relas[i];
            uint32_t type = ELF64_R_TYPE(rela.r_info);
            uint32_t sym_idx = ELF64_R_SYM(rela.r_info);
            uint64_t* target = (uint64_t*)(base_address + rela.r_offset);

            uint64_t sym_val = 0;
            const char* sym_name = nullptr;
            if (sym_idx != 0 && sym_idx < sym_count) {
                const Elf64_Sym* sym = &symtab[sym_idx];
                if (sym->st_name < strtab_size) {
                    sym_name = strtab + sym->st_name;
                }
                if (sym->st_shndx != SHN_UNDEF) {
                    sym_val = (uint64_t)(base_address + sym->st_value);
                } else if (sym_name) {
                    sym_val = (uint64_t)resolve_symbol(sym_name);
                }
            }

            switch (type) {
                case R_X86_64_RELATIVE:
                    *target = (uint64_t)(base_address + rela.r_addend);
                    break;
                case R_X86_64_GLOB_DAT:
                case R_X86_64_JUMP_SLOT:
                    *target = sym_val;
                    break;
                case R_X86_64_64:
                    *target = sym_val + rela.r_addend;
                    break;
                case R_X86_64_IRELATIVE: {
                    typedef uint64_t (__attribute__((sysv_abi)) *ifunc_resolver_t)(void);
                    ifunc_resolver_t resolver = (ifunc_resolver_t)(base_address + rela.r_addend);
                    *target = resolver();
                    break;
                }
                case R_X86_64_NONE:
                    break;
                default:
                    fprintf(stderr, "[ELF LOADER] Unsupported relocation type: %u (sym: %s)\n",
                            type, sym_name ? sym_name : "<unknown>");
                    break;
            }
        }
        return true;
    };

    if (!process_table(rela_dyn, rela_dyn_count)) return false;
    if (!process_table(rela_plt, rela_plt_count)) return false;

    return true;
}

bool ElfSharedObject::set_page_protections() {
    for (const auto& phdr : load_segments) {
        DWORD prot = 0;
        bool r = (phdr.p_flags & PF_R) != 0;
        bool w = (phdr.p_flags & PF_W) != 0;
        bool x = (phdr.p_flags & PF_X) != 0;

        if (x && w) prot = PAGE_EXECUTE_READWRITE;
        else if (x && r) prot = PAGE_EXECUTE_READ;
        else if (x) prot = PAGE_EXECUTE;
        else if (w) prot = PAGE_READWRITE;
        else if (r) prot = PAGE_READONLY;
        else prot = PAGE_NOACCESS;

        uint8_t* seg_start = (uint8_t*)(((uintptr_t)(base_address + phdr.p_vaddr)) & ~0xfffULL);
        uint8_t* seg_end = (uint8_t*)((((uintptr_t)(base_address + phdr.p_vaddr + phdr.p_memsz)) + 0xfffULL) & ~0xfffULL);
        size_t seg_size = seg_end - seg_start;

        DWORD old_prot = 0;
        if (!VirtualProtect(seg_start, seg_size, prot, &old_prot)) {
            fprintf(stderr, "[ELF LOADER] VirtualProtect failed for segment at 0x%p (size: %zu, prot: 0x%lx)\n",
                    seg_start, seg_size, prot);
            return false;
        }
    }
    return true;
}

void ElfSharedObject::run_initializers() {
    typedef void (__attribute__((sysv_abi)) *init_fn_t)(void);

    if (init_func_offset != 0) {
        init_fn_t fn = (init_fn_t)(base_address + init_func_offset);
        fn();
    }

    if (init_array && init_array_count > 0) {
        for (size_t i = 0; i < init_array_count; ++i) {
            uint64_t ptr = init_array[i];
            if (ptr != 0 && ptr != (uint64_t)-1) {
                init_fn_t fn = (init_fn_t)ptr;
                fn();
            }
        }
    }
}

void* ElfSharedObject::get_symbol(const char* name) const {
    if (!name || !symtab || !strtab) return nullptr;

    size_t name_len = strlen(name);
    for (size_t i = 0; i < sym_count; ++i) {
        const Elf64_Sym& sym = symtab[i];
        if (sym.st_shndx != SHN_UNDEF && sym.st_name < strtab_size) {
            const char* s = strtab + sym.st_name;
            if (strcmp(s, name) == 0) {
                return (void*)(base_address + sym.st_value);
            }
            if (strncmp(s, name, name_len) == 0 && s[name_len] == '@') {
                return (void*)(base_address + sym.st_value);
            }
        }
    }
    return nullptr;
}
