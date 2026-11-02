from iosc.device.afc import AFC_SERVICE, AfcClient, AfcStatusError
from iosc.device.crash import list_reports, pull_reports
from iosc.device.discovery import (
    Device,
    default_pair_records,
    find_device,
    find_network_device,
    list_devices,
    list_network_devices,
)
from iosc.device.install import (
    INSTALLATION_PROXY_SERVICE,
    InstallationProxyClient,
    ProgressCallback,
)
from iosc.device.launch import LaunchResult, launch_app
from iosc.device.lockdown import (
    LockdownClient,
    LockdownProtocolError,
    PairingDenied,
    PairingPending,
    SessionInactive,
    build_pair_record,
)
from iosc.device.oslog import OsLogEntry
from iosc.device.oslog import format_entry as format_oslog_entry
from iosc.device.oslog import stream_oslog
from iosc.device.remotexpc import Tunnel, ensure_available, run_pmd, tunnel_running
from iosc.device.services import SYSLOG_SERVICE, stream_syslog
from iosc.device.tunnel import find_device as find_wifi_device
from iosc.device.tunnel import has_pairing as has_wifi_pairing
from iosc.device.tunnel import open_session as open_wifi_session
from iosc.device.tunnel import pair as pair_wifi
from iosc.device.tunnel import record_path as wifi_record_path
from iosc.device.transport import (
    Connector,
    DeviceInfo,
    FilePairRecords,
    NetworkConnector,
    PairRecords,
    PlistConnection,
    UsbmuxClient,
    UsbmuxConnector,
    UsbmuxPairRecords,
    UsbmuxProtocolError,
    connect_device,
    recv_exact,
)

__all__ = [
    "AFC_SERVICE",
    "AfcClient",
    "AfcStatusError",
    "Connector",
    "Device",
    "DeviceInfo",
    "FilePairRecords",
    "NetworkConnector",
    "PairRecords",
    "UsbmuxConnector",
    "UsbmuxPairRecords",
    "default_pair_records",
    "find_network_device",
    "find_wifi_device",
    "has_wifi_pairing",
    "list_network_devices",
    "open_wifi_session",
    "pair_wifi",
    "wifi_record_path",
    "INSTALLATION_PROXY_SERVICE",
    "InstallationProxyClient",
    "LaunchResult",
    "LockdownClient",
    "LockdownProtocolError",
    "OsLogEntry",
    "PairingDenied",
    "PairingPending",
    "PlistConnection",
    "ProgressCallback",
    "SYSLOG_SERVICE",
    "SessionInactive",
    "Tunnel",
    "UsbmuxClient",
    "UsbmuxProtocolError",
    "build_pair_record",
    "connect_device",
    "ensure_available",
    "find_device",
    "format_oslog_entry",
    "launch_app",
    "list_devices",
    "list_reports",
    "pull_reports",
    "recv_exact",
    "run_pmd",
    "stream_oslog",
    "stream_syslog",
    "tunnel_running",
]
