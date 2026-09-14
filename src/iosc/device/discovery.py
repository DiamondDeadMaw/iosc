from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from iosc.core.errors import DeviceError
from iosc.device import afc, install, services, transport
from iosc.device.lockdown import LockdownClient
from iosc.device.transport import DeviceInfo

STAGING_DIR = "PublicStaging"


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


class Device:
    def __init__(
        self,
        info: DeviceInfo,
        host: str = transport.USBMUX_HOST,
        mux_port: int = transport.USBMUX_PORT,
    ) -> None:
        self.info = info
        self.host = host
        self.mux_port = mux_port

    @property
    def udid(self) -> str:
        return self.info.serial

    def _lockdown(self) -> LockdownClient:
        return LockdownClient(self.info, self.host, self.mux_port).connect()

    def info_dict(self) -> dict[str, Any]:
        with self._lockdown() as client:
            return {
                "UniqueDeviceID": client.udid(),
                "DeviceName": client.device_name(),
                "ProductVersion": client.product_version(),
                "ProductType": client.get_value("ProductType"),
            }
    # firs r pair will show the trust dialog, retry after trusted
    def ensure_paired(self, client: LockdownClient | None = None) -> dict[str, Any]:
        owned = client is None
        client = client or self._lockdown()
        try:
            record = client.load_saved_pair_record()
            if record is None:
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
