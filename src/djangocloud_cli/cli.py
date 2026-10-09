"""`djangocloud` command: one parser shared by the standalone script and `manage.py djangocloud`."""

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from . import __version__, auth, config, detect, envfile, link, onboarding, package, scaffold, testing
from .api import ADVICE, ApiError, Client
from .ui import CliError, NotInteractive, confirm, console, err, interactive, no_input, select, set_no_input, text

STANDALONE = "djangocloud"
MANAGE_PY = "python manage.py djangocloud"
_prog = MANAGE_PY  # what messages tell people to type; set by run() to match how we were invoked


def say(command: str) -> str:
    """The command as the user types it, e.g. 'python manage.py djangocloud login'."""
    return f"{_prog} {command}"


COMING_SOON = "'{command}' isn't available yet."
POLL_INTERVAL = 2  # seconds between release status checks
DEPLOY_TIMEOUT = 20 * 60
_sleep = time.sleep  # swapped out in tests


def build_parser(prog: str = STANDALONE) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Deploy your Django app.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="Never prompt or open a browser (for CI). Needs DJANGOCLOUD_TOKEN and a linked folder or --project",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    new = sub.add_parser(
        "new",
        help=argparse.SUPPRESS if scaffold.in_project() else "Create a new Django project that is ready to deploy",
        description="Create a folder with a project made by Django's own 'django-admin startproject' for the latest "
        "LTS release. Only the database settings differ: SQLite on your computer, and the Postgres database "
        "DjangoCloud creates for you once it runs there. Needs a network connection.",
    )
    new.add_argument("name", help="The project's name, e.g. my-shop (also the folder it is created in)")
    new.add_argument(
        "--no-install",
        action="store_true",
        help="Don't create the .venv and install Django (and the other requirements) into it",
    )
    sub.add_parser("login", help="Sign in (approve a code in your browser)")
    setup = sub.add_parser("setup", help="Guided setup: account, card, hosted or your own AWS, AWS keys")
    setup_where = setup.add_mutually_exclusive_group()
    setup_where.add_argument("--hosted", action="store_true", help="We run it for you (no AWS keys needed)")
    setup_where.add_argument("--own-cloud", action="store_true", help="It runs in your own AWS account")
    setup.add_argument("--region", help=f"AWS region for your own account (keys come from {onboarding.AWS_KEY_ENV})")
    sub.add_parser("logout", help="Forget the stored token")
    sub.add_parser("whoami", help="Show who you are signed in as")

    linkers = (
        ("deploy", "Link this folder to a project, then deploy it"),
        ("link", "Pick or create the project this folder deploys to"),
    )
    for name, helptext in linkers:
        cmd = sub.add_parser(name, help=helptext)
        cmd.add_argument("--project", help="Use this existing project (slug) without prompting")
        cmd.add_argument("--name", help="Create a new project with this name without prompting")
        cmd.add_argument("--size", help="Server size for a new project, e.g. nano")
        where = cmd.add_mutually_exclusive_group()
        where.add_argument("--hosted", action="store_true", help="New project: we run it for you (Fully managed plan)")
        where.add_argument("--own-cloud", action="store_true", help="New project: it runs in your own AWS account")
        cmd.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    deploy_flags = sub.choices["deploy"]
    deploy_flags.add_argument(
        "--wsgi-module", help="Your WSGI app, e.g. config.wsgi:application (if it can't be detected)"
    )
    deploy_flags.add_argument(
        "--asgi-module", help="Your ASGI app, e.g. config.asgi:application. When set, it is started with uvicorn"
    )
    deploy_flags.add_argument(
        "--github",
        action="store_true",
        help="Deploy the latest commit of the project's linked GitHub repo instead of uploading this folder",
    )
    deploy_flags.add_argument(
        "--skip-tests",
        action="store_true",
        help='Skip the local test run that "run_tests": "local" asks for in .djangocloud/config.json',
    )
    test = sub.add_parser(
        "test",
        help="Run the project's tests now, the way a deploy would",
        description='Run "test_command" from .djangocloud/config.json in the project (detected for you when you '
        'switch tests on). A deploy runs it first when "run_tests" is true.',
    )
    test.add_argument("extra", nargs=argparse.REMAINDER, help="Extra arguments for the test command (after --)")
    tests = sub.add_parser(
        "tests",
        help="Show or change whether tests run before each deploy",
        description="Show the test settings, or change them: 'tests on' runs your tests before every deploy "
        "(the command is detected from your project), 'tests off' stops. --require makes the project refuse "
        "deploys whose tests did not pass.",
    )
    tests.add_argument("state", nargs="?", choices=["on", "off"], help="Run tests before deploys, or not")
    tests.add_argument("--detect", action="store_true", help="Work out the test command from the project again")
    tests.add_argument("--command", dest="command_text", metavar="CMD", help="Set the test command yourself")
    tests.add_argument(
        "--require", choices=["on", "off"], help="Make the project refuse deploys unless their tests passed"
    )
    tests.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    sub.add_parser("unlink", help="Detach this folder from its project")
    status = sub.add_parser("status", help="Show whether the project is live, and its latest releases")
    status.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    status.add_argument("--json", action="store_true", help="Print the raw details as JSON, for scripts")

    logs = sub.add_parser("logs", help="Show a project's logs")
    logs.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    logs.add_argument("-f", "--follow", action="store_true", help="Keep streaming new lines (Ctrl-C to stop)")
    logs.add_argument("-n", "--lines", type=int, default=100, help="How many of the latest lines to show (default 100)")
    logs.add_argument("--source", choices=["app", "build", "release"], help="Only this kind of log line")
    logs.add_argument("--since", metavar="DURATION", help="Only lines newer than this: 90s, 30m, 2h or 7d")

    scale = sub.add_parser("scale", help="Change a project's server size or number of instances")
    scale.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    scale.add_argument("--size", help="Server size, e.g. small (see the sizes in 'status')")
    scale.add_argument("--instances", type=int, help="How many instances to run (1-20)")
    scale.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    scale.add_argument("--no-wait", action="store_true", help="Return as soon as the change is queued")

    rollback = sub.add_parser(
        "rollback",
        help="Go back to an earlier release (its image and variables are redeployed as a new release)",
        description="Redeploy an earlier release's image, with the environment variables it had, as a NEW release. "
        "Nothing is rebuilt. Database migrations are never reversed.",
    )
    rollback.add_argument("version", nargs="?", type=int, help="The release number to go back to (asked if left out)")
    rollback.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    rollback.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    rollback.add_argument("--no-wait", action="store_true", help="Return as soon as the rollback is queued")

    env = sub.add_parser("env", help="Manage a project's environment variables (push them from a file or from CI)")
    env.set_defaults(_menu=env)  # no subcommand: show this menu
    env_sub = env.add_subparsers(dest="env_command", metavar="<push|list>")
    push = env_sub.add_parser(
        "push",
        help="Set variables from a .env file, or from named variables in the environment (--from-env)",
        description="Set environment variables on a project. They are stored encrypted and reach the app from the "
        "next deploy. Values are never printed. Other variables on the project are left alone unless you pass --prune.",
    )
    push.add_argument("file", nargs="?", help="A .env file to read ('-' for standard input)")
    push.add_argument(
        "--from-env",
        nargs="+",
        metavar="NAME",
        help="Read these variables from the current environment instead (in CI: the ones you map from secrets)",
    )
    push.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    push.add_argument("--prune", action="store_true", help="Also remove the project's variables that you did not send")
    push.add_argument("--dry-run", action="store_true", help="Show what would change, without changing anything")
    push.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation when removing")
    env_list = env_sub.add_parser("list", help="List the names of a project's variables (never their values)")
    env_list.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")

    autoscale = sub.add_parser(
        "autoscale",
        help="Show or change autoscaling (add and remove instances by load)",
        description="Show autoscaling, or turn it on (with a minimum and maximum number of instances) or off.",
    )
    autoscale.add_argument("state", nargs="?", choices=["on", "off"], help="Turn it on or off (omit to show it)")
    autoscale.add_argument("--min", type=int, dest="low", help="Fewest instances to keep (1-20)")
    autoscale.add_argument("--max", type=int, dest="high", help="Most instances to run (1-20)")
    autoscale.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")

    alerts = sub.add_parser(
        "alerts",
        help="Show or change the emails sent when CPU or memory run high, or the server stops answering",
        description="Show usage alerts, or turn them on (with CPU and memory limits in percent) or off.",
    )
    alerts.add_argument("state", nargs="?", choices=["on", "off"], help="Turn them on or off (omit to show them)")
    alerts.add_argument("--cpu", type=int, help="Email when CPU stays above this percent (1-100)")
    alerts.add_argument("--memory", type=int, help="Email when memory stays above this percent (1-100)")
    alerts.add_argument(
        "--downtime",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Also email when the server stops answering",
    )
    alerts.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")

    metrics = sub.add_parser("metrics", help="Show the server's CPU and memory load")
    metrics.add_argument("--since", default="1h", metavar="DURATION", help="How far back: 30m, 6h or 7d (default 1h)")
    metrics.add_argument("--json", action="store_true", help="Print the raw samples as JSON, for scripts")
    metrics.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")

    db = sub.add_parser("db", help="The project's database: status, public access, snapshots")
    db.set_defaults(_menu=db)
    db_sub = db.add_subparsers(dest="db_command", metavar="<status|public|snapshot>")
    db_status = db_sub.add_parser("status", help="Show the database: state, size, public access, last snapshot")
    db_status.add_argument("--json", action="store_true", help="Print the raw details as JSON, for scripts")
    db_status.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    db_public = db_sub.add_parser(
        "public",
        help="Open the database to the internet for an hour, or lock it",
        description="Open the database's public endpoint for one hour (it locks again by itself), or lock it now. "
        "Locking also cuts off your app until you open it again.",
    )
    db_public.add_argument("state", nargs="?", choices=["on", "off"], help="on or off (omit to show the current state)")
    db_public.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    db_public.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    db_snapshot = db_sub.add_parser(
        "snapshot",
        help="Take a snapshot of the database now",
        description="Take a manual snapshot. Unlike the automatic backups (a week), it is kept until you delete it.",
    )
    db_snapshot.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    db_snapshot.add_argument("--no-wait", action="store_true", help="Return as soon as the snapshot is queued")

    teardown = sub.add_parser("teardown", help="Delete a project and everything it created in AWS")
    teardown.add_argument("--project", help="A project (slug) instead of the one this folder is linked to")
    teardown.add_argument("-y", "--yes", action="store_true", help="Don't ask you to type the project name")

    helper = sub.add_parser("help", help="Show this help")
    helper.add_argument("topic", nargs="?", help="A command to get help for")
    return parser


def make_client() -> Client:
    return Client(config.api_url(), config.load_token())


def ensure_login(client: Client) -> None:
    """Make sure the client holds a token the server accepts, signing in if it doesn't."""
    if client.token:
        try:
            client.get("/me")
            return
        except ApiError as exc:
            if exc.code != "unauthorized":
                raise
    if not interactive():
        raise CliError(
            "Not signed in. In CI set DJANGOCLOUD_TOKEN (create one at /dashboard/cli/); "
            f"on your own machine run '{say('login')}'."
        )
    console.print("You're not signed in yet.")
    sign_in(client)
    console.print(f"[green]✓[/green] Signed in as {client.get('/me')['email']}")


def sign_in(client: Client) -> None:
    """Sign in, or create the account first. Either way it ends with a code approved in the browser."""
    new_account = select("Do you have a DjangoCloud account?", [("Yes, sign me in", False), ("No, create one", True)])
    auth.login(client, new_account=new_account)


def _price(cents: int) -> str:
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def _aws_cents(size: dict) -> int:
    """What AWS charges for this size in the user's own account (older servers only sent our price)."""
    return size.get("aws_cents", size["price_cents"])


def _size_label(size: dict) -> str:
    specs = f"{size['vcpu']:g} vCPU, {size['ram_gb']:g} GB RAM"
    return f"{size['label']:<8} {specs:<22} ~{_price(_aws_cents(size))}/month on AWS"


def ensure_linked(client: Client, root, args, *, force: bool = False) -> dict:
    existing = None if force else link.load(root)
    if existing:
        return existing

    projects = client.get("/projects")["projects"]
    if args.project:
        match = next((p for p in projects if p["slug"] == args.project), None)
        if match is None:
            raise CliError(f"No project {args.project!r} on your account.")
        project = match
    else:
        choice = args.name or select(
            "Which project is this?",
            [("Create a new project", None)] + [(f"{p['name'] or p['slug']}  ({p['slug']})", p) for p in projects],
        )
        project = choice if isinstance(choice, dict) else create_project(client, root, args)
    path = link.save(root, project, config.api_url())
    console.print(f"[green]✓[/green] Linked to [bold]{project['slug']}[/bold] ({path.relative_to(root)})")
    return project


def choose_where(client: Client, args) -> bool:
    """True to have DjangoCloud host the project; False to run it in the user's own AWS account."""
    can_host = client.get("/me").get("can_host", False)
    if args.hosted:
        if not can_host:
            raise CliError(f"Hosting needs the Fully managed plan. Switch plans: {site_url()}/dashboard/billing/")
        return True
    if args.own_cloud or not can_host or args.yes or no_input():
        return False
    return select(
        "Where should it run?",
        [("We host it for you, in an AWS environment made just for you", True), ("In your own AWS account", False)],
    )


def create_project(client: Client, root, args) -> dict:
    name = args.name or text("Project name", default=root.name)
    hosted = choose_where(client, args)
    sizes = client.get("/sizes")["sizes"]
    if args.size:
        size = next((s for s in sizes if s["power"] == args.size), None)
        if size is None:
            raise CliError(f"Unknown size {args.size!r}. Choose from: {', '.join(s['power'] for s in sizes)}.")
    else:
        size = select("Server size", [(_size_label(s), s) for s in sizes])
    if hosted:
        price = _price(size["price_cents"])
        note = f"Your plan bills {price}/month for this server, charged before anything is set up. Continue?"
    else:
        monthly = _price(_aws_cents(size))
        note = f"AWS bills you about {monthly}/month for this server, directly in your own AWS account. Continue?"
    if not (args.yes or no_input()) and not confirm(note):
        raise CliError("Cancelled. Nothing was created.")
    try:
        return client.post("/projects", {"name": name, "power": size["power"], **({"hosted": True} if hosted else {})})
    except ApiError as exc:
        if exc.code == "payment_required":
            raise CliError(f"{exc.message}") from None
        raise


def cmd_new(args, root) -> int:
    inside = scaffold.in_project()
    if inside:
        raise CliError(
            f"You are already inside a Django project ({inside}), so there is nothing to create here. "
            f"'{say('new')}' starts a project: run it from the folder where the new one should live."
        )
    try:
        package = scaffold.package_name(args.name)
        series = scaffold.latest_lts()
        console.print(f"Creating a Django {series} (LTS) project…")
        with console.status("Setting up Django, one moment…"):
            folder, series = scaffold.create(Path.cwd(), args.name, series=series)
    except scaffold.ScaffoldError as exc:
        raise CliError(str(exc)) from None
    console.print(f"[green]✓[/green] Created [bold]{args.name}[/bold] (Django {series} LTS) in {folder}")
    console.print(f"  [dim]{package}/settings.py is Django's own, except DATABASES.[/dim]")
    installed = ""
    if not args.no_install:
        try:
            with console.status("Installing Django into .venv…"):
                installed = scaffold.install_dependencies(folder)
            console.print(
                f"[green]✓[/green] Installed Django {series} and the other requirements into .venv ({installed})."
            )
        except scaffold.ScaffoldError as exc:
            console.print(f"[yellow]! {exc}[/yellow]")
    console.print("\nNext:")
    console.print(f"  cd {args.name}")
    if installed:
        console.print("  source .venv/bin/activate         [dim]# on Windows: .venv\\Scripts\\activate[/dim]")
    else:
        console.print(
            "  uv venv && uv pip install -r requirements.txt   [dim]# or: python -m venv .venv, then pip[/dim]"
        )
    console.print("  python manage.py runserver        [dim]# see it locally[/dim]")
    console.print(f"  {say('deploy')}                  [dim]# put it online[/dim]")
    return 0


def cmd_login(args, root) -> int:
    if not interactive():
        raise CliError(f"'{say('login')}' needs a browser. In CI use a DJANGOCLOUD_TOKEN instead.")
    client = make_client()
    auth.login(client)
    console.print(f"[green]✓[/green] Signed in as {client.get('/me')['email']}")
    return 0


def cmd_setup(args, root) -> int:
    client = make_client()
    ensure_login(client)
    me = onboarding.run(client, site_url(), args, say)
    where = "hosted by us" if me["plan"] in HOSTED_PLANS else f"in your own AWS account ({aws_region(client)})"
    console.print(f"\n[green]✓[/green] All set. Your apps will run {where}.")
    console.print(f"  Next: [bold]{say('deploy')}[/bold]")
    return 0


HOSTED_PLANS = ("company", "enterprise")


def aws_region(client: Client) -> str:
    return client.get("/aws").get("region", "")


def ensure_set_up(client: Client, args) -> None:
    """Before creating a project: if the account isn't ready and someone is at the keyboard, finish the setup first.
    Without one, `preflight` names what's missing instead."""
    if not interactive():
        return
    if client.get("/me").get("setup_step", "ready") != "ready":
        console.print("Let's finish setting up your account first.")
        onboarding.run(client, site_url(), args, say)


def cmd_whoami(args, root) -> int:
    client = make_client()
    if not client.token:
        console.print(f"Not signed in. Run [bold]{say('login')}[/bold].")
        return 1
    try:
        me = client.get("/me")
    except ApiError as exc:
        if exc.code != "unauthorized":
            raise
        console.print(f"Your token is no longer valid. Run [bold]{say('login')}[/bold].")
        return 1
    console.print(f"Signed in as [bold]{me['email']}[/bold]")
    linked = link.load(root)
    if linked:
        console.print(f"This folder deploys to [bold]{linked['slug']}[/bold].")
    return 0


def cmd_logout(args, root) -> int:
    console.print("Signed out." if config.clear_token() else "You weren't signed in.")
    return 0


def cmd_link(args, root) -> int:
    client = make_client()
    ensure_login(client)
    ensure_set_up(client, args)
    ensure_linked(client, root, args, force=True)
    return 0


def cmd_unlink(args, root) -> int:
    console.print("Unlinked." if link.remove(root) else "This folder isn't linked.")
    return 0


def site_url() -> str:
    return config.api_url().removesuffix("/api/v1")


def preflight(client: Client, slug: str = "") -> None:
    """Say what is missing before we spend time packaging and uploading."""
    me = client.get("/me")
    if me.get("ready_to_deploy", True):
        return
    hosted = any(p.get("hosted") and p.get("slug") == slug for p in client.get("/projects").get("projects", []))
    if hosted:  # we run it: no AWS connection is needed, only a card in good standing
        me = {**me, "aws_connected": True}
        if me.get("subscription_active", True) and not me.get("suspended"):
            return
    if me.get("suspended"):
        raise CliError(f"Your account is suspended for non-payment. Update your card: {site_url()}/dashboard/billing/")
    if not me.get("subscription_active", True):
        raise CliError(f"No card on file yet. Add one to activate your account: {site_url()}/dashboard/billing/")
    if not me.get("aws_connected", True):
        raise CliError(f"Connect your AWS account (IAM access key and region) first: {site_url()}/dashboard/aws/")


def ensure_build_settings(client: Client, root, wsgi_module: str | None = None, asgi_module: str | None = None) -> dict:
    """The "build" block of .djangocloud/config.json: detected and written on the first deploy, then yours to edit."""
    build = link.load_build(root)
    if build is None:
        schema = client.get("/build-config")
        found, notes = detect.detect(root, schema)
        build = {**schema["defaults"], **found}
        build.pop("run_tests", None)  # asked about once, after the first deploy's settings are written
        build.pop("test_command", None)
        if wsgi_module:
            build["wsgi_module"] = wsgi_module
        if asgi_module:
            build["asgi_module"] = asgi_module
        if not (build.get("wsgi_module") or build.get("asgi_module")) and not interactive():
            raise CliError(
                "Couldn't detect your app (no wsgi.py or asgi.py found). Pass --asgi-module config.asgi:application "
                f'or --wsgi-module config.wsgi:application (or set them under "build" in {link.DIR}/{link.FILE}).'
            )
        if not (build.get("wsgi_module") or build.get("asgi_module")):
            build["wsgi_module"] = text("Where is your WSGI app? (e.g. config.wsgi:application)")
        path = link.save_build(root, build)
        console.print(f"[green]✓[/green] Wrote build settings to {path.relative_to(root)}")
        for note in notes:
            console.print(f"  [dim]found {note}[/dim]")
        console.print("  [dim]Edit that file to change how your app is built.[/dim]")
    if not (build.get("wsgi_module") or build.get("asgi_module") or build.get("start_command")):
        raise CliError(
            f'Set "asgi_module" (e.g. config.asgi:application) or "wsgi_module" under "build" in '
            f"{link.DIR}/{link.FILE}."
        )
    base = root / build.get("root", ".")
    if build.get("package_manager") == "uv":
        needed = ["pyproject.toml", "uv.lock"]
    else:
        needed = [build.get("requirements_file", "requirements.txt")]
    missing = [n for n in needed if not (base / n).is_file()]
    if missing:
        raise CliError(f"Missing {', '.join(missing)}: your dependencies must be listed so the image can install them.")
    return build


DEFAULT_TEST_COMMAND = "python -m pytest -q"


def test_command_for(root, build: dict) -> str:
    """The command to run. One you wrote yourself is used as is. When it is missing or still the generic default (you
    switched tests on by hand), the project is looked at, so a uv, Poetry or Django-runner project gets the command
    that actually works there instead of a bare `python -m pytest`."""
    command = (build.get("test_command") or "").strip()
    if command and command != DEFAULT_TEST_COMMAND:
        return command
    setup = testing.detect(root / build.get("root", "."), build.get("django_settings_module", ""))
    if setup.found:
        console.print(f"[dim]Detected the test command for this project ({setup.label}).[/dim]")
        return setup.command
    return command or DEFAULT_TEST_COMMAND


def run_tests(root, build: dict, extra: list[str] | None = None) -> tuple[int, int, str]:
    """Run the project's own test command in its folder, streaming the output. Returns (exit code, seconds, command)."""
    command = test_command_for(root, build)
    if extra:
        command += " " + " ".join(shlex.quote(a) for a in extra if a != "--")
    folder = root / build.get("root", ".")
    console.print(f"[bold]Running tests[/bold]  [dim]{command}[/dim]")
    started = time.monotonic()
    try:
        # The command is the user's own, from their own config file, and is meant to be a shell command.
        code = subprocess.run(command, shell=True, cwd=folder).returncode  # noqa: S602
    except OSError as exc:
        raise CliError(f"Couldn't run the tests: {exc}") from None
    return code, round(time.monotonic() - started), command


def cmd_test(args, root) -> int:
    build = link.load_build(root) or {}
    code, seconds, _ = run_tests(root, build, args.extra)
    console.print(
        f"[green]✓[/green] Tests passed ({seconds}s)." if code == 0 else f"[red]Tests failed[/red] (exit {code})."
    )
    return 0 if code == 0 else 1


def show_test_setup(setup: "testing.TestSetup") -> None:
    console.print(f"  Found {setup.label}.")
    console.print(f"  Command   [bold]{setup.command}[/bold]")
    for reason in setup.reasons:
        console.print(f"  [dim]· {reason}[/dim]")
    for warning in setup.warnings:
        console.print(f"  [yellow]! {warning}[/yellow]")


def ask_about_tests(root, build: dict, *, assume_yes: bool = False) -> dict:
    """Once per project: offer to run the tests before every deploy. The answer, yes or no, is written to
    .djangocloud/config.json so it is never asked again. Scripts (no terminal, --yes) are never asked and nothing is
    written: their behavior must not change on its own."""
    if "run_tests" in build or assume_yes or not interactive():
        return build
    setup = testing.detect(root, build.get("django_settings_module", ""))
    if not setup.found:
        return build  # nothing to run: say nothing, ask nothing
    console.print("\n[bold]Tests[/bold]")
    show_test_setup(setup)
    if confirm("Run your tests before every deploy? (a failing test stops the deploy)", default=True):
        build = {**build, "run_tests": True, "test_command": setup.command}
        console.print(f"[green]✓[/green] Tests will run before each deploy. Change it with '{say('tests off')}'.")
    else:
        build = {**build, "run_tests": False}
        console.print(f"Okay. Turn it on any time with '{say('tests on')}'.")
    link.save_build(root, build)
    return build


def tests_before_deploy(root, build: dict, skip: bool) -> dict:
    """Run the tests if the project asked for it. Returns the report that goes to the server with the upload:
    {"tests": "passed" | "skipped" | "none", "tests_command", "tests_seconds"}. A failing run stops the deploy."""
    if not build.get("run_tests"):
        if skip:
            console.print("[dim]--skip-tests: this project does not run tests before deploys anyway.[/dim]")
        return {"tests": "none"}
    if skip:
        console.print("[yellow]Skipping the tests (--skip-tests).[/yellow]")
        return {"tests": "skipped"}
    code, seconds, command = run_tests(root, build)
    if code != 0:
        raise CliError(
            f"The tests failed (exit {code}), so nothing was deployed. Fix them, or deploy anyway with "
            f"'{say('deploy --skip-tests')}'."
        )
    console.print(f"[green]✓[/green] Tests passed ({seconds}s).")
    return {"tests": "passed", "tests_command": command, "tests_seconds": str(seconds)}


def _tests_summary(build: dict, policy: dict | None) -> None:
    on = bool(build.get("run_tests"))
    console.print(
        f"Tests before deploy: {_onoff(on)}" + (f"  [dim]{build.get('test_command', '')}[/dim]" if on else "")
    )
    if policy is not None:
        console.print(f"The project requires passing tests: {_onoff(policy['require'])}")
        last = policy.get("last")
        if last:
            console.print(f"  [dim]Last reported: v{last['version']} {last['status']}[/dim]")


def cmd_tests(args, root) -> int:
    """Show or change the test settings: this folder's (config.json) and the project's policy (the server)."""
    build = link.load_build(root)
    if build is None:
        raise CliError(f"No build settings yet. Run '{say('deploy')}' once first, or '{say('link')}'.")
    changed = False
    django_settings = build.get("django_settings_module", "")
    if args.state == "on":
        if args.detect or not build.get("test_command"):
            setup = testing.detect(root, django_settings)
            if setup.found:
                show_test_setup(setup)
                build = {**build, "test_command": setup.command}
            else:
                build = {**build, "test_command": build.get("test_command") or DEFAULT_TEST_COMMAND}
        build = {**build, "run_tests": True}
        changed = True
    elif args.state == "off":
        build = {**build, "run_tests": False}
        changed = True
    elif args.detect:
        setup = testing.detect(root, django_settings)
        if not setup.found:
            raise CliError(" ".join(setup.warnings) or "Couldn't find your tests.")
        show_test_setup(setup)
        build = {**build, "test_command": setup.command}
        changed = True
    if args.command_text:
        build = {**build, "test_command": args.command_text}
        changed = True
    if changed:
        link.save_build(root, build)
        console.print(f"[green]✓[/green] Saved to {link.DIR}/{link.FILE}.")
    policy = None
    if args.require or not changed:
        client = make_client()
        ensure_login(client)
        project = resolve_project(client, root, args)
        path = f"/projects/{project['id']}/tests"
        if args.require:
            policy = _put(client, path, {"require": args.require == "on"})
            if policy["require"] and not build.get("run_tests"):
                console.print(
                    f"[yellow]This folder doesn't run tests, so deploys from it will be refused. "
                    f"Turn them on with '{say('tests on')}'.[/yellow]"
                )
        else:
            try:
                policy = get_or_explain(client, path)
            except CliError:
                policy = None  # a server that predates the policy: show the folder's setting only
    _tests_summary(build, policy)
    return 0


def git_sha(root) -> str:
    try:
        sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, check=True, timeout=10)
        return sha.stdout.decode().strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def explain(exc: ApiError) -> CliError:
    """An API refusal as one clear message plus the link that fixes it."""
    extra = exc.payload.get("billing_url") or exc.payload.get("aws_url")
    return CliError(f"{exc.message}\n  → {extra}" if extra else exc.message)


def print_log(line: dict) -> None:
    level = line.get("level", "info")
    style = {"error": "red", "warning": "yellow"}.get(level)
    console.print(
        f"  [{style}]{line['message']}[/{style}]" if style else f"  [dim]{line['message']}[/dim]", highlight=False
    )


def follow(client: Client, release: dict) -> int:
    """Stream the release's log lines until it is live or failed."""
    cursor, started, failures = 0, time.monotonic(), 0
    try:
        while True:
            try:
                state = client.get(f"/releases/{release['id']}?after={cursor}")
                failures = 0
            except ApiError as exc:
                if exc.code != "unreachable" or (failures := failures + 1) > 5:
                    raise
                _sleep(POLL_INTERVAL)
                continue
            for line in state["logs"]:
                print_log(line)
            cursor = state["cursor"]
            if state["done"]:
                break
            if time.monotonic() - started > DEPLOY_TIMEOUT:
                raise CliError(
                    "Still not finished after 20 minutes. It keeps running; check the dashboard for its status."
                )
            _sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        err.print("\nStopped watching. The deploy keeps running on DjangoCloud; check the dashboard for its status.")
        return 130
    if state["ok"]:
        console.print(f"[green]✓[/green] v{state['version']} is live.")
        return 0
    err.print(f"[red]✗ v{state['version']} failed.[/red] The lines above say why.")
    return 1


def cmd_deploy(args, root) -> int:
    client = make_client()
    ensure_login(client)
    if not link.load(root):  # a linked folder already went through setup
        ensure_set_up(client, args)
    project = ensure_linked(client, root, args)
    console.print(f"Deploying [bold]{project['slug']}[/bold]")
    preflight(client, project["slug"])
    fields = {"git_sha": git_sha(root)}
    if args.github:
        console.print("Deploying the latest commit from the linked GitHub repository.")
        data = None
        if args.skip_tests:
            console.print("[dim]--skip-tests has no effect on a GitHub deploy: tests run from this folder only.[/dim]")
    else:
        build = ensure_build_settings(client, root, args.wsgi_module, args.asgi_module)
        build = ask_about_tests(root, build, assume_yes=args.yes)
        fields |= tests_before_deploy(root, build, args.skip_tests)
        try:
            data, count = package.build(root)
        except package.PackageError as exc:
            raise CliError(str(exc)) from None
        console.print(
            f"[green]✓[/green] Packed {count} files ({len(data) / 1024:,.0f} KB). .env and .git are never uploaded."
        )
    try:
        release = client.upload(
            f"/projects/{project['id']}/releases",
            fields=fields,
            file_field="source",
            filename="source.tar.gz",
            content=data,
        )
    except ApiError as exc:
        if exc.code in ("deploy_in_progress", "tests_required"):
            raise CliError(exc.message) from None
        raise explain(exc) from None
    console.print(f"[green]✓[/green] Uploaded. Release v{release['version']} started.")
    return follow(client, release)


# ---------- status and logs ----------

TONES = {"green": "green", "yellow": "yellow", "red": "red", "gray": "dim"}
LEVEL_STYLES = {"error": "red", "critical": "red", "warning": "yellow"}
SINCE_RE = re.compile(r"^\d{1,5}[smhd]$")
OLD_SERVER = "This DjangoCloud server doesn't support that yet. Try again after its next release."


def resolve_project(client: Client, root, args) -> dict:
    """The project to act on: --project <slug>, else the one this folder is linked to."""
    if getattr(args, "project", None):
        match = next((p for p in client.get("/projects")["projects"] if p["slug"] == args.project), None)
        if match is None:
            raise CliError(f"No project {args.project!r} on your account.")
        return match
    linked = link.load(root)
    if linked and linked.get("id"):
        return linked
    raise CliError(f"This folder isn't linked to a project. Run '{say('link')}' or pass --project <slug>.")


def get_or_explain(client: Client, path: str) -> dict:
    """GET, turning a plain 404 (a server that predates the endpoint) into a clear message."""
    try:
        return client.get(path)
    except ApiError as exc:
        if exc.status == 404 and exc.code == "http_error":
            raise CliError(OLD_SERVER) from None
        raise


def ago(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return "never"
    then = datetime.fromisoformat(iso)
    seconds = max(0, int(((now or datetime.now(timezone.utc)) - then).total_seconds()))
    for limit, unit, size in ((60, "s", 1), (3600, "min", 60), (86400, "h", 3600)):
        if seconds < limit:
            return "just now" if seconds < 5 and unit == "s" else f"{seconds // size} {unit} ago"
    return f"{seconds // 86400} d ago"


def cmd_status(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    detail = get_or_explain(client, f"/projects/{project['id']}")
    status = detail["status"]
    if args.json:
        console.print_json(json.dumps(detail))
        return 1 if status["tone"] == "red" else 0
    tone = TONES.get(status["tone"], "white")
    console.print(f"[bold]{detail['name'] or detail['slug']}[/bold]  [{tone}]● {status['label']}[/{tone}]")
    console.print(f"  [dim]{status['detail']}[/dim]")
    if detail["url"]:
        console.print(f"  URL       {detail['url']}")
    console.print(f"  Size      {detail['power'].capitalize()} × {detail['scale']} · {detail['region']}")  # noqa: RUF001 - the multiplication sign is intentional
    health = detail["health"]
    if health["state"] != "unknown":
        console.print(f"  Checked   {ago(health['checked_at'])}" + (f" ({health['error']})" if health["error"] else ""))
    releases = detail["releases"]
    if releases:
        console.print("\n  Releases")
        for release in releases:
            sha = f"  {release['git_sha'][:7]}" if release["git_sha"] else ""
            live = "  [green]live[/green]" if release["status"] == "active" else ""
            console.print(
                f"    v{release['version']:<4} {release['status']:<11} {ago(release['created_at'])}{sha}{live}"
            )
    else:
        console.print(f"\n  Nothing deployed yet. Run '{say('deploy')}'.")
    return 1 if status["tone"] == "red" else 0


def print_logline(line: dict) -> None:
    when = datetime.fromisoformat(line["at"]).astimezone().strftime("%H:%M:%S")
    style = LEVEL_STYLES.get(line.get("level", "info"))
    message = line["message"].replace("[", "\\[")  # a log line is text, never rich markup
    body = f"[{style}]{message}[/{style}]" if style else message
    console.print(f"[dim]{when} {line['source']:<7}[/dim] {body}", highlight=False)


def cmd_logs(args, root) -> int:
    if args.since and not SINCE_RE.match(args.since):
        raise CliError("--since must look like 90s, 30m, 2h or 7d.")
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    base = f"/projects/{project['id']}/logs"
    filters = {k: v for k, v in (("source", args.source), ("since", args.since)) if v}
    page = get_or_explain(client, f"{base}?{urlencode({**filters, 'lines': max(1, args.lines)})}")
    for line in page["lines"]:
        print_logline(line)
    if not page["lines"] and not args.follow:
        console.print("[dim]No log lines match.[/dim]")
    if not args.follow:
        return 0
    cursor, failures = page["cursor"], 0
    try:
        while True:
            _sleep(POLL_INTERVAL)
            try:
                page = client.get(f"{base}?{urlencode({**filters, 'after': cursor})}")
                failures = 0
            except ApiError as exc:
                if exc.code != "unreachable" or (failures := failures + 1) > 5:
                    raise
                continue
            for line in page["lines"]:
                print_logline(line)
            cursor = page["cursor"]
    except KeyboardInterrupt:
        return 0  # stopping a follow is the normal way to end it


# ---------- scale and teardown ----------

SCALE_TIMEOUT = 15 * 60
MAX_INSTANCES = 20


def _project_detail(client: Client, project: dict) -> dict:
    return get_or_explain(client, f"/projects/{project['id']}")


def _size_choices(sizes: list[dict], detail: dict) -> list[tuple[str, str]]:
    hosted = detail.get("hosted")
    choices = []
    for size in sizes:
        cents = size["price_cents"] if hosted else _aws_cents(size)
        specs = f"{size['vcpu']:g} vCPU, {size['ram_gb']:g} GB RAM"
        now = "  (current)" if size["power"] == detail["power"] else ""
        choices.append((f"{size['label']:<8} {specs:<22} ~{_price(cents)}/month each{now}", size["power"]))
    return choices


def _per_month(sizes: list[dict], detail: dict, power: str, scale: int) -> str:
    size = next(s for s in sizes if s["power"] == power)
    cents = (size["price_cents"] if detail.get("hosted") else _aws_cents(size)) * scale
    return f"~{_price(cents)}/month " + ("on your plan" if detail.get("hosted") else "on your AWS bill")


def cmd_scale(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    detail = _project_detail(client, project)
    sizes = client.get("/sizes")["sizes"]
    power, instances = args.size, args.instances
    if power is None and instances is None:
        power = select("Server size", _size_choices(sizes, detail))
        instances = int(text("How many instances? (1-20)", default=str(detail["scale"])) or detail["scale"])
    power, instances = power or detail["power"], instances or detail["scale"]
    if power not in {s["power"] for s in sizes}:
        raise CliError(f"Unknown size {power!r}. Choose from: {', '.join(s['power'] for s in sizes)}.")
    if not 1 <= instances <= MAX_INSTANCES:
        raise CliError(f"Choose between 1 and {MAX_INSTANCES} instances.")
    if (power, instances) == (detail["power"], detail["scale"]):
        console.print(f"[bold]{detail['slug']}[/bold] is already {power.capitalize()} × {instances}.")  # noqa: RUF001
        return 0
    cost = _per_month(sizes, detail, power, instances)
    console.print(
        f"{detail['slug']}: {detail['power'].capitalize()} × {detail['scale']} → "  # noqa: RUF001
        f"[bold]{power.capitalize()} × {instances}[/bold]  ({cost})"  # noqa: RUF001
    )
    if not (args.yes or no_input()) and not confirm("Apply this change?"):
        raise CliError("Cancelled. Nothing was changed.")
    try:
        queued = client.post(f"/projects/{detail['id']}/scale", {"power": power, "scale": instances})
    except ApiError as exc:
        raise explain(exc) from None
    if args.no_wait:
        console.print("[green]✓[/green] Queued. Check progress with " + f"'{say('status')}'.")
        return 0
    return follow_resize(client, queued)


def follow_resize(client: Client, project: dict) -> int:
    """Wait for the worker to apply a size change: pending, applying, then idle (done) or failed."""
    started, failures = time.monotonic(), 0
    try:
        with console.status("Applying the change…"):
            while True:
                try:
                    state = client.get(f"/projects/{project['id']}")
                    failures = 0
                except ApiError as exc:
                    if exc.code != "unreachable" or (failures := failures + 1) > 5:
                        raise
                    _sleep(POLL_INTERVAL)
                    continue
                resize = state["resize"]
                if resize["status"] in ("idle", "failed"):
                    break
                if time.monotonic() - started > SCALE_TIMEOUT:
                    raise CliError("Still not finished after 15 minutes. It keeps running; check 'status' later.")
                _sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        err.print("\nStopped watching. The change keeps being applied on DjangoCloud.")
        return 130
    if resize["status"] == "failed":
        err.print(f"[red]✗ The change failed.[/red] {resize['error'] or 'See the dashboard for details.'}")
        return 1
    console.print(f"[green]✓[/green] {state['slug']} now runs {state['power'].capitalize()} × {state['scale']}.")  # noqa: RUF001
    return 0


def cmd_teardown(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    detail = _project_detail(client, project)
    slug = detail["slug"]
    console.print(
        f"This deletes [bold]{slug}[/bold] and removes what it created in AWS (servers, images, any database)."
    )
    console.print("Its logs and releases go with it. This cannot be undone.")
    if not args.yes:
        if no_input():
            raise CliError(f"Pass --yes to tear down {slug} without being asked.")
        if text(f"Type {slug} to confirm").strip() != slug:
            raise CliError("That doesn't match. Nothing was deleted.")
    try:
        client.delete(f"/projects/{detail['id']}", {"confirm": slug})
    except ApiError as exc:
        raise explain(exc) from None
    linked = link.load(root)
    if linked and linked.get("id") == detail["id"]:
        link.remove(root)
    console.print(f"[green]✓[/green] {slug} is deleted. The AWS resources are being removed in the background.")
    return 0


def cmd_rollback(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    detail = _project_detail(client, project)
    live = (detail.get("live_release") or {}).get("version")
    version = args.version
    if version is None:
        choices = [
            (
                f"v{r['version']:<4} {r['status']:<11} {ago(r.get('created_at')):<10} {(r.get('git_sha') or '')[:7]}",
                r["version"],
            )
            for r in detail.get("releases", [])
            if r["status"] in ("active", "superseded") and r["version"] != live
        ]
        if not choices:
            raise CliError("There is no earlier release to go back to.")
        version = select("Roll back to", choices)
    console.print(
        f"{detail['slug']}: v{live} → [bold]v{version}[/bold]. Its image and variables come back as a new release; "
        "database migrations are not reversed."
    )
    if not (args.yes or no_input()) and not confirm("Roll back?"):
        raise CliError("Cancelled. Nothing was changed.")
    try:
        release = client.post(f"/projects/{detail['id']}/rollback", {"version": version})
    except ApiError as exc:
        raise explain(exc) from None
    if args.no_wait:
        console.print(f"[green]✓[/green] Queued as v{release['version']}. Check progress with '{say('status')}'.")
        return 0
    return follow(client, release)


PLATFORM_PREFIXES = ("DJANGOCLOUD_HOSTED_DB_",)  # set by DjangoCloud itself; --prune never removes them


def _env_path(project: dict) -> str:
    return f"/projects/{project['id']}/env"


def _collect_env(args) -> dict[str, str]:
    """The variables to send: from a file, or named variables of the current environment."""
    if bool(args.file) == bool(args.from_env):
        raise CliError("Give either a .env file or --from-env NAME [NAME ...], not both and not neither.")
    if args.from_env:
        found = {name: os.environ[name] for name in args.from_env if os.environ.get(name)}
        skipped = [name for name in args.from_env if name not in found]
        if skipped:
            err.print(f"[yellow]Skipped (not set or empty):[/yellow] {', '.join(skipped)}")
        return found
    if args.file == "-":
        content = sys.stdin.read()
    else:
        try:
            content = Path(args.file).read_text()
        except OSError as exc:
            raise CliError(f"Couldn't read {args.file}: {exc.strerror}.") from None
    variables, errors = envfile.parse(content)
    if errors:
        raise CliError("Nothing was sent. " + " ".join(errors[:5]))
    return variables


def cmd_env(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    if args.env_command == "list":
        names = [v["key"] for v in get_or_explain(client, _env_path(project))["variables"]]
        console.print(f"[bold]{project['slug']}[/bold]: {len(names)} variable(s)")
        for name in names:
            console.print(f"  {name}")
        return 0
    variables = _collect_env(args)
    if not variables and not args.prune:
        raise CliError("There is nothing to send: no variables were found.")
    existing = {v["key"] for v in get_or_explain(client, _env_path(project))["variables"]}
    creating, updating = sorted(set(variables) - existing), sorted(set(variables) & existing)
    removing = sorted(k for k in existing - set(variables) if not k.startswith(PLATFORM_PREFIXES)) if args.prune else []
    console.print(
        f"{project['slug']}: [bold]{len(creating)}[/bold] new, [bold]{len(updating)}[/bold] to overwrite"
        + (f", [bold]{len(removing)}[/bold] to remove" if args.prune else "")
        + " (values are never shown)."
    )
    for label, names in (("new", creating), ("overwrite", updating), ("remove", removing)):
        if names:
            console.print(f"  {label}: {', '.join(names)}")
    if args.dry_run:
        console.print("Dry run: nothing was changed.")
        return 0
    if (
        removing
        and not (args.yes or no_input())
        and not confirm(f"Remove {len(removing)} variable(s) from the project?")
    ):
        raise CliError("Cancelled. Nothing was changed.")
    try:
        client.put(_env_path(project), {"variables": variables, "replace": args.prune})
    except ApiError as exc:
        if exc.status == 404 and exc.code == "http_error":
            raise CliError(OLD_SERVER) from None
        raise
    console.print(f"[green]✓[/green] Saved {len(variables)} variable(s). They apply from the next deploy.")
    return 0


def _onoff(value: bool) -> str:
    return "[green]on[/green]" if value else "[dim]off[/dim]"


def _put(client: Client, path: str, body: dict) -> dict:
    try:
        return client.put(path, body)
    except ApiError as exc:
        raise explain(exc) from None


def cmd_autoscale(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    path = f"/projects/{project['id']}/autoscale"
    if args.state is None and args.low is None and args.high is None:
        found = get_or_explain(client, path)
        line = f"{project['slug']}: autoscaling is {_onoff(found['enabled'])}"
        console.print(line + (f", {found['min']} to {found['max']} instances." if found["enabled"] else "."))
        if found["enabled"] and found["note"]:
            console.print(f"  [dim]{found['note']}[/dim]")
        return 0
    body = {"enabled": args.state != "off"}
    if args.state is None:  # --min/--max alone changes the range, whatever state it is in
        body["enabled"] = get_or_explain(client, path)["enabled"]
    if args.low is not None:
        body["min"] = args.low
    if args.high is not None:
        body["max"] = args.high
    if body["enabled"] and args.state == "on" and (args.low is None or args.high is None):
        raise CliError("Turning autoscaling on needs --min and --max, e.g. --min 2 --max 6.")
    saved = _put(client, path, body)
    if saved["enabled"]:
        console.print(f"[green]✓[/green] Autoscaling is on: {saved['min']} to {saved['max']} instances.")
    else:
        console.print("[green]✓[/green] Autoscaling is off.")
    return 0


def cmd_alerts(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    path = f"/projects/{project['id']}/alerts"
    if args.state is None and args.cpu is None and args.memory is None and args.downtime is None:
        found = get_or_explain(client, path)
        console.print(f"{project['slug']}: usage alerts are {_onoff(found['enabled'])}")
        console.print(f"  CPU above      {found['cpu']}%\n  Memory above   {found['memory']}%")
        console.print(f"  Server down    {_onoff(found['downtime'])}")
        if found["firing"]:
            console.print("  [red]Over a limit right now.[/red]")
        return 0
    body = {k: v for k, v in (("cpu", args.cpu), ("memory", args.memory), ("downtime", args.downtime)) if v is not None}
    if args.state:
        body["enabled"] = args.state == "on"
    saved = _put(client, path, body)
    if saved["enabled"]:
        console.print(
            f"[green]✓[/green] We'll email you when CPU stays above {saved['cpu']}% or memory above {saved['memory']}%"
            + (", or the server stops answering." if saved["downtime"] else ".")
        )
    else:
        console.print("[green]✓[/green] Usage alerts are off.")
    return 0


BARS = "▁▂▃▄▅▆▇█"


def sparkline(values: list[float], width: int = 48) -> str:
    """Percent values (0-100) as a line of block characters, thinned to `width`."""
    if len(values) > width:
        step = len(values) / width
        values = [max(values[int(i * step) : max(int((i + 1) * step), int(i * step) + 1)]) for i in range(width)]
    return "".join(BARS[min(len(BARS) - 1, int(max(0.0, v) / 100 * len(BARS)))] for v in values)


def cmd_metrics(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    try:
        found = get_or_explain(client, f"/projects/{project['id']}/metrics?" + urlencode({"since": args.since}))
    except ApiError as exc:
        raise explain(exc) from None
    if args.json:
        console.print_json(json.dumps(found))
        return 0
    points, latest = found["points"], found["latest"]
    console.print(f"[bold]{project['slug']}[/bold]  {found['power'].capitalize()} × {found['scale']}")  # noqa: RUF001
    if not points:
        console.print(
            f"  No samples in the last {args.since}." + (f" Latest was {ago(latest['at'])}." if latest else "")
        )
        return 0
    for label, key in (("CPU", "cpu"), ("Memory", "memory")):
        values = [p[key] for p in points]
        now, peak = values[-1], max(values)
        console.print(
            f"  {label:<7}{sparkline(values)}  now {now:.0f}%  peak {peak:.0f}%  avg {sum(values) / len(values):.0f}%"
        )
    console.print(f"  [dim]{len(points)} samples over the last {args.since}, newest {ago(points[-1]['at'])}[/dim]")
    return 0


def _database(client: Client, project: dict) -> dict:
    try:
        return get_or_explain(client, f"/projects/{project['id']}/database")
    except ApiError as exc:
        raise explain(exc) from None


def cmd_db(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = resolve_project(client, root, args)
    path = f"/projects/{project['id']}/database"
    found = _database(client, project)
    if args.db_command == "status":
        return _db_status(args, project, found)
    if args.db_command == "public":
        return _db_public(client, args, project, found, path)
    return _db_snapshot(client, args, project, found, path)


def _db_public_text(found: dict) -> str:
    if found.get("network_pending"):
        return f"{found['network_pending']}…"
    if found.get("public") is None:
        return "unknown"
    until = f" (locks again {found['open_until'][11:16]} UTC)" if found.get("open_until") else ""
    return ("[yellow]open to the internet[/yellow]" + until) if found["public"] else "[green]locked[/green]"


def _db_status(args, project: dict, found: dict) -> int:
    if args.json:
        console.print_json(json.dumps(found))
        return 0
    console.print(
        f"[bold]{project['slug']}[/bold] database  {found['size'].capitalize()}"
        + (" · high availability" if found["ha"] else "")
    )
    if found.get("removing"):
        console.print("  [yellow]Being removed.[/yellow]")
        return 0
    if not found["ready"]:
        console.print("  Still being created.")
        return 0
    console.print(f"  State      {found.get('state', 'unknown')}")
    console.print(f"  Public     {_db_public_text(found)}")
    if found.get("endpoint"):
        console.print(f"  Endpoint   {found['endpoint']}")
    snapshot = found.get("last_snapshot")
    if snapshot:
        console.print(
            f"  Snapshot   {snapshot['name']} ({ago(snapshot['created_at'])})"
            + ("  [dim]taking another…[/dim]" if found.get("snapshot_pending") else "")
        )
    elif found.get("snapshot_pending"):
        console.print("  Snapshot   being taken…")
    if found.get("latest_restorable"):
        console.print(f"  Restorable up to {ago(found['latest_restorable'])} (automatic backups, a week)")
    if found.get("aws_error"):
        console.print(f"  [yellow]AWS couldn't be reached: {found['aws_error']}[/yellow]")
    return 0


def _db_public(client: Client, args, project: dict, found: dict, path: str) -> int:
    if args.state is None:
        console.print(f"{project['slug']}: database is {_db_public_text(found)}")
        return 0
    want = args.state == "on"
    if want:
        console.print(
            "The database will accept connections from anywhere on the internet for one hour, then lock again."
        )
    else:
        console.print(
            "[yellow]Locking cuts off every connection, including your app, until you open it again.[/yellow]"
        )
    if not (args.yes or no_input()) and not confirm("Continue?", default=False):
        raise CliError("Cancelled. Nothing was changed.")
    try:
        client.post(f"{path}/network", {"public": want})
    except ApiError as exc:
        raise explain(exc) from None
    console.print("[green]✓[/green] " + ("Opening the database for an hour." if want else "Locking the database."))
    console.print(f"  Follow it with '{say('db status')}'.")
    return 0


def _db_snapshot(client: Client, args, project: dict, found: dict, path: str) -> int:
    try:
        client.post(f"{path}/snapshot")
    except ApiError as exc:
        raise explain(exc) from None
    if args.no_wait:
        console.print("[green]✓[/green] Snapshot queued. Check it with " + f"'{say('db status')}'.")
        return 0
    try:
        with console.status("Taking the snapshot…"):
            deadline = time.monotonic() + 1800
            while time.monotonic() < deadline:
                _sleep(5)
                state = _database(client, project)
                if not state.get("snapshot_pending"):
                    break
            else:
                raise CliError(f"Still taking the snapshot. Check it with '{say('db status')}'.")
    except KeyboardInterrupt:
        console.print(f"\nStill running in the background. Check it with '{say('db status')}'.")
        return 0
    snapshot = state.get("last_snapshot")
    console.print("[green]✓[/green] Snapshot " + (f"{snapshot['name']} is ready." if snapshot else "finished."))
    return 0


COMMANDS = {
    "new": cmd_new,
    "test": cmd_test,
    "tests": cmd_tests,
    "autoscale": cmd_autoscale,
    "alerts": cmd_alerts,
    "metrics": cmd_metrics,
    "db": cmd_db,
    "env": cmd_env,
    "rollback": cmd_rollback,
    "login": cmd_login,
    "setup": cmd_setup,
    "scale": cmd_scale,
    "teardown": cmd_teardown,
    "logout": cmd_logout,
    "whoami": cmd_whoami,
    "link": cmd_link,
    "unlink": cmd_unlink,
    "deploy": cmd_deploy,
    "status": cmd_status,
    "logs": cmd_logs,
}


def full_help(parser: argparse.ArgumentParser) -> str:
    """The overview plus every command's own help (flags included), nested commands like `env push` too."""
    sections = [parser.format_help().rstrip()]

    def walk(p: argparse.ArgumentParser) -> None:
        group = next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)
        for name, sub in group.choices.items() if group else ():
            if name == "help" or (name == "new" and scaffold.in_project()):
                continue
            sections.append(sub.format_help().rstrip())
            walk(sub)

    walk(parser)
    rule = "\n\n" + "-" * 72 + "\n\n"
    return sections[0] + "\n\nEvery command and its options:" + rule + rule.join(sections[1:])


def run(argv: list[str] | None = None, prog: str = STANDALONE) -> int:
    global _prog
    _prog = prog
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    set_no_input(getattr(args, "no_input", False))

    if args.command is None:  # just `djangocloud`: the menu of commands, and where to find everything else
        sys.stdout.write(parser.format_help() + f"\nRun '{say('help')}' for every command with all of its options.\n")
        return 0
    if args.command == "help":
        topic = getattr(args, "topic", None)
        if topic:
            return run([topic, "--help"], prog)  # argparse prints the sub-command's help, then exits
        sys.stdout.write(full_help(parser) + "\n")
        return 0
    menu = getattr(args, "_menu", None)
    if menu is not None and not getattr(args, f"{args.command}_command", None):
        sys.stdout.write(menu.format_help())  # `djangocloud db` on its own: that command's menu, not an error
        return 2
    handler = COMMANDS.get(args.command)
    if handler is None:
        err.print(COMING_SOON.format(command=say(args.command)))
        return 2
    ADVICE.clear()
    try:
        return handler(args, link.find_root())
    except (CliError, NotInteractive) as exc:
        err.print(f"[red]Error:[/red] {exc}")
    except ApiError as exc:
        if exc.code == "upgrade_required":  # this version is no longer supported: say how to upgrade, nothing else
            err.print(f"[red]Upgrade needed:[/red] {exc.message}")
        else:
            err.print(f"[red]Error:[/red] {exc.message}")
    except KeyboardInterrupt:
        err.print("\nCancelled.")
        return 130
    finally:
        show_advice()
    return 1


def show_advice() -> None:
    """After a command: what the server said about this version (a notice from us, or a newer release)."""
    command = ADVICE.get("upgrade_command") or "pip install -U djangocloud-cli"
    if ADVICE.get("notice"):
        err.print(f"\n[yellow]Notice:[/yellow] {ADVICE['notice']}")
    if ADVICE.get("latest"):
        err.print(
            f"[yellow]A newer djangocloud-cli is available[/yellow] ({ADVICE['latest']}; you have {__version__}). "
            f"Upgrade: {command}"
        )


def main() -> None:
    sys.exit(run(sys.argv[1:]))
