"""`cloud` command: one parser shared by the standalone script and `manage.py cloud`."""

import argparse
import sys

from . import __version__, auth, config, link
from .api import ApiError, Client
from .ui import NotInteractive, confirm, console, err, interactive, no_input, select, set_no_input, text

COMING_SOON = "'cloud {name}' isn't available yet: the build and deploy pipeline is still being built."


def build_parser(prog: str = "cloud") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Deploy your Django app.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--no-input",
        action="store_true",
        help="Never prompt or open a browser (for CI). Needs DJANGOCLOUD_TOKEN and a linked folder or --project",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("login", help="Sign in (approve a code in your browser)")
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
        cmd.add_argument("-y", "--yes", action="store_true", help="Don't ask for confirmation")
    sub.add_parser("unlink", help="Detach this folder from its project")
    sub.add_parser("status", help="Show the current release and its state")

    logs = sub.add_parser("logs", help="Show a project's logs")
    logs.add_argument("-f", "--follow", action="store_true", help="Keep streaming new lines")
    logs.add_argument("--source", choices=["app", "build", "release"], help="Only this kind of log line")
    logs.add_argument("--since", metavar="DURATION", help="Only lines newer than this, e.g. 2h")

    helper = sub.add_parser("help", help="Show this help")
    helper.add_argument("topic", nargs="?", help="A command to get help for")
    return parser


class CliError(Exception):
    pass


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
            "on your own machine run 'cloud login'."
        )
    console.print("You're not signed in yet.")
    auth.login(client)
    console.print(f"[green]✓[/green] Signed in as {client.get('/me')['email']}")


def _price(cents: int) -> str:
    return f"${cents / 100:,.0f}" if cents % 100 == 0 else f"${cents / 100:,.2f}"


def _size_label(size: dict) -> str:
    specs = f"{size['vcpu']:g} vCPU, {size['ram_gb']:g} GB RAM"
    return f"{size['label']:<8} {specs:<22} {_price(size['price_cents'])}/month"


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


def create_project(client: Client, root, args) -> dict:
    name = args.name or text("Project name", default=root.name)
    sizes = client.get("/sizes")["sizes"]
    if args.size:
        size = next((s for s in sizes if s["power"] == args.size), None)
        if size is None:
            raise CliError(f"Unknown size {args.size!r}. Choose from: {', '.join(s['power'] for s in sizes)}.")
    else:
        size = select("Server size", [(_size_label(s), s) for s in sizes])
    monthly = _price(size["price_cents"])
    if not (args.yes or no_input()) and not confirm(f"This will cost {monthly} per month. Continue?"):
        raise CliError("Cancelled. Nothing was created.")
    try:
        return client.post("/projects", {"name": name, "power": size["power"]})
    except ApiError as exc:
        if exc.code == "payment_required":
            raise CliError(f"{exc.message}") from None
        raise


def cmd_login(args, root) -> int:
    if not interactive():
        raise CliError("'cloud login' needs a browser. In CI use a DJANGOCLOUD_TOKEN instead.")
    client = make_client()
    auth.login(client)
    console.print(f"[green]✓[/green] Signed in as {client.get('/me')['email']}")
    return 0


def cmd_whoami(args, root) -> int:
    client = make_client()
    if not client.token:
        console.print("Not signed in. Run [bold]cloud login[/bold].")
        return 1
    try:
        me = client.get("/me")
    except ApiError as exc:
        if exc.code != "unauthorized":
            raise
        console.print("Your token is no longer valid. Run [bold]cloud login[/bold].")
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
    ensure_linked(client, root, args, force=True)
    return 0


def cmd_unlink(args, root) -> int:
    console.print("Unlinked." if link.remove(root) else "This folder isn't linked.")
    return 0


def cmd_deploy(args, root) -> int:
    client = make_client()
    ensure_login(client)
    project = ensure_linked(client, root, args)
    console.print(f"Ready to deploy [bold]{project['slug']}[/bold].")
    err.print(COMING_SOON.format(name="deploy") + " Your project is linked, so the next release will pick it up.")
    return 2


COMMANDS = {
    "login": cmd_login,
    "logout": cmd_logout,
    "whoami": cmd_whoami,
    "link": cmd_link,
    "unlink": cmd_unlink,
    "deploy": cmd_deploy,
}


def run(argv: list[str] | None = None, prog: str = "cloud") -> int:
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
        err.print(COMING_SOON.format(name=args.command))
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
