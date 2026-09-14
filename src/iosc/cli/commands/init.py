import re

from iosc.cli.args import project_dir
from iosc.config.manifest import RE_BUNDLE_ID, default_manifest
from iosc.core import logging
from iosc.core.errors import UsageError

MANIFEST_NAME = "iosc.toml"
RE_UNSAFE = re.compile(r"[^A-Za-z0-9-]+")


def default_bundle_id(name: str) -> str:
    segment = RE_UNSAFE.sub("-", name).strip("-").lower()
    return f"com.example.{segment or 'app'}"


# check now, not at first build
# catches a shell-mangled argument while the user remembers typing it
def check_bundle_id(bundle_id: str) -> str:
    if not RE_BUNDLE_ID.match(bundle_id):
        raise UsageError(
            f"'{bundle_id}' is not a usable bundle id. It reads like com.yourname.appname, "
            "two or more parts separated by dots, each part letters digits or hyphens. "
            "Angle brackets in an example mean replace this, do not type them"
        )
    return bundle_id


def run(args) -> None:
    directory = project_dir(args)
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.exists():
        raise UsageError(f"{manifest_path} already exists")

    name = args.name or directory.resolve().name
    bundle_id = check_bundle_id(args.bundle_id or default_bundle_id(name))

    directory.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(default_manifest(name, bundle_id), encoding="utf-8")
    (directory / "Sources").mkdir(exist_ok=True)

    if args.json:
        logging.result(
            {"manifest": str(manifest_path), "name": name, "bundle_id": bundle_id}
        )
        return
    logging.success(f"wrote {manifest_path} for {bundle_id}")
