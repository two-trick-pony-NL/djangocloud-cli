"""The primary entry point: `python manage.py djangocloud <command>` inside a Django project."""

import pytest

django = pytest.importorskip("django")


@pytest.fixture(scope="module", autouse=True)
def configured_django():
    from django.conf import settings

    if not settings.configured:
        settings.configure(INSTALLED_APPS=["djangocloud_cli"], SECRET_KEY="x", USE_TZ=True)
        django.setup()


def manage(*args):
    from django.core.management import ManagementUtility

    ManagementUtility(["manage.py", *args]).execute()


def test_the_command_is_discovered_and_help_names_the_manage_py_form(capsys):
    with pytest.raises(SystemExit) as done:
        manage("djangocloud", "help")
    assert done.value.code == 0
    out = capsys.readouterr().out
    assert "usage: python manage.py djangocloud" in out and "login" in out


def test_flags_reach_our_parser_untouched_by_djangos(capsys):
    with pytest.raises(SystemExit):
        manage("djangocloud", "logs", "--help")
    out = capsys.readouterr().out
    assert "--follow" in out and "--since" in out


def test_messages_tell_people_to_type_the_manage_py_command(api, capsys):
    with pytest.raises(SystemExit) as done:
        manage("djangocloud", "whoami")
    assert done.value.code == 1
    assert "python manage.py djangocloud login" in capsys.readouterr().out
