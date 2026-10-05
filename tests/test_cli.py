import json
import os
import stat

import pytest

from djangocloud_cli import auth, cli, config, link
from djangocloud_cli.api import ApiError, Client


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)


# ---------- help / config ----------


def test_help_lists_every_command(capsys):
    assert cli.run(["help"]) == 0
    out = capsys.readouterr().out
    for command in ("login", "logout", "whoami", "deploy", "link", "unlink", "status", "logs", "help"):
        assert command in out


def test_help_for_a_command_shows_its_options(capsys):
    with pytest.raises(SystemExit):
        cli.run(["help", "logs"])
    assert "--follow" in capsys.readouterr().out


def test_token_file_is_private_and_round_trips():
    path = config.save_token("tok_123")
    assert config.load_token() == "tok_123"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert json.loads(path.read_text()) == {"token": "tok_123"}


def test_env_token_beats_the_file(monkeypatch):
    config.save_token("from-file")
    monkeypatch.setenv(config.TOKEN_ENV, "from-env")
    assert config.load_token() == "from-env"


# ---------- link file ----------


def test_link_file_is_written_with_a_gitignore_and_no_secrets(tmp_path):
    path = link.save(tmp_path, {"id": 7, "slug": "my-shop"}, "https://x/api/v1")
    assert json.loads(path.read_text()) == {"project_id": 7, "project": "my-shop", "api": "https://x/api/v1"}
    assert (tmp_path / ".djangocloud" / ".gitignore").read_text() == "*\n"
    assert link.load(tmp_path)["slug"] == "my-shop"
    assert link.remove(tmp_path) and link.load(tmp_path) is None


def test_find_root_prefers_a_linked_parent_then_manage_py(tmp_path):
    (tmp_path / "manage.py").write_text("")
    sub = tmp_path / "apps" / "shop"
    sub.mkdir(parents=True)
    assert link.find_root(sub) == tmp_path.resolve()
    link.save(tmp_path, {"id": 1, "slug": "abc"}, "x")
    assert link.find_root(sub) == tmp_path.resolve()


# ---------- login (against a fake API over HTTP) ----------


def test_login_polls_until_approved_then_stores_the_token(api, capsys):
    opened = []
    client = Client(config.api_url())
    token = auth.login(client, sleep=lambda s: None, open_browser=opened.append)
    assert token == api.token and config.load_token() == api.token
    assert opened == ["https://example.test/dashboard/cli/?code=ABCD-EFGH"]
    assert "ABCD-EFGH" in capsys.readouterr().out


def test_login_survives_a_missing_browser(api):
    def no_browser(url):
        raise RuntimeError("no display")

    auth.login(Client(config.api_url()), sleep=lambda s: None, open_browser=no_browser)
    assert config.load_token() == api.token


def test_whoami_signed_in_out_and_stale(api, capsys):
    assert cli.run(["whoami"]) == 1 and "Not signed in" in capsys.readouterr().out
    config.save_token("dcl_stale")
    assert cli.run(["whoami"]) == 1 and "no longer valid" in capsys.readouterr().out
    config.save_token(api.token)
    assert cli.run(["whoami"]) == 0 and "a@example.com" in capsys.readouterr().out


def test_logout_forgets_the_token(api):
    config.save_token(api.token)
    cli.run(["logout"])
    assert config.load_token() is None


# ---------- deploy / link ----------


def test_deploy_creates_and_links_a_project_non_interactively(api, tmp_path, capsys):
    config.save_token(api.token)
    code = cli.run(["deploy", "--name", "My Shop", "--size", "micro", "--yes"])
    assert code == 2  # linked, but the pipeline isn't built yet
    assert api.projects[0]["power"] == "micro"
    assert link.load(tmp_path)["slug"] == "my-shop"
    assert "isn't available yet" in capsys.readouterr().err


def test_linked_folder_skips_all_prompts(api, tmp_path):
    config.save_token(api.token)
    link.save(tmp_path, {"id": 1, "slug": "already"}, config.api_url())
    cli.run(["deploy"])
    assert not any(r[1] == "/api/v1/projects" and r[0] == "POST" for r in api.requests)


def test_link_to_an_existing_project_by_slug(api, tmp_path):
    api.projects.append({"id": 9, "slug": "blog", "name": "Blog", "power": "nano"})
    config.save_token(api.token)
    assert cli.run(["link", "--project", "blog"]) == 0
    assert link.load(tmp_path)["slug"] == "blog"
    assert cli.run(["link", "--project", "missing"]) == 1


def test_creating_a_project_without_a_card_explains_why(api, tmp_path, capsys):
    api.card = False
    config.save_token(api.token)
    assert cli.run(["deploy", "--name", "Shop", "--size", "nano", "--yes"]) == 1
    assert "Add a card" in capsys.readouterr().err
    assert link.load(tmp_path) is None


def test_deploy_without_a_terminal_or_token_fails_clearly(api, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert cli.run(["deploy", "--name", "x", "--yes"]) == 1
    assert "DJANGOCLOUD_TOKEN" in capsys.readouterr().err


def test_unknown_size_lists_the_valid_ones(api, capsys):
    config.save_token(api.token)
    assert cli.run(["link", "--name", "Shop", "--size", "gigantic", "--yes"]) == 1
    assert "nano" in capsys.readouterr().err


def test_unreachable_server_is_a_clean_error(monkeypatch):
    monkeypatch.setenv(config.API_ENV, "http://127.0.0.1:1/api/v1")
    with pytest.raises(ApiError) as caught:
        Client(config.api_url()).get("/sizes")
    assert caught.value.code == "unreachable"


# ---------- --no-input (CI) ----------


@pytest.fixture(autouse=True)
def reset_no_input():
    yield
    from djangocloud_cli import ui

    ui.set_no_input(False)


def test_no_input_with_a_token_and_a_project_flag_runs_without_any_prompt(api, tmp_path, monkeypatch):
    monkeypatch.setenv(config.TOKEN_ENV, api.token)
    api.projects.append({"id": 9, "slug": "blog", "name": "Blog", "power": "nano"})
    monkeypatch.setattr("builtins.input", lambda *a: pytest.fail("prompted"))
    code = cli.run(["--no-input", "deploy", "--project", "blog"])
    assert code == 2 and link.load(tmp_path)["slug"] == "blog"  # linked; pipeline still pending


def test_no_input_never_starts_a_browser_login(api, capsys):
    assert cli.run(["--no-input", "deploy", "--project", "blog"]) == 1
    err = capsys.readouterr().err
    assert "DJANGOCLOUD_TOKEN" in err
    assert not any(r[1] == "/api/v1/auth/device" for r in api.requests)


def test_no_input_cannot_prompt_for_a_missing_project(api, monkeypatch, capsys):
    monkeypatch.setenv(config.TOKEN_ENV, api.token)
    assert cli.run(["--no-input", "deploy"]) == 1
    assert "--project" in capsys.readouterr().err


def test_no_input_can_create_a_project_when_fully_specified(api, monkeypatch, tmp_path):
    monkeypatch.setenv(config.TOKEN_ENV, api.token)
    assert cli.run(["--no-input", "deploy", "--name", "CI Shop", "--size", "nano"]) == 2
    assert api.projects[0]["slug"] == "ci-shop"


def test_no_input_env_var_works_too(api, monkeypatch, capsys):
    monkeypatch.setenv("DJANGOCLOUD_NO_INPUT", "1")
    assert cli.run(["login"]) == 1
    assert "browser" in capsys.readouterr().err
