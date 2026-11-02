import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from iosc.config import paths
from iosc.core import get_reporter
from iosc.core.errors import DeviceError
from iosc.device import afc, install, mdns, services, transport, tunnel
from iosc.device.lockdown import LockdownClient
from iosc.device.transport import DeviceInfo, NetworkConnector, PairRecords

STAGING_DIR = "PublicStaging"
WIRELESS_DOMAIN = "com.apple.mobile.wireless_lockdown"
WIRELESS_KEY = "EnableWifiConnections"
NETWORK = tunnel.NETWORK


# pairings on windows live in apple mobile device service
def default_pair_records() -> PairRecords:
    if sys.platform == "win32":
        return transport.UsbmuxPairRecords()
    return transport.FilePairRecords(paths.pair_records_dir())


def list_devices(
    host: str = transport.USBMUX_HOST, port: int = transport.USBMUX_PORT
) -> list[DeviceInfo]:
    return transport.list_devices(host, port)


def find_device(
    udid: str | None = None,
    host: str = transport.USBMUX_HOST,
    port: int = transport.USBMUX_PORT,
) -> DeviceInfo:
    with transport.UsbmuxClient(host, port) as client:
        device = client.find_device(udid)
    if device is None:
        raise DeviceError(
            "no device found, plug in an iPhone and trust this computer"
            if udid is None
            else f"device {udid} not connected"
        )
    return device


# network lockdown refuses GetValue before a session
# only the matching pair record starts one
def identify(address: str, candidates: list[str], records: PairRecords) -> DeviceInfo | None:
    for udid in candidates:
        record = records.read(udid)
        if record is None:
            continue
        info = DeviceInfo(device_id=0, serial=udid, connection_type=NETWORK, address=address)
        connector = NetworkConnector(address, timeout=3.0, io_timeout=5.0)
        try:
            with LockdownClient(info, connector, records).connect() as client:
                client.start_session(record)
        except (DeviceError, OSError) as error:
            get_reporter().detail(f"{address} is not {udid} ({error})")
            continue
        return info
    return None


def list_network_devices(
    timeout: float = 2.0,
    interface: str | None = None,
    udid: str | None = None,
    records: PairRecords | None = None,
) -> list[DeviceInfo]:
    records = records or default_pair_records()
    candidates = [udid] if udid else records.udids()
    found: dict[str, DeviceInfo] = {}
    for instance in mdns.browse(timeout=timeout, interface=interface):
        for address in instance.addresses:
            remaining = [c for c in candidates if c not in found]
            info = identify(address, remaining, records)
            if info is not None:
                found[info.serial] = info
                break
    return list(found.values())


def find_network_device(
    udid: str | None = None, timeout: float = 2.0, interface: str | None = None
) -> DeviceInfo:
    devices = list_network_devices(timeout, interface, udid)
    if udid is not None:
        devices = [d for d in devices if d.serial == udid]
    if not devices:
        raise DeviceError(
            "no device answered on the network. Unlock it, join the same network, "
            "and run 'iosc wifi enable' once over usb"
            if udid is None
            else f"device {udid} did not answer on the network"
        )
    if udid is None and len(devices) > 1:
        names = ", ".join(d.serial for d in devices)
        raise DeviceError(f"more than one device on the network, pass --udid to pick one of {names}")
    return devices[0]


class Device:
    def __init__(
        self,
        info: DeviceInfo,
        connector: transport.Connector | None = None,
        records: PairRecords | None = None,
    ) -> None:
        self.info = info
        self.connector = connector or transport.connector_for(info)
        self.records = records or default_pair_records()

    @property
    def udid(self) -> str:
        return self.info.serial

    def _lockdown(self) -> LockdownClient:
        return LockdownClient(self.info, self.connector, self.records).connect()

    def info_dict(self) -> dict[str, Any]:
        with self._lockdown() as client:
            return {
                "UniqueDeviceID": client.udid(),
                "DeviceName": client.device_name(),
                "ProductVersion": client.product_version(),
                "ProductType": client.get_value("ProductType"),
            }
    # first pair shows the trust dialog. retry after trusted
    def ensure_paired(self, client: LockdownClient | None = None) -> dict[str, Any]:
        owned = client is None
        client = client or self._lockdown()
        try:
            record = client.load_saved_pair_record()
            if record is None:
                if not self.connector.can_pair:
                    raise DeviceError(
                        f"no pair record for {self.udid}. Pair over usb first, "
                        f"or copy its record into {paths.pair_records_dir()}"
                    )
                record = client.pair()
            client.pair_record = record
            return record
        finally:
            if owned:
                client.close()

    def open_session(self) -> LockdownClient:
        client = self._lockdown()
        self.ensure_paired(client)
        client.start_session()
        return client

    @contextmanager
    def _proxy(self) -> Iterator[install.InstallationProxyClient]:
        with self.open_session() as client:
            proxy = install.InstallationProxyClient(
                client.open_service(install.INSTALLATION_PROXY_SERVICE)
            )
            try:
                yield proxy
            finally:
                proxy.close()

    def install_app(
        self,
        app_or_ipa: Path | str,
        progress: install.ProgressCallback | None = None,
    ) -> dict[str, Any]:
        local = Path(app_or_ipa)
        if self.info.connection_type == NETWORK:
            with tunnel.open_session(self.info) as session:
                return session.install(local, progress=progress)
        is_ipa = local.is_file()
        remote = STAGING_DIR + "/" + local.name
        with self.open_session() as client:
            with afc.AfcClient(client.open_service(afc.AFC_SERVICE)) as conduit:
                conduit.make_dir_p(STAGING_DIR)
                conduit.upload_path(local, remote)
            proxy = install.InstallationProxyClient(
                client.open_service(install.INSTALLATION_PROXY_SERVICE)
            )
            try:
                package_type = None if is_ipa else "Developer"
                return proxy.install(
                    remote, package_type=package_type, progress=progress
                )
            finally:
                proxy.close()

    def wifi_enabled(self) -> bool:
        with self.open_session() as client:
            return bool(client.get_value(WIRELESS_KEY, domain=WIRELESS_DOMAIN))

    def set_wifi(self, enabled: bool) -> None:
        with self.open_session() as client:
            client.set_value(WIRELESS_KEY, enabled, domain=WIRELESS_DOMAIN)

    def uninstall_app(
        self, bundle_id: str, progress: install.ProgressCallback | None = None
    ) -> dict[str, Any]:
        with self._proxy() as proxy:
            return proxy.uninstall(bundle_id, progress=progress)

    def list_apps(self, application_type: str = "User") -> list[dict[str, Any]]:
        with self._proxy() as proxy:
            return proxy.browse(
                attributes=[
                    "CFBundleIdentifier",
                    "CFBundleDisplayName",
                    "CFBundleVersion",
                ],
                application_type=application_type,
            )

    def syslog(self, contains: str | None = None) -> Iterator[str]:
        with self.open_session() as client:
            stream = client.open_service(services.SYSLOG_SERVICE)
            try:
                yield from services.stream_syslog(stream, contains)
            finally:
                stream.close()
