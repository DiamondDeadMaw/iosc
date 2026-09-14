import socket
from typing import Any, Callable

from iosc.core.errors import InstallError
from iosc.device.transport import PlistConnection

INSTALLATION_PROXY_SERVICE = "com.apple.mobile.installation_proxy"

ProgressCallback = Callable[[int, str], None]


class InstallationProxyClient:
    def __init__(self, sock: socket.socket) -> None:
        self.connection = PlistConnection(sock, "installation_proxy")

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "InstallationProxyClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _raise_if_error(self, response: dict[str, Any]) -> None:
        error = response.get("Error")
        if error:
            raise InstallError(response.get("ErrorDescription", error))

    def _run_command(
        self, payload: dict[str, Any], progress: ProgressCallback | None = None
    ) -> dict[str, Any]:
        self.connection.send(payload)
        while True:
            response = self.connection.receive()
            self._raise_if_error(response)
            status = response.get("Status")
            if status == "Complete":
                return response
            if status is None:
                return response
            if progress is not None:
                progress(int(response.get("PercentComplete", 0)), status)

    def install(
        self,
        package_path: str,
        package_type: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if package_type is not None:
            options["PackageType"] = package_type
        return self._run_command(
            {
                "Command": "Install",
                "PackagePath": package_path,
                "ClientOptions": options,
            },
            progress,
        )

    def upgrade(
        self,
        package_path: str,
        package_type: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if package_type is not None:
            options["PackageType"] = package_type
        return self._run_command(
            {
                "Command": "Upgrade",
                "PackagePath": package_path,
                "ClientOptions": options,
            },
            progress,
        )

    def uninstall(
        self, bundle_id: str, progress: ProgressCallback | None = None
    ) -> dict[str, Any]:
        return self._run_command(
            {
                "Command": "Uninstall",
                "ApplicationIdentifier": bundle_id,
                "ClientOptions": {},
            },
            progress,
        )

    def lookup(
        self,
        bundle_ids: list[str] | None = None,
        attributes: list[str] | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if bundle_ids is not None:
            options["BundleIDs"] = bundle_ids
        if attributes is not None:
            options["ReturnAttributes"] = attributes
        self.connection.send({"Command": "Lookup", "ClientOptions": options})
        return self.connection.receive().get("LookupResult", {})

    def browse(
        self,
        attributes: list[str] | None = None,
        application_type: str = "User",
    ) -> list[dict[str, Any]]:
        options: dict[str, Any] = {"ApplicationType": application_type}
        if attributes is not None:
            options["ReturnAttributes"] = attributes
        self.connection.send({"Command": "Browse", "ClientOptions": options})
        results: list[dict[str, Any]] = []
        while True:
            response = self.connection.receive()
            self._raise_if_error(response)
            results.extend(response.get("CurrentList", []))
            if response.get("Status") == "Complete":
                return results
