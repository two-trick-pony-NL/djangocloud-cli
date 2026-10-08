"""`djangocloud setup`: the guided first run. A card on file, then hosted or your own AWS, then (for your own AWS)
the access keys. Every step asks the server what is still missing, so it can be re-run any time and picks up where
it stopped. Nothing secret is stored here: the AWS secret goes to the server once, over HTTPS, and nowhere else."""

import contextlib
import json
import os
import time
import webbrowser

from .api import ApiError, Client
from .ui import CliError, confirm, console, interactive, password, select, text

POLL_INTERVAL = 2  # seconds between checks while the user is on the Stripe page
CARD_TIMEOUT = 15 * 60
DEFAULT_REGION = "eu-west-1"
AWS_KEY_ENV, AWS_SECRET_ENV = "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"
_sleep = time.sleep  # swapped out in tests
_open = webbrowser.open


def open_browser(url: str) -> None:
    with contextlib.suppress(Exception):  # no browser (SSH, CI) is fine: the URL is printed as well
        _open(url)


def add_card(client: Client, site: str) -> None:
    """Put a card on file through Stripe Checkout: open the page, then wait until Stripe says it's done."""
    if not interactive():
        raise CliError(f"No card on file yet. Add one to activate your account: {site}/dashboard/billing/")
    started = client.post("/billing/checkout")
    if started.get("active"):
        return
    url = started["url"]
    console.print(f"\nAdd a card to activate your account (it's handled by Stripe):\n[link={url}]{url}[/link]\n")
    open_browser(url)
    deadline = time.monotonic() + CARD_TIMEOUT
    with console.status("Waiting for you to finish in the browser…"):
        while time.monotonic() < deadline:
            _sleep(POLL_INTERVAL)
            if client.post("/billing/confirm", {"session_id": started["session_id"]}).get("subscription_active"):
                console.print("[green]✓[/green] Card added. Your account is active.")
                return
    raise CliError("Didn't see a card added in time. Run the command again to get a fresh link.")


def choose_hosting(client: Client, me: dict, site: str, args) -> bool:
    """Ask hosted or your own AWS and tell the server. True when we host it, False when the user's AWS runs it."""
    hosted_open = me.get("hosted_open", False)
    if getattr(args, "hosted", False):
        hosted = True
    elif getattr(args, "own_cloud", False):
        hosted = False
    elif not interactive():
        raise CliError("Say where it should run: pass --hosted or --own-cloud.")
    elif hosted_open:
        hosted = select(
            "Where should your apps run?",
            [
                ("Hosted: we run it for you, in an AWS environment made just for you", True),
                ("Your own AWS account: you keep the keys and AWS bills you directly", False),
            ],
        )
    else:
        console.print(f"[dim]Hosting isn't open yet (join the waitlist at {site}). Using your own AWS account.[/dim]")
        hosted = False
    client.post("/setup/hosting", {"hosting": "hosted" if hosted else "self"})
    if hosted:
        console.print("[green]✓[/green] Hosted. We set up and run the AWS side; there are no keys to hand over.")
    return hosted


def _keys_from_env() -> tuple[str, str]:
    return os.environ.get(AWS_KEY_ENV, "").strip(), os.environ.get(AWS_SECRET_ENV, "").strip()


def connect_aws(client: Client, site: str, args) -> None:
    """Hand the server an IAM access key for the user's AWS account. The server checks it against AWS first."""
    info = client.get("/aws")
    key_id, secret = _keys_from_env()
    region = getattr(args, "region", None)
    if not interactive():
        if not (key_id and secret and region):
            raise CliError(
                f"Set {AWS_KEY_ENV} and {AWS_SECRET_ENV} in the environment and pass --region "
                f"(or connect it at {site}/dashboard/aws/)."
            )
    else:
        console.print(
            "\nWe deploy into your AWS account with an IAM user's access key. Create an IAM user for DjangoCloud "
            f"(steps at {site}/dashboard/aws/) and keep its permissions to the policy we list."
        )
        if confirm("Show the IAM policy here?", default=False):
            console.print_json(json.dumps(info["policy"]))
        if key_id and secret and confirm(f"Use the AWS keys from your environment ({key_id[:4]}…{key_id[-4:]})?"):
            pass
        else:
            key_id = text("AWS access key ID (starts with AKIA)").strip()
            secret = password("AWS secret access key (hidden)").strip()
        if not region:
            default = info.get("region") or DEFAULT_REGION
            choices = [(f"{r['label']}  ({r['code']})", r["code"]) for r in info["regions"]]
            choices.sort(key=lambda c: c[1] != default)  # the default first, so Enter picks it
            region = select("Which AWS region should apps run in?", choices)
    try:
        with console.status("Checking the keys with AWS…"):
            result = client.put("/aws", {"access_key_id": key_id, "secret_access_key": secret, "region": region})
    except ApiError as exc:
        if exc.code == "invalid_aws_credentials":
            raise CliError(exc.message) from None
        raise
    console.print(f"[green]✓[/green] Connected AWS account {result['account_id']} in {result['region']}.")


def run(client: Client, site: str, args, say) -> dict:
    """Do whatever is still missing; returns the account as the server then sees it."""
    me = client.get("/me")
    if "setup_step" not in me:
        raise CliError("This DjangoCloud server doesn't support guided setup yet. Try again after its next release.")
    if me["setup_step"] == "card":
        add_card(client, site)
        me = client.get("/me")
    if me["setup_step"] == "hosting":
        if not choose_hosting(client, me, site, args):
            connect_aws(client, site, args)
        me = client.get("/me")
    if me["setup_step"] != "ready":
        raise CliError(f"Setup isn't finished. Run '{say('setup')}' to continue.")
    return me
