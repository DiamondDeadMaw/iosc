#include "elfloader.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
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
        memory = (uint8_t*)plat_alloc(capacity, true);
    }
    ~StubMemory() {
        if (memory) {
            plat_free(memory, capacity);
        }
    }
};
static StubMemory g_stub_mem;

extern "C" ADI_ABI int64_t on_missing_stub_called(const char* name) {
    fprintf(stderr, "[ELF LOADER STUB HIT] Unimplemented symbol invoked: %s\n", name ? name : "<unknown>");
    g_missing_stub_hits++;
    return 0;
}

// thunk loads the symbol name into the first argument register and jumps to the logger
static size_t write_stub_thunk(uint8_t* p, const char* name_storage) {
#if defined(__x86_64__)
    // movabs rdi, name. movabs rax, handler. jmp rax
    p[0] = 0x48; p[1] = 0xbf;
    *(uint64_t*)(p + 2) = (uint64_t)name_storage;
    p[10] = 0x48; p[11] = 0xb8;
    *(uint64_t*)(p + 12) = (uint64_t)on_missing_stub_called;
    p[20] = 0xff; p[21] = 0xe0;
    return 24;
#elif defined(__aarch64__)
    // ldr x0, name. ldr x16, handler. br x16. nop. then the two literals
    uint32_t* code = (uint32_t*)p;
    code[0] = 0x58000080;
    code[1] = 0x580000B0;
    code[2] = 0xD61F0200;
    code[3] = 0xD503201F;
    *(uint64_t*)(p + 16) = (uint64_t)name_storage;
    *(uint64_t*)(p + 24) = (uint64_t)on_missing_stub_called;
    return 32;
#endif
}

void* get_or_create_logging_stub(const char* name) {
    if (std::find(g_missing_symbols.begin(), g_missing_symbols.end(), name) == g_missing_symbols.end()) {
        g_missing_symbols.push_back(name);
    }

    if (!g_stub_mem.memory) {
        return nullptr;
    }

    size_t name_len = strlen(name) + 1;
    size_t stub_size = (32 + name_len + 15) & ~(size_t)15;
    if (g_stub_mem.used + stub_size > g_stub_mem.capacity) {
        return (void*)on_missing_stub_called;
    }

    uint8_t* stub_addr = g_stub_mem.memory + g_stub_mem.used;
    char* name_storage = (char*)(stub_addr + 32);
    memcpy(name_storage, name, name_len);
    size_t code_size = write_stub_thunk(stub_addr, name_storage);
    plat_flush_icache(stub_addr, code_size);

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

const std::vector<ElfSharedObject*>& loaded_libraries() {
    return g_loaded_libraries;
}

ElfSharedObject* load_library_cached(const std::string& lib_name, const std::string& dir) {
    ElfSharedObject* existing = find_loaded_library(lib_name);
    if (existing) {
        return existing;
    }

    ElfSharedObject* new_lib = new ElfSharedObject();
    new_lib->set_soname(lib_name);
    register_loaded_library(new_lib);

    if (!new_lib->load(plat_join(dir, lib_name), dir)) {
        return nullptr;
    }
    return new_lib;
}

std::string strip_version_tag(const char* sym_name) {
    if (!sym_name) return "";
    const char* at = strchr(sym_name, '@');
    if (at) {
        return std::string(sym_name, at - sym_name);
    }
    return std::string(sym_name);
}

static void* find_shim(const char* name) {
    for (const ShimEntry* entry = plat_shims(); entry->name != nullptr; ++entry) {
        if (strcmp(entry->name, name) == 0) {
            return entry->func;
        }
    }
    return nullptr;
}

void* lookup_shim(const char* name) {
    if (!name) return nullptr;
    void* found = find_shim(name);
    if (found) return found;
    std::string base = strip_version_tag(name);
    return base != name ? find_shim(base.c_str()) : nullptr;
}

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
        plat_free(base_address, total_span);
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

    if (ehdr->e_machine != ELF_MACHINE) {
        fprintf(stderr, "[ELF LOADER] %s is not an %s library, extract the libraries for this cpu\n",
                filename.c_str(), ELF_MACHINE_NAME);
        return false;
    }

    if (ehdr->e_type != ET_DYN) {
        fprintf(stderr, "[ELF LOADER] Object is not ET_DYN shared library\n");
        return false;
    }

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

    uint64_t page_mask = (uint64_t)plat_page_size() - 1;
    uint64_t aligned_min = min_vaddr & ~page_mask;
    uint64_t aligned_max = (max_vaddr + page_mask) & ~page_mask;
    total_span = (size_t)(aligned_max - aligned_min);

    base_address = (uint8_t*)plat_alloc(total_span, false);
    if (!base_address) {
        fprintf(stderr, "[ELF LOADER] Failed to allocate span: %zu bytes\n", total_span);
        return false;
    }

    for (const auto& phdr : load_segments) {
        uint8_t* dest = base_address + phdr.p_vaddr;
        if (phdr.p_filesz > 0) {
            memcpy(dest, file_data.data() + phdr.p_offset, phdr.p_filesz);
        }
        if (phdr.p_memsz > phdr.p_filesz) {
            memset(dest + phdr.p_filesz, 0, phdr.p_memsz - phdr.p_filesz);
        }
    }

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
            default:
                break;
        }
    }

    if (!strtab || !symtab) {
        fprintf(stderr, "[ELF LOADER] Missing DT_SYMTAB or DT_STRTAB\n");
        return false;
    }

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

    if (ehdr->e_shoff != 0 && ehdr->e_shnum > 0) {
        const Elf64_Shdr* shdrs = (const Elf64_Shdr*)(file_data.data() + ehdr->e_shoff);
        for (Elf64_Half i = 0; i < ehdr->e_shnum; ++i) {
            if (shdrs[i].sh_type == SHT_DYNSYM) {
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

    // apple libs, libc shims, logging stub last
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

    void* shim = lookup_shim(sym_name);
    if (shim) {
        return shim;
    }

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
                case R_RELATIVE:
                    *target = (uint64_t)(base_address + rela.r_addend);
                    break;
                case R_GLOB_DAT:
                case R_JUMP_SLOT:
                    *target = sym_val;
                    break;
                case R_ABS64:
                    *target = sym_val + rela.r_addend;
                    break;
                case R_IRELATIVE: {
                    typedef uint64_t (ADI_ABI *ifunc_resolver_t)(void);
                    ifunc_resolver_t resolver = (ifunc_resolver_t)(base_address + rela.r_addend);
                    *target = resolver();
                    break;
                }
                case R_NONE:
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
    uintptr_t page_mask = (uintptr_t)plat_page_size() - 1;
    for (const auto& phdr : load_segments) {
        bool r = (phdr.p_flags & PF_R) != 0;
        bool w = (phdr.p_flags & PF_W) != 0;
        bool x = (phdr.p_flags & PF_X) != 0;

        uint8_t* seg_start = (uint8_t*)(((uintptr_t)(base_address + phdr.p_vaddr)) & ~page_mask);
        uint8_t* seg_end = (uint8_t*)((((uintptr_t)(base_address + phdr.p_vaddr + phdr.p_memsz)) + page_mask) & ~page_mask);
        size_t seg_size = seg_end - seg_start;

        if (x) {
            plat_flush_icache(seg_start, seg_size);
        }
        if (!plat_protect(seg_start, seg_size, r, w, x)) {
            fprintf(stderr, "[ELF LOADER] Failed to protect segment at %p (size: %zu)\n",
                    (void*)seg_start, seg_size);
            return false;
        }
    }
    return true;
}

void ElfSharedObject::run_initializers() {
    typedef void (ADI_ABI *init_fn_t)(void);

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
