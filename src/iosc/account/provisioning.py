from typing import Any

from iosc.core.errors import DevServicesError
from iosc.formats.mobileprovision import Profile


def download_profile(session: Any, team_id: str, app_id_id: str) -> Profile:
    req_func = getattr(session, "request", getattr(session, "_request", None))
    if req_func is None:
        raise DevServicesError("invalid session object for profile download")
    response = req_func(
        "downloadTeamProvisioningProfile.action",
        {"teamId": team_id, "appIdId": app_id_id},
    )
    prov = response.get("provisioningProfile")
    if not prov or "encodedProfile" not in prov:
        raise DevServicesError("Apple response did not contain provisioning profile")
    return Profile.from_bytes(bytes(prov["encodedProfile"]))
