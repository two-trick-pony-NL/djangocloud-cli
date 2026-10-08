"""`djangocloud` command: one parser shared by the standalone script and `manage.py djangocloud`."""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

from . import __version__, auth, config, detect, link, onboarding, package
from .api import ApiError, Client
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
    else:
        ensure_build_settings(client, root, args.wsgi_module, args.asgi_module)
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
        if exc.code == "deploy_in_progress":
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


COMMANDS = {
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


def run(argv: list[str] | None = None, prog: str = STANDALONE) -> int:
    global _prog
    _prog = prog
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    set_no_input(getattr(args, "no_input", False))

    if args.command in (None, "help"):
        topic = getattr(args, "topic", None)
        if topic:
            return run([topic, "--help"], prog)  # argparse prints the sub-command's help, then exits
        parser.print_help()
        return 0
    handler = COMMANDS.get(args.command)
    if handler is None:
        err.print(COMING_SOON.format(command=say(args.command)))
        return 2
    try:
        return handler(args, link.find_root())
    except (CliError, NotInteractive) as exc:
        err.print(f"[red]Error:[/red] {exc}")
    except ApiError as exc:
        err.print(f"[red]Error:[/red] {exc.message}")
    except KeyboardInterrupt:
        err.print("\nCancelled.")
        return 130
    return 1


def main() -> None:
    sys.exit(run(sys.argv[1:]))
