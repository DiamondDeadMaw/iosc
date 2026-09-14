import io
from pathlib import Path
import zipfile

from iosc.config.paths import adi_lib_dir, adi_libraries, apk_dir
from iosc.core import MissingExternalAsset, get_reporter

REQUIRED_APK = "Apple Music 6.5.2"


def find_archive(search_dir: Path) -> Path | None:
    if not search_dir.is_dir():
        return None
    for pattern in ("*.apkm", "*.apk"):
        candidates = sorted(search_dir.glob(pattern))
        if candidates:
            return candidates[0]
    return None


def extract_adi_libraries(
    apk_path: Path | str | None = None,
    target_dir: Path | str | None = None,
) -> list[Path]:
    reporter = get_reporter()
    if apk_path is None:
        source_archive = find_archive(apk_dir().path)
    else:
        p = Path(apk_path)
        source_archive = p if p.is_file() else None

    if source_archive is None:
        raise MissingExternalAsset(
            f"{REQUIRED_APK} is required. Place the .apkm in external/apk"
        )

    out_dir = Path(target_dir) if target_dir is not None else adi_lib_dir().path
    out_dir.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    with zipfile.ZipFile(source_archive, "r") as outer_zf:
        names = outer_zf.namelist()
        if "split_config.x86_64.apk" in names:
            reporter.detail("Opening inner split_config.x86_64.apk")
            inner_bytes = outer_zf.read("split_config.x86_64.apk")
            active_zf = zipfile.ZipFile(io.BytesIO(inner_bytes), "r")
        else:
            active_zf = outer_zf

        available = set(active_zf.namelist())
        for lib_name in adi_libraries():
            member = f"lib/x86_64/{lib_name}"
            if member in available:
                dest = out_dir / lib_name
                payload = active_zf.read(member)
                dest.write_bytes(payload)
                reporter.detail(f"Extracted {lib_name} ({len(payload)} bytes)")
                extracted.append(dest)

    return extracted
