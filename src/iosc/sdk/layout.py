from dataclasses import dataclass
import json
from pathlib import Path
import plistlib

from iosc.config import paths
from iosc.core.errors import SdkError

__all__ = ["SdkLayout", "from_root"]


@dataclass(frozen=True)
class SdkLayout:
    root: Path
    frameworks_dir: Path
    swift_lib_dir: Path
    usr_lib_dir: Path
    platform_version: str


def from_root(root: Path | str | None = None) -> SdkLayout:
    if root is None:
        root_path = paths.sdk_root().path
    else:
        root_path = Path(root)

    if not root_path.is_dir():
        raise SdkError(f"SDK root directory missing at {root_path}")

    frameworks = root_path / "System" / "Library" / "Frameworks"
    if not frameworks.is_dir():
        raise SdkError(f"SDK missing frameworks directory at {frameworks}")

    usr_lib = root_path / "usr" / "lib"
    if not usr_lib.is_dir():
        raise SdkError(f"SDK missing usr lib directory at {usr_lib}")

    swift_lib = usr_lib / "swift"
    if not swift_lib.is_dir():
        raise SdkError(f"SDK missing swift lib directory at {swift_lib}")

    settings_json = root_path / "SDKSettings.json"
    settings_plist = root_path / "SDKSettings.plist"
    version: str | None = None

    if settings_json.is_file():
        try:
            data = json.loads(settings_json.read_text(encoding="utf-8"))
            version = data.get("Version") or data.get("DefaultDeploymentTarget") or data.get("MinimalDisplayName")
        except Exception as err:
            raise SdkError(f"Corrupt SDKSettings.json at {settings_json}") from err
    elif settings_plist.is_file():
        try:
            data = plistlib.loads(settings_plist.read_bytes())
            version = data.get("Version") or data.get("DefaultDeploymentTarget") or data.get("MinimalDisplayName")
        except Exception as err:
            raise SdkError(f"Corrupt SDKSettings.plist at {settings_plist}") from err

    if not version:
        raise SdkError(f"SDK missing version information at {root_path}")

    return SdkLayout(
        root=root_path,
        frameworks_dir=frameworks,
        swift_lib_dir=swift_lib,
        usr_lib_dir=usr_lib,
        platform_version=str(version),
    )
