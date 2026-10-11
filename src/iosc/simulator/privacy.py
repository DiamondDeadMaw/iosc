from pathlib import Path
import sqlite3

from iosc.core import SimulatorError

SERVICES = {
    "microphone": "kTCCServiceMicrophone",
    "camera": "kTCCServiceCamera",
    "photos": "kTCCServicePhotos",
    "photos-add": "kTCCServicePhotosAdd",
    "contacts": "kTCCServiceAddressBook",
    "calendar": "kTCCServiceCalendar",
    "reminders": "kTCCServiceReminders",
    "media-library": "kTCCServiceMediaLibrary",
    "motion": "kTCCServiceMotion",
    "speech-recognition": "kTCCServiceSpeechRecognition",
}
ACTIONS = ("grant", "revoke", "reset")

TCC_DENIED = 0
TCC_ALLOWED = 2
TCC_REASON_USER_SET = 2
TCC_VERSION = 1
TCC_CLIENT_BUNDLE = 0


def service_name(service: str) -> str:
    name = SERVICES.get(service, service)
    if not name.startswith("kTCCService"):
        raise SimulatorError(
            f"unknown service {service}, one of {', '.join(SERVICES)} or a kTCCService name"
        )
    return name


# rows as xcrun simctl privacy writes them. tccd reads the database per request
def set_permission(tcc_db: Path, action: str, service: str, bundle_id: str) -> str:
    name = service_name(service)
    if not tcc_db.is_file():
        raise SimulatorError(f"the simulator privacy database {tcc_db} is missing")
    db = sqlite3.connect(tcc_db)
    try:
        with db:
            if action == "reset":
                db.execute(
                    "DELETE FROM access WHERE service = ? AND client = ?", (name, bundle_id)
                )
            else:
                value = TCC_ALLOWED if action == "grant" else TCC_DENIED
                db.execute(
                    "INSERT OR REPLACE INTO access "
                    "(service, client, client_type, auth_value, auth_reason, auth_version) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (name, bundle_id, TCC_CLIENT_BUNDLE, value, TCC_REASON_USER_SET, TCC_VERSION),
                )
    finally:
        db.close()
    return name
