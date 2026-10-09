import pytest

from djangocloud_cli import cli, config, envfile, link

# ---------- the .env reader ----------


def test_reads_what_people_actually_have_in_a_dotenv_file():
    text = """
# a comment
export DEBUG=False
SECRET_KEY="a b#c"          # trailing comment
DATABASE_URL=postgres://u:p@h/db   # inline comment
SINGLE='$not_expanded'
EMPTY=
ESCAPED="line1\\nline2 \\"q\\""
PEM="-----BEGIN KEY-----
abc
-----END KEY-----"
DEBUG=True
"""
    env, errors = envfile.parse(text)
    assert errors == []
    assert env["DEBUG"] == "True"  # a later duplicate wins
    assert env["SECRET_KEY"] == "a b#c"
    assert env["DATABASE_URL"] == "postgres://u:p@h/db"
    assert env["SINGLE"] == "$not_expanded" and env["EMPTY"] == ""
    assert env["ESCAPED"] == 'line1\nline2 "q"'
    assert env["PEM"] == "-----BEGIN KEY-----\nabc\n-----END KEY-----"


@pytest.mark.parametrize("bad", ["JUSTAWORD", "1BAD=x", "BAD KEY=x", 'OPEN="never closed'])
def test_bad_lines_are_reported_with_their_line_number(bad):
    _, errors = envfile.parse(f"OK=1\n{bad}\n")
    assert errors and errors[0].startswith("Line 2")


# ---------- env push / env list ----------


@pytest.fixture
def linked(api, tmp_path):
    config.save_token(api.token)
    link.save(tmp_path, {"id": 1, "slug": "my-shop"}, config.api_url())
    return tmp_path


def dotenv(tmp_path, text="A=1\nB=two words\n"):
    path = tmp_path / "prod.env"
    path.write_text(text)
    return str(path)


def test_push_sends_the_file_and_never_prints_a_value(api, linked, tmp_path, capsys):
    assert cli.run(["env", "push", dotenv(tmp_path, "TOKEN=hunter2hunter2\nA=1\n")]) == 0
    out = capsys.readouterr().out
    assert api.env_saved == [{"variables": {"TOKEN": "hunter2hunter2", "A": "1"}, "replace": False}]
    assert "Saved 2 variable(s)" in out and "hunter2" not in out


def test_push_says_which_names_are_new_and_which_overwrite(api, linked, tmp_path, capsys):
    api.env_existing = ["A", "OLD"]
    assert cli.run(["env", "push", dotenv(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "new: B" in out and "overwrite: A" in out and "remove" not in out
    assert api.env_saved[0]["replace"] is False  # merge by default


def test_dry_run_changes_nothing(api, linked, tmp_path, capsys):
    assert cli.run(["env", "push", dotenv(tmp_path), "--dry-run"]) == 0
    assert api.env_saved == [] and "Dry run" in capsys.readouterr().out


def test_prune_removes_the_rest_but_never_the_platforms_own_variables(api, linked, tmp_path, capsys):
    api.env_existing = ["A", "OLD", "DJANGOCLOUD_HOSTED_DB_PASSWORD"]
    assert cli.run(["--no-input", "env", "push", dotenv(tmp_path), "--prune"]) == 0
    out = capsys.readouterr().out
    assert "remove: OLD" in out and "DJANGOCLOUD_HOSTED_DB_PASSWORD" not in out
    assert api.env_saved[0]["replace"] is True


def test_from_env_reads_named_variables_and_skips_empty_ones(api, linked, monkeypatch, capsys):
    monkeypatch.setenv("SECRET_A", "alpha")
    monkeypatch.setenv("SECRET_B", "")
    monkeypatch.delenv("SECRET_C", raising=False)
    assert cli.run(["--no-input", "env", "push", "--from-env", "SECRET_A", "SECRET_B", "SECRET_C"]) == 0
    captured = capsys.readouterr()
    assert api.env_saved[0]["variables"] == {"SECRET_A": "alpha"}
    assert "SECRET_B" in captured.err and "SECRET_C" in captured.err and "alpha" not in captured.out + captured.err


@pytest.mark.parametrize("argv", [["env", "push"], ["env", "push", "x.env", "--from-env", "A"]])
def test_needs_exactly_one_source(api, linked, argv, capsys):
    assert cli.run(argv) == 1
    assert "either a .env file or --from-env" in capsys.readouterr().err
    assert api.env_saved == []


def test_a_bad_file_sends_nothing(api, linked, tmp_path, capsys):
    assert cli.run(["env", "push", dotenv(tmp_path, "OK=1\n1BAD=x\n")]) == 1
    assert "Line 2" in capsys.readouterr().err and api.env_saved == []


def test_missing_file_is_a_clear_error(api, linked, tmp_path, capsys):
    assert cli.run(["env", "push", str(tmp_path / "nope.env")]) == 1
    assert "Couldn't read" in capsys.readouterr().err


def test_server_validation_errors_are_shown(api, linked, tmp_path, capsys):
    api.env_error = (400, {"error": "invalid_env", "message": "Nothing was saved: A: too long."})
    assert cli.run(["env", "push", dotenv(tmp_path)]) == 1
    assert "Nothing was saved" in capsys.readouterr().err


def test_an_old_server_gets_a_plain_explanation(api, linked, tmp_path, capsys):
    api.old_server = True
    assert cli.run(["env", "push", dotenv(tmp_path)]) == 1
    assert "doesn't support that yet" in capsys.readouterr().err


def test_list_shows_names_only(api, linked, capsys):
    api.env_existing = ["A", "B"]
    assert cli.run(["env", "list"]) == 0
    out = capsys.readouterr().out
    assert "2 variable(s)" in out and "A" in out and "B" in out


# ---------- rollback ----------


def test_rollback_to_a_given_version_asks_for_nothing_and_follows_the_new_release(api, linked, capsys):
    assert cli.run(["--no-input", "rollback", "1"]) == 0
    assert api.rollback_requests == [{"version": 1}]
    assert "v1 is live" in capsys.readouterr().out  # the fake release script ends live


def test_rollback_says_what_it_does_and_does_not_reverse(api, linked, capsys):
    cli.run(["--no-input", "rollback", "1", "--no-wait"])
    out = capsys.readouterr().out
    assert "v2 → v1" in out and "migrations are not reversed" in out and "Queued as v3" in out


def test_rollback_without_a_version_cannot_ask_in_ci(api, linked, capsys):
    assert cli.run(["--no-input", "rollback"]) == 1
    assert api.rollback_requests == []


def test_a_refused_rollback_shows_the_reason(api, linked, capsys):
    api.rollback_error = (409, {"error": "rollback_refused", "message": "v2 is already the live release."})
    assert cli.run(["--no-input", "rollback", "2"]) == 1
    assert "already the live release" in capsys.readouterr().err


# ---------- help ----------


def test_help_lists_every_command_with_all_of_its_options(capsys):
    assert cli.run(["help"]) == 0  # bare `djangocloud` prints only the short menu; see test_cli
    out = capsys.readouterr().out
    for expected in (
        "rollback", "env push", "env list", "--from-env", "--prune", "--dry-run", "--instances", "--since",
        "--follow", "--github", "--no-wait", "--own-cloud",
    ):  # fmt: skip
        assert expected in out, expected
