from dataclasses import dataclass
from pathlib import Path
from typing import Any

from iosc.toolchain import clang

MODULE_MAP_NAME = "module.modulemap"
DEFAULT_HEADERS = "include"
HEADER_SUFFIXES = (".h", ".hh", ".hpp", ".hxx")


@dataclass(frozen=True)
class ClangTarget:
    # public headers folder, None when the target has none
    include_dir: Path | None = None
    # module map the package provides outside the include folder
    module_map: Path | None = None
    # module map text iosc generates when the package has nothing
    map_text: str | None = None
    c_flags: tuple[str, ...] = ()
    cxx_flags: tuple[str, ...] = ()
    c_standard: str | None = None
    cxx_standard: str | None = None
    cxx: bool = False

    def flags_for(self, language: str) -> list[str]:
        return list(self.cxx_flags if language in clang.CXX_LANGUAGES else self.c_flags)

    def standard_for(self, language: str) -> str | None:
        return self.cxx_standard if language in clang.CXX_LANGUAGES else self.c_standard

    def to_json(self) -> dict[str, Any]:
        return {
            "include_dir": str(self.include_dir) if self.include_dir else None,
            "module_map": str(self.module_map) if self.module_map else None,
            "map_text": self.map_text,
            "c_flags": list(self.c_flags),
            "cxx_flags": list(self.cxx_flags),
            "c_standard": self.c_standard,
            "cxx_standard": self.cxx_standard,
            "cxx": self.cxx,
        }

    @staticmethod
    def from_json(data: dict[str, Any]) -> "ClangTarget":
        return ClangTarget(
            include_dir=Path(data["include_dir"]) if data["include_dir"] else None,
            module_map=Path(data["module_map"]) if data["module_map"] else None,
            map_text=data["map_text"],
            c_flags=tuple(data["c_flags"]),
            cxx_flags=tuple(data["cxx_flags"]),
            c_standard=data["c_standard"],
            cxx_standard=data["cxx_standard"],
            cxx=data["cxx"],
        )


# compiler flags for one cSettings or cxxSettings entry, ValueError names what is refused
def setting_flags(kind: str, value: Any, target_dir: Path) -> list[str]:
    if kind == "define":
        return [f"-D{value}"]
    if kind == "headerSearchPath":
        return ["-I", str((target_dir / value).resolve())]
    if kind == "unsafeFlags":
        refused = clang.unsafe_flag(value)
        if refused is not None:
            raise ValueError(f"uses the unsafe flag {refused}, iosc passes only {clang.SAFE_FLAG_SUMMARY}")
        return list(value)
    raise ValueError(f"uses the {kind} setting, which iosc does not support yet")


def header_files(include_dir: Path) -> list[Path]:
    return sorted(p for p in include_dir.rglob("*") if p.is_file() and p.suffix in HEADER_SUFFIXES)


def render_module_map(module: str, umbrella: Path, is_directory: bool) -> str:
    kind = "umbrella" if is_directory else "umbrella header"
    return f'module {module} {{\n    {kind} "{umbrella.as_posix()}"\n    export *\n}}\n'


# a module map from the package wins, otherwise the umbrella the swiftpm would pick
def public_headers(target_dir: Path, public: str | None, module: str) -> tuple[Path | None, Path | None, str | None]:
    include = target_dir / (public or DEFAULT_HEADERS)
    if not include.is_dir():
        return None, None, None
    if (include / MODULE_MAP_NAME).is_file():
        return include, None, None
    if (target_dir / MODULE_MAP_NAME).is_file():
        return include, target_dir / MODULE_MAP_NAME, None
    for header in (include / f"{module}.h", include / module / f"{module}.h"):
        if header.is_file():
            return include, None, render_module_map(module, header, False)
    if header_files(include):
        return include, None, render_module_map(module, include, True)
    return include, None, None
