import json
import os
import stat

import pytest

from djangocloud_cli import cli, config


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)


def test_help_lists_every_command(capsys):
    assert cli.run(["help"]) == 0
    out = capsys.readouterr().out
    for command in ("login", "logout", "whoami", "deploy", "status", "logs", "help"):
        assert command in out


def test_help_for_a_command_shows_its_options(capsys):
    with pytest.raises(SystemExit):
        cli.run(["help", "logs"])
    assert "--follow" in capsys.readouterr().out


def test_no_arguments_prints_help(capsys):
    assert cli.run([]) == 0
    assert "usage: cloud" in capsys.readouterr().out


def test_token_file_is_private_and_round_trips():
    path = config.save_token("tok_123")
    assert config.load_token() == "tok_123"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text()) == {"token": "tok_123"}


def test_env_token_beats_the_file(monkeypatch):
    config.save_token("from-file")
    monkeypatch.setenv(config.TOKEN_ENV, "from-env")
    assert config.load_token() == "from-env"


def test_logout_and_whoami(capsys):
    assert cli.run(["whoami"]) == 0 and "Not signed in" in capsys.readouterr().out
    config.save_token("t")
    assert cli.run(["whoami"]) == 0 and "Signed in" in capsys.readouterr().out
    cli.run(["logout"])
    assert config.load_token() is None


def test_unbuilt_commands_say_so_instead_of_pretending(capsys):
    assert cli.run(["deploy"]) == 2
    assert "isn't available yet" in capsys.readouterr().err
