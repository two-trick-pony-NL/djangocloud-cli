"""`python manage.py djangocloud <command>`: the same CLI, run inside the user's Django project."""

from django.core.management.base import BaseCommand

from djangocloud_cli.cli import run


class Command(BaseCommand):
    help = "Deploy this project: djangocloud login | deploy | logs | status | help"

    def add_arguments(self, parser):
        parser.add_argument("args", nargs="*")  # handed to the shared parser untouched

    def run_from_argv(self, argv):
        # Skip Django's own option parsing so flags like -f and --since reach our parser.
        raise SystemExit(run(argv[2:], prog="manage.py djangocloud"))
