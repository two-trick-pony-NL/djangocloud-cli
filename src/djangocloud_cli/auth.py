"""`cloud login`: the device-code flow. No passwords or keys are typed into the terminal."""

import time
import webbrowser

from . import config
from .api import ApiError, Client
from .ui import console


def login(client: Client, *, sleep=time.sleep, open_browser=webbrowser.open, client_name: str = "cloud CLI") -> str:
    started = client.post("/auth/device", {"client_name": client_name})
    url, code = started["verification_uri_complete"], started["user_code"]
    console.print(f"\nOpen [link={url}]{url}[/link]\nand check that the code is [bold]{code}[/bold].\n")
    try:
        open_browser(url)
    except Exception:  # noqa: BLE001 - no browser (SSH, CI) is fine: the URL is printed above
        pass

    interval = started.get("interval", 5)
    deadline = time.monotonic() + started.get("expires_in", 600)
    with console.status("Waiting for you to approve in the browser…"):
        while time.monotonic() < deadline:
            sleep(interval)
            try:
                token = client.post("/auth/token", {"device_code": started["device_code"]})["access_token"]
            except ApiError as exc:
                if exc.code == "authorization_pending":
                    continue
                if exc.code == "slow_down":
                    interval += 5
                    continue
                raise
            config.save_token(token)
            client.token = token
            return token
    raise ApiError(400, "expired_token", "That login expired. Run 'cloud login' again.")
