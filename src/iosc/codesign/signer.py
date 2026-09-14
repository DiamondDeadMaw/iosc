from dataclasses import dataclass
import datetime
import hashlib
import os
from pathlib import Path
import struct
from typing import Any

from iosc.core.errors import SigningError
from iosc.formats.mobileprovision import PROFILE_NAME, Profile
from iosc.formats.pki import SigningIdentity
from iosc.formats.plist import read_plist
from iosc.codesign import cms
from iosc.codesign.directory import (
    CD_HEADER_SIZE,
    CS_ADHOC,
    CS_EXECSEG_ALLOW_UNSIGNED,
    CS_EXECSEG_MAIN_BINARY,
    CSMAGIC_BLOBWRAPPER,
    CSMAGIC_CODEDIRECTORY,
    CSSLOT_CODEDIRECTORY,
    CSSLOT_ENTITLEMENTS,
    CSSLOT_ENTITLEMENTS_DER,
    CSSLOT_REQUIREMENTS,
    CSSLOT_SIGNATURE,
    HASH_SIZE_SHA256,
    LC_CODE_SIGNATURE,
    LINKEDIT_DATA_COMMAND_SIZE,
    LINKEDIT_VM_ALIGNMENT,
    MH_EXECUTE,
    PAGE_SIZE,
    SIGNATURE_ALIGNMENT,
    SPECIAL_SLOT_ENTITLEMENTS,
    SPECIAL_SLOT_ENTITLEMENTS_DER,
    SPECIAL_SLOT_INFO,
    SPECIAL_SLOT_REQUIREMENTS,
    SPECIAL_SLOT_RESOURCES,
    align_to,
    blob,
    build_code_directory,
    build_requirements_blob,
    build_superblob,
    cdhash,
    parse_macho,
)
from iosc.codesign.entitlements import build_entitlements_blobs
from iosc.codesign.resources import (
    CODE_RESOURCES_NAME,
    CODE_SIGNATURE_DIR,
    build_code_resources,
)


@dataclass(frozen=True)
class DylibSignResult:
    path: str
    identifier: str
    cdhash: bytes


@dataclass(frozen=True)
class BundleSignResult:
    identifier: str
    cdhash: bytes
    resources: int
    size: int
    adhoc: bool
    team_id: str | None
    profile: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AppSignResult:
    app: BundleSignResult
    frameworks: tuple[BundleSignResult, ...]
    dylibs: tuple[DylibSignResult, ...]
    plugins: tuple[BundleSignResult, ...]
    identifier: str
    cdhash: bytes
    resources: int
    size: int
    adhoc: bool
    team_id: str | None
    profile: str | None
    warnings: tuple[str, ...]


def _signature_size(
    identifier: str,
    code_limit: int,
    special_count: int,
    sub_blobs: list[tuple[int, bytes]],
    team_id: str | None = None,
) -> int:
    identifier_length = len(identifier.encode("utf-8")) + 1
    team_length = (len(team_id.encode("utf-8")) + 1) if team_id else 0
    code_slots = (code_limit + PAGE_SIZE - 1) // PAGE_SIZE
    cd_length = (
        CD_HEADER_SIZE
        + identifier_length
        + team_length
        + (special_count + code_slots) * HASH_SIZE_SHA256
    )
    blob_count = 1 + len(sub_blobs)
    return 12 + blob_count * 8 + cd_length + sum(len(b) for _, b in sub_blobs)


def _empty_signature_blob(
    identity: SigningIdentity,
    signing_time: datetime.datetime | None,
) -> bytes:
    return blob(
        CSMAGIC_BLOBWRAPPER,
        cms.build_signature(identity, [b"\x00" * CD_HEADER_SIZE], signing_time),
    )


def sign_macho(
    data: bytes,
    identifier: str,
    entitlements: dict[str, Any] | None = None,
    info_plist: bytes | None = None,
    code_resources: bytes | None = None,
    flags: int | None = None,
    team_id: str | None = None,
    platform: int = 0,
    identity: SigningIdentity | None = None,
    signing_time: datetime.datetime | None = None,
) -> bytes:
    if flags is None:
        flags = 0 if identity else CS_ADHOC
    if identity is not None and flags & CS_ADHOC:
        raise SigningError("a signature with a real identity cannot be ad-hoc")
    if identity is not None and team_id is None:
        team_id = identity.team_id

    info = parse_macho(data)
    text = info["segments"].get("__TEXT")
    linkedit = info["segments"].get("__LINKEDIT")
    if text is None:
        raise SigningError("binary has no __TEXT segment")
    if linkedit is None:
        raise SigningError("binary has no __LINKEDIT segment, cannot place a signature")

    existing = info["signature"]
    unsigned_length = existing["dataoff"] if existing else len(data)
    content = bytearray(data[:unsigned_length])

    if existing:
        command_offset = existing["command_offset"]
    else:
        command_offset = 32 + info["sizeofcmds"]
        free = (info["first_section_offset"] or len(content)) - command_offset
        if free < LINKEDIT_DATA_COMMAND_SIZE:
            raise SigningError(
                f"no room for LC_CODE_SIGNATURE: {free} free header bytes, "
                f"needs {LINKEDIT_DATA_COMMAND_SIZE}"
            )
        struct.pack_into(
            "<II",
            content,
            16,
            info["ncmds"] + 1,
            info["sizeofcmds"] + LINKEDIT_DATA_COMMAND_SIZE,
        )

    code_limit = align_to(len(content), SIGNATURE_ALIGNMENT)
    content.extend(b"\x00" * (code_limit - len(content)))

    sub_blobs: list[tuple[int, bytes]] = [
        (CSSLOT_REQUIREMENTS, build_requirements_blob(identifier, identity))
    ]
    special: dict[int, bytes] = {
        SPECIAL_SLOT_REQUIREMENTS: hashlib.sha256(sub_blobs[0][1]).digest()
    }
    if info_plist is not None:
        special[SPECIAL_SLOT_INFO] = hashlib.sha256(info_plist).digest()
    if code_resources is not None:
        special[SPECIAL_SLOT_RESOURCES] = hashlib.sha256(code_resources).digest()
    if entitlements:
        xml_blob, der_blob = build_entitlements_blobs(entitlements)
        sub_blobs.append((CSSLOT_ENTITLEMENTS, xml_blob))
        sub_blobs.append((CSSLOT_ENTITLEMENTS_DER, der_blob))
        special[SPECIAL_SLOT_ENTITLEMENTS] = hashlib.sha256(xml_blob).digest()
        special[SPECIAL_SLOT_ENTITLEMENTS_DER] = hashlib.sha256(der_blob).digest()

    special_count = max(special)
    sized_blobs = list(sub_blobs)
    if identity is not None:
        sized_blobs.append(
            (CSSLOT_SIGNATURE, _empty_signature_blob(identity, signing_time))
        )
    logical = _signature_size(identifier, code_limit, special_count, sized_blobs, team_id)
    datasize = align_to(logical, SIGNATURE_ALIGNMENT)

    struct.pack_into(
        "<IIII",
        content,
        command_offset,
        LC_CODE_SIGNATURE,
        LINKEDIT_DATA_COMMAND_SIZE,
        code_limit,
        datasize,
    )

    linkedit_filesize = code_limit + datasize - linkedit["fileoff"]
    struct.pack_into(
        "<Q",
        content,
        linkedit["command_offset"] + 32,
        align_to(linkedit_filesize, LINKEDIT_VM_ALIGNMENT),
    )
    struct.pack_into(
        "<Q",
        content,
        linkedit["command_offset"] + 48,
        linkedit_filesize,
    )

    exec_seg_flags = CS_EXECSEG_MAIN_BINARY if info["filetype"] == MH_EXECUTE else 0
    if (
        identity is not None
        and info["filetype"] == MH_EXECUTE
        and entitlements
        and entitlements.get("get-task-allow")
    ):
        exec_seg_flags |= CS_EXECSEG_ALLOW_UNSIGNED

    directory = build_code_directory(
        identifier,
        code_limit,
        bytes(content),
        special,
        text["filesize"],
        exec_seg_flags,
        flags,
        team_id,
        platform,
    )
    if identity is not None:
        sub_blobs.append(
            (
                CSSLOT_SIGNATURE,
                blob(
                    CSMAGIC_BLOBWRAPPER,
                    cms.build_signature(identity, [directory], signing_time),
                ),
            )
        )
    superblob = build_superblob([(CSSLOT_CODEDIRECTORY, directory)] + sub_blobs)
    if len(superblob) != logical:
        raise SigningError(
            f"predicted signature size {logical} but built {len(superblob)}"
        )
    return bytes(content) + superblob + b"\x00" * (datasize - len(superblob))


def install_profile(
    bundle_dir: str | Path,
    profile: Profile | bytes | str | Path,
) -> Profile:
    if isinstance(profile, (str, Path)):
        profile_obj = Profile.from_file(profile)
    elif isinstance(profile, bytes):
        profile_obj = Profile.from_bytes(profile)
    elif isinstance(profile, Profile):
        profile_obj = profile
    else:
        raise SigningError(f"unsupported profile type: {type(profile).__name__}")

    bundle_path = Path(bundle_dir)
    target = bundle_path / PROFILE_NAME
    temp = target.with_name(target.name + ".partial")
    with open(temp, "wb") as f:
        f.write(profile_obj.data)
    os.replace(temp, target)
    return profile_obj


def sign_bundle(
    bundle_dir: str | Path,
    identifier: str | None = None,
    entitlements: dict[str, Any] | None = None,
    flags: int | None = None,
    identity: SigningIdentity | None = None,
    profile: Profile | bytes | str | Path | None = None,
    signing_time: datetime.datetime | None = None,
    udid: str | None = None,
) -> BundleSignResult:
    bundle_path = Path(bundle_dir)
    info_path = bundle_path / "Info.plist"
    if not info_path.is_file():
        raise SigningError(f"{bundle_dir} has no Info.plist")
    with open(info_path, "rb") as f:
        info_bytes = f.read()
    info = read_plist(info_bytes)

    executable = info.get("CFBundleExecutable")
    if not executable:
        raise SigningError("Info.plist has no CFBundleExecutable")
    executable_path = bundle_path / executable
    if not executable_path.is_file():
        raise SigningError(f"missing executable {executable}")

    identifier = identifier or info.get("CFBundleIdentifier") or executable

    warnings: list[str] = []
    profile_obj: Profile | None = None
    if profile is not None:
        profile_obj = install_profile(bundle_dir, profile)
        certificate = identity.certificate if identity else None
        problems = profile_obj.problems(identifier, udid, certificate)
        if problems:
            raise SigningError(
                "provisioning profile does not fit this build: " + "; ".join(problems)
            )
        warnings.extend(profile_obj.warnings())
        if entitlements is None:
            entitlements = profile_obj.entitlements_for(identifier)
    if identity is not None:
        warnings.extend(identity.problems())

    resources = build_code_resources(bundle_path, executable)
    sig_dir = bundle_path / CODE_SIGNATURE_DIR
    sig_dir.mkdir(exist_ok=True)
    with open(sig_dir / CODE_RESOURCES_NAME, "wb") as f:
        f.write(resources)

    with open(executable_path, "rb") as f:
        binary = f.read()
    signed = sign_macho(
        binary,
        identifier,
        entitlements=entitlements,
        info_plist=info_bytes,
        code_resources=resources,
        flags=flags,
        identity=identity,
        signing_time=signing_time,
    )
    temp = executable_path.with_name(executable_path.name + ".partial")
    with open(temp, "wb") as f:
        f.write(signed)
    os.replace(temp, executable_path)

    resources_plist = read_plist(resources)
    return BundleSignResult(
        identifier=identifier,
        cdhash=cdhash(signed),
        resources=len(resources_plist.get("files", {})),
        size=len(signed),
        adhoc=identity is None,
        team_id=identity.team_id if identity else None,
        profile=profile_obj.uuid if profile_obj else None,
        warnings=tuple(warnings),
    )


def sign_app_recursive(
    app_dir: str | Path,
    identity: SigningIdentity | None = None,
    profile: Profile | bytes | str | Path | None = None,
    signing_time: datetime.datetime | None = None,
    udid: str | None = None,
) -> AppSignResult:
    app_path = Path(app_dir)
    app_info_path = app_path / "Info.plist"
    if not app_info_path.is_file():
        raise SigningError(f"{app_dir} has no Info.plist")
    with open(app_info_path, "rb") as f:
        app_info = read_plist(f.read())

    app_bundle_id = app_info.get("CFBundleIdentifier")
    if not app_bundle_id:
        raise SigningError("app Info.plist has no CFBundleIdentifier")

    profile_obj: Profile | None = None
    if profile is not None:
        if isinstance(profile, (str, Path)):
            profile_obj = Profile.from_file(profile)
        elif isinstance(profile, bytes):
            profile_obj = Profile.from_bytes(profile)
        elif isinstance(profile, Profile):
            profile_obj = profile
        else:
            raise SigningError(f"unsupported profile type: {type(profile).__name__}")

        if not profile_obj.matches_bundle_id(app_bundle_id):
            raise SigningError(
                f"provisioning profile does not cover app bundle id {app_bundle_id!r}"
            )
        certificate = identity.certificate if identity else None
        problems = profile_obj.problems(app_bundle_id, udid, certificate)
        if problems:
            raise SigningError(
                "provisioning profile does not fit this build: " + "; ".join(problems)
            )
        app_entitlements = profile_obj.entitlements_for(app_bundle_id)
        if profile_obj.team_id:
            app_entitlements["application-identifier"] = f"{profile_obj.team_id}.{app_bundle_id}"
            app_entitlements["com.apple.developer.team-identifier"] = profile_obj.team_id
        app_entitlements["get-task-allow"] = True
    else:
        team_id = identity.team_id if identity else None
        if team_id:
            app_entitlements = {
                "application-identifier": f"{team_id}.{app_bundle_id}",
                "com.apple.developer.team-identifier": team_id,
                "get-task-allow": True,
            }
        else:
            app_entitlements = None

    dylibs_signed: list[DylibSignResult] = []
    frameworks_signed: list[BundleSignResult] = []
    frameworks_dir = app_path / "Frameworks"
    if frameworks_dir.is_dir():
        for root, dirs, files in os.walk(frameworks_dir):
            dirs[:] = [d for d in dirs if not d.endswith(".framework")]
            for name in sorted(files):
                if name.endswith(".dylib"):
                    dylib_path = Path(root) / name
                    if dylib_path.is_symlink():
                        continue
                    with open(dylib_path, "rb") as f:
                        dylib_data = f.read()
                    signed = sign_macho(
                        dylib_data,
                        identifier=name,
                        identity=identity,
                        signing_time=signing_time,
                    )
                    temp = dylib_path.with_name(dylib_path.name + ".partial")
                    with open(temp, "wb") as f:
                        f.write(signed)
                    os.replace(temp, dylib_path)
                    dylibs_signed.append(
                        DylibSignResult(
                            path=dylib_path.relative_to(app_path).as_posix(),
                            identifier=name,
                            cdhash=cdhash(signed),
                        )
                    )

        for name in sorted(os.listdir(frameworks_dir)):
            fw_path = frameworks_dir / name
            if name.endswith(".framework") and fw_path.is_dir():
                fw_result = sign_bundle(
                    fw_path,
                    identity=identity,
                    profile=None,
                    signing_time=signing_time,
                )
                frameworks_signed.append(fw_result)

    plugins_signed: list[BundleSignResult] = []
    plugins_dir = app_path / "PlugIns"
    if plugins_dir.is_dir():
        for name in sorted(os.listdir(plugins_dir)):
            ext_dir = plugins_dir / name
            if name.endswith(".appex") and ext_dir.is_dir():
                ext_info_path = ext_dir / "Info.plist"
                if not ext_info_path.is_file():
                    raise SigningError(f"extension {name} has no Info.plist")
                with open(ext_info_path, "rb") as f:
                    ext_info = read_plist(f.read())
                ext_bundle_id = ext_info.get("CFBundleIdentifier")
                if not ext_bundle_id:
                    raise SigningError(f"extension {name} has no CFBundleIdentifier")

                prefix = app_bundle_id + "."
                if not (ext_bundle_id.startswith(prefix) and len(ext_bundle_id) > len(prefix)):
                    raise SigningError(
                        f"extension bundle id {ext_bundle_id!r} is not a suffix of app bundle id {app_bundle_id!r}"
                    )

                if profile_obj is not None:
                    if not profile_obj.matches_bundle_id(ext_bundle_id):
                        raise SigningError(
                            f"provisioning profile does not cover extension bundle id {ext_bundle_id!r}"
                        )
                    certificate = identity.certificate if identity else None
                    problems = profile_obj.problems(ext_bundle_id, udid, certificate)
                    if problems:
                        raise SigningError(
                            f"provisioning profile does not fit extension {ext_bundle_id}: "
                            + "; ".join(problems)
                        )
                    ext_entitlements = profile_obj.entitlements_for(ext_bundle_id)
                    if profile_obj.team_id:
                        ext_entitlements["application-identifier"] = (
                            f"{profile_obj.team_id}.{ext_bundle_id}"
                        )
                        ext_entitlements["com.apple.developer.team-identifier"] = profile_obj.team_id
                    ext_entitlements["get-task-allow"] = True
                else:
                    team_id = identity.team_id if identity else None
                    if team_id:
                        ext_entitlements = {
                            "application-identifier": f"{team_id}.{ext_bundle_id}",
                            "com.apple.developer.team-identifier": team_id,
                            "get-task-allow": True,
                        }
                    else:
                        ext_entitlements = None

                ext_result = sign_bundle(
                    ext_dir,
                    identifier=ext_bundle_id,
                    entitlements=ext_entitlements,
                    identity=identity,
                    profile=None,
                    signing_time=signing_time,
                )
                plugins_signed.append(ext_result)

    app_result = sign_bundle(
        app_path,
        identifier=app_bundle_id,
        entitlements=app_entitlements,
        identity=identity,
        profile=profile_obj,
        signing_time=signing_time,
        udid=udid,
    )

    return AppSignResult(
        app=app_result,
        frameworks=tuple(frameworks_signed),
        dylibs=tuple(dylibs_signed),
        plugins=tuple(plugins_signed),
        identifier=app_result.identifier,
        cdhash=app_result.cdhash,
        resources=app_result.resources,
        size=app_result.size,
        adhoc=app_result.adhoc,
        team_id=app_result.team_id,
        profile=app_result.profile,
        warnings=app_result.warnings,
    )
