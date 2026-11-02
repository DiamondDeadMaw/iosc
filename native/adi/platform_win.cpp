#include "elfloader.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <cerrno>
#include <fcntl.h>
#include <io.h>
#include <sys/stat.h>
#include <direct.h>
#include <windows.h>
#include <wincrypt.h>

// android x86_64 struct stat, 144 bytes
struct linux_stat64 {
    uint64_t st_dev;
    uint64_t st_ino;
    uint64_t st_nlink;
    uint32_t st_mode;
    uint32_t st_uid;
    uint32_t st_gid;
    uint32_t __pad0;
    uint64_t st_rdev;
    int64_t  st_size;
    int64_t  st_blksize;
    int64_t  st_blocks;
    int64_t  st_atime;
    int64_t  st_atimensec;
    int64_t  st_mtime;
    int64_t  st_mtimensec;
    int64_t  st_ctime;
    int64_t  st_ctimensec;
    int64_t  __unused[3];
};

struct linux_timeval {
    int64_t tv_sec;
    int64_t tv_usec;
};

size_t plat_page_size() {
    return 4096;
}

void* plat_alloc(size_t size, bool executable) {
    DWORD prot = executable ? PAGE_EXECUTE_READWRITE : PAGE_READWRITE;
    return VirtualAlloc(nullptr, size, MEM_RESERVE | MEM_COMMIT, prot);
}

void plat_free(void* ptr, size_t size) {
    (void)size;
    VirtualFree(ptr, 0, MEM_RELEASE);
}

bool plat_protect(void* ptr, size_t size, bool r, bool w, bool x) {
    DWORD prot;
    if (x && w) prot = PAGE_EXECUTE_READWRITE;
    else if (x && r) prot = PAGE_EXECUTE_READ;
    else if (x) prot = PAGE_EXECUTE;
    else if (w) prot = PAGE_READWRITE;
    else if (r) prot = PAGE_READONLY;
    else prot = PAGE_NOACCESS;
    DWORD old_prot = 0;
    return VirtualProtect(ptr, size, prot, &old_prot) != 0;
}

void plat_flush_icache(void* ptr, size_t size) {
    FlushInstructionCache(GetCurrentProcess(), ptr, size);
}

std::string plat_join(const std::string& dir, const std::string& name) {
    std::string full = dir;
    if (!full.empty() && full.back() != '/' && full.back() != '\\') {
        full += "\\";
    }
    return full + name;
}

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

extern "C" ADI_ABI int* shim_errno() {
    return &s_tl_errno;
}

extern "C" ADI_ABI void* shim_malloc(size_t size) {
    return ::malloc(size);
}

extern "C" ADI_ABI void shim_free(void* ptr) {
    ::free(ptr);
}

extern "C" ADI_ABI void* shim_calloc(size_t nmemb, size_t size) {
    return ::calloc(nmemb, size);
}

extern "C" ADI_ABI void* shim_realloc(void* ptr, size_t size) {
    return ::realloc(ptr, size);
}

extern "C" ADI_ABI int shim_posix_memalign(void** memptr, size_t alignment, size_t size) {
    if (!memptr) return 22;
    void* p = _aligned_malloc(size, alignment);
    if (!p) return 12;
    *memptr = p;
    return 0;
}

extern "C" ADI_ABI void* shim_memcpy(void* dest, const void* src, size_t n) {
    return ::memcpy(dest, src, n);
}

extern "C" ADI_ABI void* shim_memmove(void* dest, const void* src, size_t n) {
    return ::memmove(dest, src, n);
}

extern "C" ADI_ABI void* shim_memset(void* s, int c, size_t n) {
    return ::memset(s, c, n);
}

extern "C" ADI_ABI int shim_memcmp(const void* s1, const void* s2, size_t n) {
    return ::memcmp(s1, s2, n);
}

extern "C" ADI_ABI void* shim_memchr(const void* s, int c, size_t n) {
    return (void*)::memchr(s, c, n);
}

extern "C" ADI_ABI size_t shim_strlen(const char* s) {
    return ::strlen(s);
}

extern "C" ADI_ABI int shim_strcmp(const char* s1, const char* s2) {
    return ::strcmp(s1, s2);
}

extern "C" ADI_ABI int shim_strncmp(const char* s1, const char* s2, size_t n) {
    return ::strncmp(s1, s2, n);
}

extern "C" ADI_ABI char* shim_strcpy(char* dest, const char* src) {
    return ::strcpy(dest, src);
}

extern "C" ADI_ABI char* shim_strncpy(char* dest, const char* src, size_t n) {
    return ::strncpy(dest, src, n);
}

extern "C" ADI_ABI char* shim_strcat(char* dest, const char* src) {
    return ::strcat(dest, src);
}

extern "C" ADI_ABI char* shim_strncat(char* dest, const char* src, size_t n) {
    return ::strncat(dest, src, n);
}

extern "C" ADI_ABI char* shim_strchr(const char* s, int c) {
    return (char*)::strchr(s, c);
}

extern "C" ADI_ABI char* shim_strrchr(const char* s, int c) {
    return (char*)::strrchr(s, c);
}

extern "C" ADI_ABI char* shim_strstr(const char* haystack, const char* needle) {
    return (char*)::strstr(haystack, needle);
}

extern "C" ADI_ABI char* shim_strerror(int errnum) {
    return ::strerror(errnum);
}

extern "C" ADI_ABI int shim_atoi(const char* nptr) {
    return ::atoi(nptr);
}

extern "C" ADI_ABI long long shim_atoll(const char* nptr) {
    return ::atoll(nptr);
}

extern "C" ADI_ABI void shim_abort() {
    ::abort();
}

extern "C" ADI_ABI void shim_stack_chk_fail() {
    fprintf(stderr, "[ELF LOADER FATAL] Stack smashing detected (__stack_chk_fail)\n");
    ::abort();
}

extern "C" ADI_ABI int shim_cxa_atexit(void (*func)(void*), void* arg, void* dso_handle) {
    (void)func; (void)arg; (void)dso_handle;
    return 0;
}

extern "C" ADI_ABI void shim_cxa_finalize(void* dso_handle) {
    (void)dso_handle;
}

extern "C" ADI_ABI int shim_register_atfork(void (*prepare)(void), void (*parent)(void), void (*child)(void)) {
    (void)prepare; (void)parent; (void)child;
    return 0;
}

extern "C" ADI_ABI int shim_getpagesize() {
    return 4096;
}

extern "C" ADI_ABI long shim_sysconf(int name) {
    (void)name;
    return 4096;
}

extern "C" ADI_ABI int shim_open(const char* pathname, int flags, ...) {
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

extern "C" ADI_ABI int shim_close(int fd) {
    int ret = _close(fd);
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" ADI_ABI int64_t shim_read(int fd, void* buf, size_t count) {
    int ret = _read(fd, buf, (unsigned int)count);
    if (ret < 0) {
        s_tl_errno = errno;
        return -1;
    }
    return ret;
}

extern "C" ADI_ABI int64_t shim_write(int fd, const void* buf, size_t count) {
    int ret = _write(fd, buf, (unsigned int)count);
    if (ret < 0) {
        s_tl_errno = errno;
        return -1;
    }
    return ret;
}

extern "C" ADI_ABI int shim_ftruncate(int fd, int64_t length) {
    int ret = _chsize(fd, (long)length);
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" ADI_ABI int shim_mkdir(const char* pathname, int mode) {
    (void)mode;
    std::string win_path = to_windows_path(pathname);
    int ret = _mkdir(win_path.c_str());
    if (ret < 0) {
        s_tl_errno = errno;
    }
    return ret;
}

extern "C" ADI_ABI int shim_chmod(const char* pathname, int mode) {
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

extern "C" ADI_ABI int shim_umask(int mask) {
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

extern "C" ADI_ABI int shim_fstat(int fd, struct linux_stat64* buf) {
    if (!buf) {
        s_tl_errno = 22;
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

extern "C" ADI_ABI int shim_lstat(const char* path, struct linux_stat64* buf) {
    if (!path || !buf) {
        s_tl_errno = 22;
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

extern "C" ADI_ABI int shim_gettimeofday(struct linux_timeval* tv, void* tz) {
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

extern "C" ADI_ABI uint32_t shim_arc4random() {
    uint32_t val = 0;
    HCRYPTPROV hProv = 0;
    if (CryptAcquireContextW(&hProv, NULL, NULL, PROV_RSA_FULL, CRYPT_VERIFYCONTEXT | CRYPT_SILENT)) {
        CryptGenRandom(hProv, sizeof(val), (BYTE*)&val);
        CryptReleaseContext(hProv, 0);
        return val;
    }
    return (uint32_t)GetTickCount64();
}

extern "C" ADI_ABI int shim_system_property_get(const char* name, char* value) {
    (void)name;
    const char* default_val = "000000000000";
    if (value) {
        strcpy(value, default_val);
    }
    return (int)strlen(default_val);
}

extern "C" ADI_ABI void* shim_dlopen(const char* filename, int flags) {
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

extern "C" ADI_ABI void* shim_dlsym(void* handle, const char* symbol) {
    if (!symbol) return nullptr;

    if (handle && handle != (void*)1) {
        ElfSharedObject* obj = (ElfSharedObject*)handle;
        void* sym = obj->get_symbol(symbol);
        if (sym) return sym;
    }

    for (auto* lib : loaded_libraries()) {
        void* sym = lib->get_symbol(symbol);
        if (sym) return sym;
    }

    return lookup_shim(symbol);
}

extern "C" ADI_ABI int shim_dlclose(void* handle) {
    (void)handle;
    return 0;
}

extern "C" ADI_ABI void* shim_dlerror() {
    return nullptr;
}

extern "C" ADI_ABI int shim_dl_iterate_phdr(void* callback, void* data) {
    (void)callback; (void)data;
    return 0;
}

extern "C" ADI_ABI int shim_empty_stub() {
    return 0;
}

extern "C" ADI_ABI int shim_pthread_once(int* once_control, void (ADI_ABI *init_routine)(void)) {
    if (once_control && init_routine) {
        if (*once_control == 0) {
            *once_control = 1;
            init_routine();
        }
    }
    return 0;
}

extern "C" ADI_ABI int shim_pthread_key_create(uint32_t* key, void (*destructor)(void*)) {
    (void)destructor;
    DWORD k = TlsAlloc();
    if (k == TLS_OUT_OF_INDEXES) return 11;
    if (key) *key = (uint32_t)k;
    return 0;
}

extern "C" ADI_ABI int shim_pthread_key_delete(uint32_t key) {
    return TlsFree((DWORD)key) ? 0 : 22;
}

extern "C" ADI_ABI int shim_pthread_setspecific(uint32_t key, const void* value) {
    return TlsSetValue((DWORD)key, (LPVOID)value) ? 0 : 22;
}

extern "C" ADI_ABI void* shim_pthread_getspecific(uint32_t key) {
    return TlsGetValue((DWORD)key);
}

extern "C" ADI_ABI uint64_t shim_pthread_self() {
    return (uint64_t)GetCurrentThreadId();
}

extern "C" ADI_ABI int shim_pthread_equal(uint64_t t1, uint64_t t2) {
    return t1 == t2;
}

extern "C" ADI_ABI int shim_android_log_print(int prio, const char* tag, const char* fmt, ...) {
    (void)prio; (void)tag; (void)fmt;
    return 0;
}

extern "C" ADI_ABI int shim_android_log_write(int prio, const char* tag, const char* text) {
    (void)prio; (void)tag; (void)text;
    return 0;
}

extern "C" ADI_ABI int shim_android_log_vprint(int prio, const char* tag, const char* fmt, va_list ap) {
    (void)prio; (void)tag; (void)fmt; (void)ap;
    return 0;
}

static uint8_t s_bionic_sF[1024];

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

const ShimEntry* plat_shims() {
    return g_shims;
}
