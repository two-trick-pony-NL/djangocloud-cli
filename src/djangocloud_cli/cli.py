"""`cloud` command: one parser shared by the standalone script and `manage.py cloud`."""

import argparse
import sys

from . import __version__, config

COMING_SOON = "'cloud {name}' isn't available yet: the DjangoCloud API it talks to is still being built."


def build_parser(prog: str = "cloud") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog, description="Deploy your Django app.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    sub.add_parser("login", help="Sign in (opens a code-based approval in your browser)")
    sub.add_parser("logout", help="Forget the stored token")
    sub.add_parser("whoami", help="Show who you are signed in as")
    sub.add_parser("deploy", help="Package, upload and deploy this project")
    sub.add_parser("status", help="Show the current release and its state")

    logs = sub.add_parser("logs", help="Show a project's logs")
    logs.add_argument("-f", "--follow", action="store_true", help="Keep streaming new lines")
    logs.add_argument("--source", choices=["app", "build", "release"], help="Only this kind of log line")
    logs.add_argument("--since", metavar="DURATION", help="Only lines newer than this, e.g. 2h")

    helper = sub.add_parser("help", help="Show this help")
    helper.add_argument("topic", nargs="?", help="A command to get help for")
    return parser


def run(argv: list[str] | None = None, prog: str = "cloud") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(argv)

    if args.command in (None, "help"):
        topic = getattr(args, "topic", None)
        if topic:
            return run([topic, "--help"], prog)  # argparse prints the sub-command's help, then exits
        parser.print_help()
        return 0
    if args.command == "logout":
        print("Signed out." if config.clear_token() else "You weren't signed in.")
        return 0
    if args.command == "whoami":
        print("Signed in (token found)." if config.load_token() else "Not signed in. Run 'cloud login'.")
        return 0
    print(COMING_SOON.format(name=args.command), file=sys.stderr)
    return 2


def main() -> None:
    sys.exit(run(sys.argv[1:]))
