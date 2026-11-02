// android and linux share kernel abi per cpu. most imports call host libc directly

#include "elfloader.h"

#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <fcntl.h>
#include <pthread.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/sysinfo.h>
#include <sys/time.h>
#include <unistd.h>

// sys/auxv.h drags in elf.h, which clashes with our elf structs
extern "C" unsigned long getauxval(unsigned long type);

size_t plat_page_size() {
    static size_t page = (size_t)sysconf(_SC_PAGESIZE);
    return page;
}

void* plat_alloc(size_t size, bool executable) {
    int prot = PROT_READ | PROT_WRITE | (executable ? PROT_EXEC : 0);
    void* p = mmap(nullptr, size, prot, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    return p == MAP_FAILED ? nullptr : p;
}

void plat_free(void* ptr, size_t size) {
    munmap(ptr, size);
}

bool plat_protect(void* ptr, size_t size, bool r, bool w, bool x) {
    int prot = (r ? PROT_READ : 0) | (w ? PROT_WRITE : 0) | (x ? PROT_EXEC : 0);
    return mprotect(ptr, size, prot) == 0;
}

void plat_flush_icache(void* ptr, size_t size) {
    __builtin___clear_cache((char*)ptr, (char*)ptr + size);
}

std::string plat_join(const std::string& dir, const std::string& name) {
    std::string full = dir;
    if (!full.empty() && full.back() != '/') {
        full += "/";
    }
    return full + name;
}

extern "C" int* shim_errno() {
    return &errno;
}

// bionic FILE is 152 bytes on lp64. stdin, stdout, stderr are __sF[0..2]
static const size_t BIONIC_FILE_SIZE = 152;
static uint8_t s_bionic_sF[3 * BIONIC_FILE_SIZE];

static FILE* host_stream(void* stream) {
    uint8_t* p = (uint8_t*)stream;
    if (p >= s_bionic_sF && p < s_bionic_sF + sizeof(s_bionic_sF)) {
        size_t index = (size_t)(p - s_bionic_sF) / BIONIC_FILE_SIZE;
        return index == 0 ? stdin : index == 1 ? stdout : stderr;
    }
    return (FILE*)stream;
}

static bool is_bionic_stream(void* stream) {
    uint8_t* p = (uint8_t*)stream;
    return p >= s_bionic_sF && p < s_bionic_sF + sizeof(s_bionic_sF);
}

extern "C" int shim_vfprintf(void* stream, const char* fmt, va_list ap) {
    return vfprintf(host_stream(stream), fmt, ap);
}

extern "C" int shim_fprintf(void* stream, const char* fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int written = vfprintf(host_stream(stream), fmt, ap);
    va_end(ap);
    return written;
}

extern "C" size_t shim_fwrite(const void* data, size_t size, size_t count, void* stream) {
    return fwrite(data, size, count, host_stream(stream));
}

extern "C" int shim_fputc(int c, void* stream) {
    return fputc(c, host_stream(stream));
}

extern "C" int shim_fflush(void* stream) {
    return fflush(stream ? host_stream(stream) : nullptr);
}

extern "C" int shim_fclose(void* stream) {
    if (is_bionic_stream(stream)) return 0;
    return fclose((FILE*)stream);
}

extern "C" int shim_vsprintf_chk(char* dest, int flags, size_t dest_len, const char* fmt, va_list ap) {
    (void)flags;
    return vsnprintf(dest, dest_len, fmt, ap);
}

extern "C" void* shim_memchr_chk(const void* s, int c, size_t n, size_t buf_size) {
    (void)buf_size;
    return (void*)memchr(s, c, n);
}

// bionic _SC_ numbers differ from host
extern "C" long shim_sysconf(int name) {
    (void)name;
    return (long)plat_page_size();
}

extern "C" uint32_t shim_arc4random() {
    uint32_t value = 0;
    if (getentropy(&value, sizeof(value)) != 0) {
        value = (uint32_t)time(nullptr) ^ (uint32_t)(uintptr_t)&value;
    }
    return value;
}

extern "C" int shim_system_property_get(const char* name, char* value) {
    (void)name;
    const char* default_val = "000000000000";
    if (value) {
        strcpy(value, default_val);
    }
    return (int)strlen(default_val);
}

extern "C" void shim_android_set_abort_message(const char* msg) {
    if (msg) fprintf(stderr, "[ADI ABORT] %s\n", msg);
}

extern "C" void shim_stack_chk_fail() {
    fprintf(stderr, "[ELF LOADER FATAL] Stack smashing detected (__stack_chk_fail)\n");
    abort();
}

extern "C" int shim_cxa_atexit(void (*func)(void*), void* arg, void* dso_handle) {
    (void)func; (void)arg; (void)dso_handle;
    return 0;
}

extern "C" void shim_cxa_finalize(void* dso_handle) {
    (void)dso_handle;
}

extern "C" int shim_register_atfork(void (*prepare)(void), void (*parent)(void), void (*child)(void)) {
    (void)prepare; (void)parent; (void)child;
    return 0;
}

extern "C" void* shim_dlopen(const char* filename, int flags) {
    (void)flags;
    if (!filename) {
        return (void*)1;
    }
    std::string name(filename);
    size_t slash = name.find_last_of('/');
    if (slash != std::string::npos) {
        name = name.substr(slash + 1);
    }
    ElfSharedObject* obj = find_loaded_library(name);
    return obj ? (void*)obj : (void*)1;
}

extern "C" void* shim_dlsym(void* handle, const char* symbol) {
    if (!symbol) return nullptr;
    if (handle && handle != (void*)1) {
        void* sym = ((ElfSharedObject*)handle)->get_symbol(symbol);
        if (sym) return sym;
    }
    for (auto* lib : loaded_libraries()) {
        void* sym = lib->get_symbol(symbol);
        if (sym) return sym;
    }
    return lookup_shim(symbol);
}

extern "C" int shim_dlclose(void* handle) {
    (void)handle;
    return 0;
}

extern "C" void* shim_dlerror() {
    return nullptr;
}

extern "C" int shim_dl_iterate_phdr(void* callback, void* data) {
    (void)callback; (void)data;
    return 0;
}

// libs call in from one thread only. locks are no-ops
extern "C" int shim_empty_stub() {
    return 0;
}

extern "C" int shim_pthread_once(int* once_control, void (*init_routine)(void)) {
    if (once_control && init_routine && *once_control == 0) {
        *once_control = 1;
        init_routine();
    }
    return 0;
}

extern "C" int shim_pthread_key_create(int* key, void (*destructor)(void*)) {
    pthread_key_t host_key;
    int result = pthread_key_create(&host_key, destructor);
    if (result == 0 && key) *key = (int)host_key;
    return result;
}

extern "C" int shim_pthread_key_delete(int key) {
    return pthread_key_delete((pthread_key_t)key);
}

extern "C" int shim_pthread_setspecific(int key, const void* value) {
    return pthread_setspecific((pthread_key_t)key, value);
}

extern "C" void* shim_pthread_getspecific(int key) {
    return pthread_getspecific((pthread_key_t)key);
}

extern "C" int shim_log_noop() {
    return 0;
}

static const ShimEntry g_shims[] = {
    {"open",                     (void*)::open},
    {"close",                    (void*)::close},
    {"read",                     (void*)::read},
    {"write",                    (void*)::write},
    {"pread",                    (void*)::pread},
    {"lstat",                    (void*)::lstat},
    {"fstat",                    (void*)::fstat},
    {"ftruncate",                (void*)::ftruncate},
    {"mkdir",                    (void*)::mkdir},
    {"chmod",                    (void*)::chmod},
    {"umask",                    (void*)::umask},
    {"unlink",                   (void*)::unlink},
    {"rename",                   (void*)::rename},
    {"flock",                    (void*)::flock},
    {"ioctl",                    (void*)::ioctl},
    {"socket",                   (void*)::socket},
    {"syscall",                  (void*)::syscall},
    {"sysinfo",                  (void*)::sysinfo},
    {"getauxval",                (void*)::getauxval},
    {"getpagesize",              (void*)::getpagesize},
    {"sleep",                    (void*)::sleep},
    {"clock_gettime",            (void*)::clock_gettime},
    {"gettimeofday",             (void*)::gettimeofday},
    {"mktime",                   (void*)::mktime},
    {"malloc",                   (void*)::malloc},
    {"free",                     (void*)::free},
    {"calloc",                   (void*)::calloc},
    {"realloc",                  (void*)::realloc},
    {"posix_memalign",           (void*)::posix_memalign},
    {"memcpy",                   (void*)::memcpy},
    {"memmove",                  (void*)::memmove},
    {"memset",                   (void*)::memset},
    {"memcmp",                   (void*)::memcmp},
    {"memchr",                   (void*)(void* (*)(void*, int, size_t))::memchr},
    {"memmem",                   (void*)::memmem},
    {"strlen",                   (void*)::strlen},
    {"strnlen",                  (void*)::strnlen},
    {"strcmp",                   (void*)::strcmp},
    {"strncmp",                  (void*)::strncmp},
    {"strcoll",                  (void*)::strcoll},
    {"strxfrm",                  (void*)::strxfrm},
    {"strcpy",                   (void*)::strcpy},
    {"strncpy",                  (void*)::strncpy},
    {"strcat",                   (void*)::strcat},
    {"strncat",                  (void*)::strncat},
    {"strchr",                   (void*)(char* (*)(char*, int))::strchr},
    {"strrchr",                  (void*)(char* (*)(char*, int))::strrchr},
    {"strstr",                   (void*)(char* (*)(char*, const char*))::strstr},
    {"strpbrk",                  (void*)(char* (*)(char*, const char*))::strpbrk},
    {"strspn",                   (void*)::strspn},
    {"strcspn",                  (void*)::strcspn},
    {"strtok",                   (void*)::strtok},
    {"strerror",                 (void*)::strerror},
    {"atoi",                     (void*)::atoi},
    {"atoll",                    (void*)::atoll},
    {"abort",                    (void*)::abort},
    {"snprintf",                 (void*)::snprintf},
    {"vsnprintf",                (void*)::vsnprintf},
    {"vasprintf",                (void*)::vasprintf},
    {"fopen",                    (void*)::fopen},
    {"fclose",                   (void*)shim_fclose},
    {"fflush",                   (void*)shim_fflush},
    {"fwrite",                   (void*)shim_fwrite},
    {"fputc",                    (void*)shim_fputc},
    {"fprintf",                  (void*)shim_fprintf},
    {"vfprintf",                 (void*)shim_vfprintf},
    {"__vsprintf_chk",           (void*)shim_vsprintf_chk},
    {"__memchr_chk",             (void*)shim_memchr_chk},
    {"__sF",                     (void*)s_bionic_sF},
    {"__errno",                  (void*)shim_errno},
    {"__errno_location",         (void*)shim_errno},
    {"sysconf",                  (void*)shim_sysconf},
    {"arc4random",               (void*)shim_arc4random},
    {"__system_property_get",    (void*)shim_system_property_get},
    {"android_set_abort_message",(void*)shim_android_set_abort_message},
    {"__stack_chk_fail",         (void*)shim_stack_chk_fail},
    {"__cxa_atexit",             (void*)shim_cxa_atexit},
    {"__cxa_finalize",           (void*)shim_cxa_finalize},
    {"__register_atfork",        (void*)shim_register_atfork},
    {"dlopen",                   (void*)shim_dlopen},
    {"dlsym",                    (void*)shim_dlsym},
    {"dlclose",                  (void*)shim_dlclose},
    {"dlerror",                  (void*)shim_dlerror},
    {"dl_iterate_phdr",          (void*)shim_dl_iterate_phdr},
    {"pthread_once",             (void*)shim_pthread_once},
    {"pthread_create",           (void*)shim_empty_stub},
    {"pthread_mutex_lock",       (void*)shim_empty_stub},
    {"pthread_mutex_unlock",     (void*)shim_empty_stub},
    {"pthread_rwlock_init",      (void*)shim_empty_stub},
    {"pthread_rwlock_destroy",   (void*)shim_empty_stub},
    {"pthread_rwlock_rdlock",    (void*)shim_empty_stub},
    {"pthread_rwlock_wrlock",    (void*)shim_empty_stub},
    {"pthread_rwlock_unlock",    (void*)shim_empty_stub},
    {"pthread_cond_init",        (void*)shim_empty_stub},
    {"pthread_cond_destroy",     (void*)shim_empty_stub},
    {"pthread_cond_broadcast",   (void*)shim_empty_stub},
    {"pthread_cond_signal",      (void*)shim_empty_stub},
    {"pthread_cond_wait",        (void*)shim_empty_stub},
    {"pthread_key_create",       (void*)shim_pthread_key_create},
    {"pthread_key_delete",       (void*)shim_pthread_key_delete},
    {"pthread_setspecific",      (void*)shim_pthread_setspecific},
    {"pthread_getspecific",      (void*)shim_pthread_getspecific},
    {"pthread_self",             (void*)::pthread_self},
    {"pthread_equal",            (void*)::pthread_equal},
    {"__android_log_print",      (void*)shim_log_noop},
    {"__android_log_write",      (void*)shim_log_noop},
    {"__android_log_vprint",     (void*)shim_log_noop},
    {"openlog",                  (void*)shim_log_noop},
    {"syslog",                   (void*)shim_log_noop},
    {"closelog",                 (void*)shim_log_noop},
    {nullptr,                    nullptr}
};

const ShimEntry* plat_shims() {
    return g_shims;
}
