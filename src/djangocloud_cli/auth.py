"""`djangocloud login`: the device-code flow. No passwords or keys are typed into the terminal."""

import contextlib
import time
import webbrowser
from urllib.parse import quote, urlsplit

from . import config
from .api import ApiError, Client
from .ui import console


def signup_url(verification_url: str) -> str:
    """The sign-up page that, once the account exists, lands on the same approval page as the login would."""
    parts = urlsplit(verification_url)
    approve = parts.path + (f"?{parts.query}" if parts.query else "")
    return f"{parts.scheme}://{parts.netloc}/signup/?next={quote(approve, safe='')}"


def login(
    client: Client,
    *,
    sleep=time.sleep,
    open_browser=webbrowser.open,
    client_name: str = "cloud CLI",
    new_account: bool = False,
) -> str:
    """Sign in by approving a code in the browser. `new_account` opens the sign-up page first: nothing is typed here."""
    started = client.post("/auth/device", {"client_name": client_name})
    url, code = started["verification_uri_complete"], started["user_code"]
    if new_account:
        url = signup_url(url)
    what = "Create your account at" if new_account else "Open"
    console.print(f"\n{what} [link={url}]{url}[/link]\nand check that the code is [bold]{code}[/bold].\n")
    with contextlib.suppress(Exception):  # no browser (SSH, CI) is fine: the URL is printed above
        open_browser(url)

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
    raise ApiError(400, "expired_token", "That login expired. Run the login command again.")
