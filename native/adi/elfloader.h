#ifndef ELFLOADER_H
#define ELFLOADER_H

#include <cstdint>
#include <cstddef>
#include <string>
#include <vector>
#include <unordered_map>
#include <windows.h>

// ELF64 standard types
typedef uint64_t Elf64_Addr;
typedef uint64_t Elf64_Off;
typedef uint16_t Elf64_Half;
typedef uint32_t Elf64_Word;
typedef int32_t  Elf64_Sword;
typedef uint64_t Elf64_Xword;
typedef int64_t  Elf64_Sxword;

#define EI_NIDENT 16
struct Elf64_Ehdr {
    unsigned char e_ident[EI_NIDENT];
    Elf64_Half    e_type;
    Elf64_Half    e_machine;
    Elf64_Word    e_version;
    Elf64_Addr    e_entry;
    Elf64_Off     e_phoff;
    Elf64_Off     e_shoff;
    Elf64_Word    e_flags;
    Elf64_Half    e_ehsize;
    Elf64_Half    e_phentsize;
    Elf64_Half    e_phnum;
    Elf64_Half    e_shentsize;
    Elf64_Half    e_shnum;
    Elf64_Half    e_shstrndx;
};

#define ELFMAG "\177ELF"
#define SELFMAG 4
#define EI_MAG0 0
#define EI_MAG1 1
#define EI_MAG2 2
#define EI_MAG3 3
#define EI_CLASS 4
#define ELFCLASS64 2
#define EI_DATA 5
#define ELFDATA2LSB 1
#define ET_DYN 3
#define EM_X86_64 62

struct Elf64_Phdr {
    Elf64_Word  p_type;
    Elf64_Word  p_flags;
    Elf64_Off   p_offset;
    Elf64_Addr  p_vaddr;
    Elf64_Addr  p_paddr;
    Elf64_Xword p_filesz;
    Elf64_Xword p_memsz;
    Elf64_Xword p_align;
};

struct Elf64_Shdr {
    Elf64_Word  sh_name;
    Elf64_Word  sh_type;
    Elf64_Xword sh_flags;
    Elf64_Addr  sh_addr;
    Elf64_Off   sh_offset;
    Elf64_Xword sh_size;
    Elf64_Word  sh_link;
    Elf64_Word  sh_info;
    Elf64_Xword sh_addralign;
    Elf64_Xword sh_entsize;
};

#define PT_LOAD 1
#define PT_DYNAMIC 2
#define PF_X 0x1
#define PF_W 0x2
#define PF_R 0x4

struct Elf64_Dyn {
    Elf64_Sxword d_tag;
    union {
        Elf64_Xword d_val;
        Elf64_Addr  d_ptr;
    } d_un;
};

#define DT_NULL 0
#define DT_NEEDED 1
#define DT_PLTRELSZ 2
#define DT_PLTGOT 3
#define DT_HASH 4
#define DT_STRTAB 5
#define DT_SYMTAB 6
#define DT_RELA 7
#define DT_RELASZ 8
#define DT_RELAENT 9
#define DT_STRSZ 10
#define DT_SYMENT 11
#define DT_INIT 12
#define DT_FINI 13
#define DT_SONAME 14
#define DT_RPATH 15
#define DT_SYMBOLIC 16
#define DT_REL 17
#define DT_RELSZ 18
#define DT_RELENT 19
#define DT_PLTREL 20
#define DT_DEBUG 21
#define DT_TEXTREL 22
#define DT_JMPREL 23
#define DT_INIT_ARRAY 25
#define DT_FINI_ARRAY 26
#define DT_INIT_ARRAYSZ 27
#define DT_FINI_ARRAYSZ 28
#define DT_RUNPATH 29
#define DT_FLAGS 30
#define DT_GNU_HASH 0x6ffffef5

struct Elf64_Sym {
    Elf64_Word    st_name;
    unsigned char st_info;
    unsigned char st_other;
    Elf64_Half    st_shndx;
    Elf64_Addr    st_value;
    Elf64_Xword   st_size;
};

#define SHN_UNDEF 0

struct Elf64_Rela {
    Elf64_Addr   r_offset;
    Elf64_Xword  r_info;
    Elf64_Sxword r_addend;
};

#define ELF64_R_SYM(i)    ((i) >> 32)
#define ELF64_R_TYPE(i)   ((i) & 0xffffffffL)

#define R_X86_64_NONE 0
#define R_X86_64_64 1
#define R_X86_64_GLOB_DAT 6
#define R_X86_64_JUMP_SLOT 7
#define R_X86_64_RELATIVE 8
#define R_X86_64_IRELATIVE 37

// Linux x86_64 struct stat (144 bytes)
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

class ElfSharedObject {
public:
    ElfSharedObject();
    ~ElfSharedObject();

    bool load(const std::string& filepath, const std::string& search_dir);
    void* get_symbol(const char* name) const;
    uint8_t* get_base() const { return base_address; }
    const std::string& get_soname() const { return soname; }
    void set_soname(const std::string& s) { soname = s; }
    const std::vector<std::string>& get_needed() const { return needed_libs; }

private:
    std::string filename;
    std::string soname;
    std::string library_dir;
    uint8_t* base_address;
    size_t total_span;

    std::vector<Elf64_Phdr> load_segments;
    std::vector<std::string> needed_libs;

    const Elf64_Sym* symtab;
    const char* strtab;
    size_t strtab_size;
    size_t sym_count;

    const Elf64_Rela* rela_dyn;
    size_t rela_dyn_count;

    const Elf64_Rela* rela_plt;
    size_t rela_plt_count;

    uint64_t init_func_offset;
    const uint64_t* init_array;
    size_t init_array_count;

    bool parse_elf(const std::vector<uint8_t>& file_data);
    bool apply_relocations();
    bool set_page_protections();
    void run_initializers();

    void* resolve_symbol(const char* sym_name);
};

// Shims lookup
void* lookup_shim(const char* name);
void* get_or_create_logging_stub(const char* name);
int get_stub_hit_count();
const std::vector<std::string>& get_missing_symbols();

// Global loader registry for dlopen/dlsym
ElfSharedObject* find_loaded_library(const std::string& name);
void register_loaded_library(ElfSharedObject* obj);
ElfSharedObject* load_library_cached(const std::string& lib_name, const std::string& dir);

#endif
