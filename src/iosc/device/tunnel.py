import asyncio
import importlib.util
import plistlib
import ssl
from contextlib import AsyncExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator

from iosc.core.errors import DeviceError, MissingExternalAsset
from iosc.device.install import ProgressCallback
from iosc.device.transport import DeviceInfo

INSTALL_HINT = "pip install 'pymobiledevice3>=11.49'"
NETWORK = "Network"
PORT_PROPERTY = "RemotePairingPort"
DISCOVERY_TIMEOUT = 5.0
# the library default of 1s loses to a dozing phone
CONNECT_TIMEOUT = 10.0

# ios 27 refuses installation_proxy over plain wifi lockdown
# installs go through the remote pairing tunnel instead


def is_available() -> bool:
    return importlib.util.find_spec("pymobiledevice3") is not None


def ensure_available() -> None:
    if not is_available():
        raise MissingExternalAsset(
            "pymobiledevice3 is not installed, needed for installs over wifi. "
            f"Install it with '{INSTALL_HINT}'."
        )


# the tunnel is tls psk. python has it from 3.13
def supports_tunnel() -> bool:
    return hasattr(ssl.SSLContext, "set_psk_client_callback")


def ensure_tunnel_support() -> None:
    ensure_available()
    if not supports_tunnel():
        raise DeviceError("installs over wifi need Python 3.13 or newer for the tls psk tunnel")


def record_path(udid: str) -> Path:
    ensure_available()
    from pymobiledevice3.pair_records import (
        create_pairing_records_cache_folder,
        get_remote_pairing_record_filename,
    )
    from pymobiledevice3.remote.tunnel_service import PAIRING_RECORD_EXT

    folder = create_pairing_records_cache_folder()
    return folder / f"{get_remote_pairing_record_filename(udid)}.{PAIRING_RECORD_EXT}"


def has_pairing(udid: str) -> bool:
    return record_path(udid).is_file()


def run(coroutine) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coroutine)
    finally:
        loop.close()


# promptless over a trusted usb lockdown connection
# a fresh pairing hands out the altirk that wifi discovery matches on
def pair(udid: str) -> Path:
    ensure_available()
    path = record_path(udid)
    previous = path.read_bytes() if path.is_file() else None
    path.unlink(missing_ok=True)
    try:
        run(_pair(udid))
    except BaseException:
        if previous is not None and not path.is_file():
            path.write_bytes(previous)
        raise
    if not path.is_file():
        raise DeviceError(f"remote pairing with {udid} finished without a pair record")
    stamp_host_identifier(path)
    return path


async def _pair(udid: str) -> None:
    from pymobiledevice3.exceptions import RemotePairingCompletedError
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.remote.tunnel_service import RemotePairingLockdownService

    lockdown = await create_using_usbmux(serial=udid, autopair=False)
    try:
        service = await RemotePairingLockdownService.create(lockdown)
        try:
            await service.connect(autopair=True)
        except RemotePairingCompletedError:
            pass
        finally:
            await service.close()
    finally:
        await lockdown.close()


# the library derives the host id from the hostname
# pinning it lets a copied record work on another machine
def stamp_host_identifier(path: Path) -> None:
    from pymobiledevice3.pair_records import generate_host_id

    record = plistlib.loads(path.read_bytes())
    record.setdefault("host_identifier", generate_host_id())
    path.write_bytes(plistlib.dumps(record))


def find_device(udid: str | None = None, timeout: float = DISCOVERY_TIMEOUT) -> DeviceInfo:
    ensure_tunnel_support()
    found = run(_discover(udid, timeout))
    if not found:
        raise DeviceError(
            "no paired device answered on the network. Pair once over usb with "
            "'iosc wifi pair', keep the phone unlocked and on the same network"
            if udid is None
            else f"device {udid} did not answer on the network"
        )
    if udid is None and len(found) > 1:
        names = ", ".join(d.serial for d in found)
        raise DeviceError(f"more than one paired device on the network, pass --udid to pick one of {names}")
    return found[0]


async def _discover(udid: str | None, timeout: float) -> list[DeviceInfo]:
    from pymobiledevice3.remote import tunnel_service

    tunnel_service.TIMEOUT = max(tunnel_service.TIMEOUT, CONNECT_TIMEOUT)
    services = await tunnel_service.get_remote_pairing_tunnel_services(bonjour_timeout=timeout, udid=udid)
    found: dict[str, DeviceInfo] = {}
    for service in services:
        found.setdefault(service.remote_identifier, network_info(service.remote_identifier, service.hostname, service.port))
        await service.close()
    return list(found.values())


def network_info(udid: str, address: str, port: int) -> DeviceInfo:
    return DeviceInfo(
        device_id=0,
        serial=udid,
        connection_type=NETWORK,
        properties={PORT_PROPERTY: port},
        address=address,
    )


class Session:
    def __init__(self, loop: asyncio.AbstractEventLoop, rsd) -> None:
        self.loop = loop
        self.rsd = rsd

    def _proxy(self):
        from pymobiledevice3.services.installation_proxy import InstallationProxyService

        return InstallationProxyService(lockdown=self.rsd)

    def install(self, package: Path | str, progress: ProgressCallback | None = None) -> dict[str, Any]:
        local = Path(package)
        handler = (lambda percent, *_: progress(int(percent), "installing")) if progress else None
        self.loop.run_until_complete(
            self._proxy().install_from_local(local, handler=handler, developer=local.is_dir())
        )
        return {"Status": "Complete"}

    def list_apps(self, application_type: str = "User") -> dict[str, Any]:
        return self.loop.run_until_complete(self._proxy().get_apps(application_type=application_type))


@contextmanager
def open_session(info: DeviceInfo) -> Iterator[Session]:
    ensure_tunnel_support()
    if not info.address or PORT_PROPERTY not in info.properties:
        info = find_device(info.serial)
    loop = asyncio.new_event_loop()
    stack = AsyncExitStack()
    try:
        rsd = loop.run_until_complete(_open(stack, info))
        yield Session(loop, rsd)
    finally:
        try:
            loop.run_until_complete(_close(stack))
        finally:
            loop.close()


# same chain as pymobiledevice3 UserspaceRsdTunnel, minus its usbmux step
async def _open(stack: AsyncExitStack, info: DeviceInfo):
    from pymobiledevice3.remote import tunnel_service
    from pymobiledevice3.remote.remote_service_discovery import RemoteServiceDiscoveryService
    from pymobiledevice3.remote.userspace_tunnel import UserspaceDialPlane

    tunnel_service.TIMEOUT = max(tunnel_service.TIMEOUT, CONNECT_TIMEOUT)
    service = tunnel_service.RemotePairingTunnelService(info.serial, info.address, info.properties[PORT_PROPERTY])
    stack.push_async_callback(service.close)
    try:
        await service.connect(autopair=False)
    except (OSError, asyncio.TimeoutError) as error:
        raise DeviceError(f"cannot reach {info.serial} at {info.address} ({error!r})") from error

    # the userspace stack needs no root or tun device
    tunnel_service.USE_USERSPACE_TUNNEL = True
    result = await stack.enter_async_context(service.start_tcp_tunnel())
    tun = result.client.tun
    tun.set_peer(result.address)
    dial_plane = await stack.enter_async_context(UserspaceDialPlane(tun, result.address))
    rsd = RemoteServiceDiscoveryService(
        (result.address, result.port),
        open_connection=dial_plane.dial,
        auxiliary_metadata=result.auxiliary_metadata,
    )
    stack.push_async_callback(rsd.close)
    await rsd.connect()
    return rsd


async def _close(stack: AsyncExitStack) -> None:
    from pymobiledevice3.remote import tunnel_service

    try:
        await stack.aclose()
    finally:
        tunnel_service.USE_USERSPACE_TUNNEL = False
