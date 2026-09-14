import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any
import uuid

from iosc.account.anisette.base import AnisetteHeaders, client_time
from iosc.account.anisette.remote import (
    DEFAULT_CLIENT_INFO,
    end_provisioning,
    start_provisioning,
)
from iosc.config.paths import adi_bridge, adi_lib_dir, require, state_dir
from iosc.core import AnisetteError, get_reporter, write_atomic

if sys.platform == "win32":
    SEM_FAILCRITICALERRORS = 0x0001
    SEM_NOGPFAULTERRORBOX = 0x0002
    try:
        ctypes.windll.kernel32.SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX)
    except Exception:
        pass

DEFAULT_DSID = 0xFFFFFFFFFFFFFFFE


def bind_adi_native(dll_path: Path | str | None = None) -> ctypes.WinDLL:
    if dll_path is None:
        path = require(adi_bridge())
    else:
        path = Path(dll_path)
        if not path.is_file():
            raise AnisetteError(f"Native bridge DLL not found at {path}")

    bridge = ctypes.WinDLL(str(path))

    bridge.adi_init.argtypes = [ctypes.c_char_p]
    bridge.adi_init.restype = ctypes.c_int

    bridge.adi_LoadLibraryWithPath.argtypes = [ctypes.c_char_p]
    bridge.adi_LoadLibraryWithPath.restype = ctypes.c_int

    bridge.adi_SetProvisioningPath.argtypes = [ctypes.c_char_p]
    bridge.adi_SetProvisioningPath.restype = ctypes.c_int

    bridge.adi_SetIdentifier.argtypes = [ctypes.c_char_p, ctypes.c_uint32]
    bridge.adi_SetIdentifier.restype = ctypes.c_int

    bridge.adi_Start.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    bridge.adi_Start.restype = ctypes.c_int

    bridge.adi_End.argtypes = [
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint32,
    ]
    bridge.adi_End.restype = ctypes.c_int

    bridge.adi_GetLoginCode.argtypes = [ctypes.c_uint64]
    bridge.adi_GetLoginCode.restype = ctypes.c_int

    bridge.adi_RequestOTP.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    bridge.adi_RequestOTP.restype = ctypes.c_int

    bridge.adi_Synchronize.argtypes = [
        ctypes.c_uint64,
        ctypes.POINTER(ctypes.c_uint8),
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint8)),
        ctypes.POINTER(ctypes.c_uint32),
    ]
    bridge.adi_Synchronize.restype = ctypes.c_int

    bridge.adi_Erase.argtypes = [ctypes.c_uint64]
    bridge.adi_Erase.restype = ctypes.c_int

    bridge.adi_Destroy.argtypes = [ctypes.c_uint32]
    bridge.adi_Destroy.restype = ctypes.c_int

    bridge.adi_Dispose.argtypes = [ctypes.c_void_p]
    bridge.adi_Dispose.restype = ctypes.c_int

    bridge.adi_get_missing_symbols_count.argtypes = []
    bridge.adi_get_missing_symbols_count.restype = ctypes.c_int

    bridge.adi_get_missing_symbol.argtypes = [ctypes.c_int]
    bridge.adi_get_missing_symbol.restype = ctypes.c_char_p

    bridge.adi_get_stub_hit_count.argtypes = []
    bridge.adi_get_stub_hit_count.restype = ctypes.c_int

    return bridge


def generate_device_identity(dev_uuid: uuid.UUID | None = None) -> tuple[str, str, str]:
    if dev_uuid is None:
        dev_uuid = uuid.uuid4()
    device_id = str(dev_uuid).upper()
    local_user_uuid = hashlib.sha256(dev_uuid.bytes).hexdigest().upper()
    identifier = device_id[:16]
    return device_id, local_user_uuid, identifier


def check_stub_hits(bridge: Any) -> int:
    if not hasattr(bridge, "adi_get_stub_hit_count"):
        return 0
    stub_hits = bridge.adi_get_stub_hit_count()
    if stub_hits > 0:
        count = bridge.adi_get_missing_symbols_count()
        hit_list: list[str] = []
        for i in range(count):
            sym = bridge.adi_get_missing_symbol(i)
            if sym:
                hit_list.append(sym.decode("utf-8", errors="replace"))
        get_reporter().warn(f"{stub_hits} missing symbol stub hits: {hit_list}")
    return stub_hits


class LocalADIProvider:
    def __init__(
        self,
        device_id: str | None = None,
        local_user_uuid: str | None = None,
        identifier: str | None = None,
        provisioning_dir: Path | str | None = None,
        vendor_dir: Path | str | None = None,
        dll_path: Path | str | None = None,
        dsid: int = DEFAULT_DSID,
        client_info: str | None = None,
        routing_info: str | None = None,
        verify_ssl: bool = False,
        timeout: int = 15,
        bridge: Any | None = None,
    ) -> None:
        if provisioning_dir is not None:
            self.provisioning_dir = Path(provisioning_dir)
        else:
            self.provisioning_dir = state_dir() / "adi"

        self.provisioning_dir.mkdir(parents=True, exist_ok=True)
        self._identity_path = self.provisioning_dir / "device.json"

        saved: dict[str, Any] = {}
        if self._identity_path.is_file():
            try:
                saved = json.loads(self._identity_path.read_text(encoding="utf-8"))
            except Exception:
                saved = {}

        gen_dev_id, gen_lu_uuid, gen_ident = generate_device_identity()
        self.device_id = device_id or saved.get("device_id") or gen_dev_id
        self.local_user_uuid = local_user_uuid or saved.get("local_user_uuid") or gen_lu_uuid
        self.identifier = identifier or saved.get("identifier") or gen_ident
        self.routing_info = routing_info or saved.get("routing_info") or "17106176"
        self.client_info = client_info or saved.get("client_info") or DEFAULT_CLIENT_INFO
        self._save_identity()

        self.vendor_dir = Path(vendor_dir) if vendor_dir is not None else adi_lib_dir().path
        self.dll_path = Path(dll_path) if dll_path is not None else adi_bridge().path
        self.dsid = dsid
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self.bridge = bridge
        self._initialized = False

    def _save_identity(self) -> None:
        data = {
            "device_id": self.device_id,
            "local_user_uuid": self.local_user_uuid,
            "identifier": self.identifier,
            "routing_info": self.routing_info,
            "client_info": self.client_info,
        }
        write_atomic(self._identity_path, (json.dumps(data, indent=2) + "\n").encode("utf-8"))

    def _ensure_bridge(self) -> None:
        if self._initialized:
            return

        if self.bridge is None:
            if not self.vendor_dir.is_dir():
                raise AnisetteError(f"ADI vendor directory not found: {self.vendor_dir}")
            if not self.dll_path.is_file():
                raise AnisetteError(f"Native bridge DLL not found: {self.dll_path}")
            try:
                self.bridge = bind_adi_native(self.dll_path)
            except Exception as exc:
                raise AnisetteError(f"Failed to load native bridge DLL {self.dll_path}: {exc}") from exc

        vendor_bytes = str(self.vendor_dir).encode("utf-8")
        rc_init = self.bridge.adi_init(vendor_bytes)
        if rc_init != 0:
            raise AnisetteError(f"adi_init failed with code {rc_init}")

        rc_load = self.bridge.adi_LoadLibraryWithPath(vendor_bytes)
        if rc_load != 0:
            raise AnisetteError(f"adi_LoadLibraryWithPath failed with code {rc_load}")

        prov_bytes = str(self.provisioning_dir).encode("utf-8")
        rc_prov = self.bridge.adi_SetProvisioningPath(prov_bytes)
        if rc_prov != 0:
            raise AnisetteError(f"adi_SetProvisioningPath failed with code {rc_prov}")

        ident_bytes = self.identifier.encode("ascii")
        rc_ident = self.bridge.adi_SetIdentifier(ident_bytes, len(ident_bytes))
        if rc_ident != 0:
            raise AnisetteError(f"adi_SetIdentifier failed with code {rc_ident}")

        self._initialized = True

    def is_provisioned(self) -> bool:
        self._ensure_bridge()
        return self.bridge.adi_GetLoginCode(self.dsid) == 0

    def provision(self) -> None:
        self._ensure_bridge()
        spim_bytes, session_obj = start_provisioning(
            device_id=self.device_id,
            local_user_uuid=self.local_user_uuid,
            verify_ssl=self.verify_ssl,
            timeout=self.timeout,
        )

        spim_buf = (ctypes.c_uint8 * len(spim_bytes)).from_buffer_copy(spim_bytes)
        out_cpim_ptr = ctypes.POINTER(ctypes.c_uint8)()
        out_cpim_len = ctypes.c_uint32(0)
        out_session = ctypes.c_uint32(0)

        rc_start = self.bridge.adi_Start(
            self.dsid,
            spim_buf,
            len(spim_bytes),
            ctypes.byref(out_cpim_ptr),
            ctypes.byref(out_cpim_len),
            ctypes.byref(out_session),
        )
        if rc_start != 0:
            raise AnisetteError(f"adi_Start failed with code {rc_start}")

        try:
            cpim_bytes = bytes(ctypes.string_at(out_cpim_ptr, out_cpim_len.value))
        finally:
            self.bridge.adi_Dispose(out_cpim_ptr)

        prov_result = end_provisioning(
            session=session_obj,
            cpim=cpim_bytes,
            timeout=self.timeout,
        )

        ptm_buf = (ctypes.c_uint8 * len(prov_result.ptm)).from_buffer_copy(prov_result.ptm)
        tk_buf = (ctypes.c_uint8 * len(prov_result.tk)).from_buffer_copy(prov_result.tk)

        rc_end = self.bridge.adi_End(
            out_session.value,
            ptm_buf,
            len(prov_result.ptm),
            tk_buf,
            len(prov_result.tk),
        )
        if rc_end != 0:
            raise AnisetteError(f"adi_End failed with code {rc_end}")

        rc_login = self.bridge.adi_GetLoginCode(self.dsid)
        if rc_login != 0:
            raise AnisetteError(f"adi_GetLoginCode failed with code {rc_login}")

        if prov_result.routing_info is not None:
            self.routing_info = str(prov_result.routing_info)
        self._save_identity()

    def request_otp(self) -> tuple[bytes, bytes]:
        self._ensure_bridge()
        mid_ptr = ctypes.POINTER(ctypes.c_uint8)()
        mid_len = ctypes.c_uint32(0)
        otp_ptr = ctypes.POINTER(ctypes.c_uint8)()
        otp_len = ctypes.c_uint32(0)

        rc_otp = self.bridge.adi_RequestOTP(
            self.dsid,
            ctypes.byref(mid_ptr),
            ctypes.byref(mid_len),
            ctypes.byref(otp_ptr),
            ctypes.byref(otp_len),
        )
        if rc_otp != 0:
            raise AnisetteError(f"adi_RequestOTP failed with code {rc_otp}")

        try:
            mid_bytes = bytes(ctypes.string_at(mid_ptr, mid_len.value))
            otp_bytes = bytes(ctypes.string_at(otp_ptr, otp_len.value))
        finally:
            self.bridge.adi_Dispose(mid_ptr)
            self.bridge.adi_Dispose(otp_ptr)

        return mid_bytes, otp_bytes

    def fetch(self) -> dict[str, str]:
        self._ensure_bridge()
        try:
            if not self.is_provisioned():
                self.provision()
            mid_bytes, otp_bytes = self.request_otp()
        finally:
            check_stub_hits(self.bridge)

        return {
            "X-Apple-I-MD": base64.b64encode(otp_bytes).decode("ascii"),
            "X-Apple-I-MD-M": base64.b64encode(mid_bytes).decode("ascii"),
            "X-Apple-I-MD-RINFO": str(self.routing_info),
            "X-Apple-I-MD-LU": self.local_user_uuid,
            "X-Mme-Device-Id": self.device_id,
            "X-Mme-Client-Info": self.client_info,
            "X-Apple-I-Client-Time": client_time(),
            "X-Apple-I-TimeZone": "UTC",
            "X-Apple-Locale": "en_US",
            "X-Apple-I-SRL-NO": "0",
        }

    def headers(self) -> AnisetteHeaders:
        raw = self.fetch()
        return AnisetteHeaders.from_dict(raw)
