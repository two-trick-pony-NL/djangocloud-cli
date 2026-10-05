"""Terminal output and prompts. Prompts need a terminal: scripts must pass flags instead."""

import os
import sys

import questionary
from rich.console import Console

console = Console()
err = Console(stderr=True)


class NotInteractive(Exception):
    pass


NO_INPUT_ENV = "DJANGOCLOUD_NO_INPUT"
_no_input = False


def set_no_input(value: bool) -> None:
    """--no-input (or DJANGOCLOUD_NO_INPUT=1): never prompt, never open a browser. For CI."""
    global _no_input
    _no_input = value


def no_input() -> bool:
    return _no_input or os.environ.get(NO_INPUT_ENV, "") not in ("", "0", "false")


def interactive() -> bool:
    return not no_input() and sys.stdin.isatty() and sys.stdout.isatty()


def _need_terminal(what: str) -> None:
    if not interactive():
        raise NotInteractive(
            f"Can't ask for {what} without a terminal (or with --no-input). "
            "Pass it as a flag, e.g. --project, --name, --size, --yes."
        )


def ask(question: questionary.Question):
    answer = question.ask()
    if answer is None:  # Ctrl-C inside a prompt
        raise KeyboardInterrupt
    return answer


def text(message: str, default: str = "") -> str:
    _need_terminal(message.lower())
    return ask(questionary.text(message, default=default))


def confirm(message: str, default: bool = True) -> bool:
    _need_terminal("confirmation")
    return ask(questionary.confirm(message, default=default))


def select(message: str, choices: list[tuple[str, object]]):
    """Arrow-key menu over (label, value) pairs; returns the chosen value."""
    _need_terminal(message.lower())
    options = [questionary.Choice(title=label, value=value) for label, value in choices]
    return ask(questionary.select(message, choices=options))
