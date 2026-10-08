import json
from datetime import datetime, timedelta, timezone

import pytest

from djangocloud_cli import cli, config, link
from djangocloud_cli.api import ApiError, Client


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(cli, "_sleep", lambda s: None)


@pytest.fixture
def linked(api, tmp_path):
    config.save_token(api.token)
    link.save(tmp_path, {"id": 1, "slug": "my-shop"}, config.api_url())
    return tmp_path


# ---------- status ----------


def test_status_shows_whether_it_is_live_where_and_the_latest_releases(api, linked, capsys):
    assert cli.run(["status"]) == 0
    out = capsys.readouterr().out
    assert "My Shop" in out and "Live" in out and "https://my-shop.example.test" in out
    assert "Nano" in out and "eu-central-1" in out
    assert "v2" in out and "bbbbbbb" in out and "v1" in out and "live" in out


def test_status_exits_nonzero_and_says_so_when_the_server_is_not_responding(api, linked, capsys):
    api.detail["status"] = {"label": "Not responding", "tone": "red", "detail": "v2 is deployed but not answering."}
    api.detail["health"] = {"state": "unhealthy", "checked_at": None, "since": None, "error": "HTTP 502"}
    assert cli.run(["status"]) == 1
    out = capsys.readouterr().out
    assert "Not responding" in out and "HTTP 502" in out


def test_status_json_is_machine_readable(api, linked, capsys):
    assert cli.run(["status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["slug"] == "my-shop"


def test_status_for_a_project_with_nothing_deployed(api, linked, capsys):
    api.detail.update(
        releases=[], live_release=None, status={"label": "Waiting for first deploy", "tone": "gray", "detail": "x"}
    )
    assert cli.run(["status"]) == 0
    assert "Nothing deployed yet" in capsys.readouterr().out


def test_status_by_slug_needs_no_link(api, tmp_path, capsys):
    config.save_token(api.token)
    api.projects.append({"id": 1, "slug": "my-shop", "name": "My Shop", "power": "nano"})
    assert cli.run(["status", "--project", "my-shop"]) == 0
    assert cli.run(["status", "--project", "nope"]) == 1
    assert "No project 'nope'" in capsys.readouterr().err


def test_an_unlinked_folder_is_told_how_to_fix_it(api, tmp_path, capsys):
    config.save_token(api.token)
    assert cli.run(["status"]) == 1
    assert "isn't linked" in capsys.readouterr().err


def test_an_older_server_gets_a_clear_message_not_a_stack_trace(api, linked, capsys):
    api.old_server = True
    assert cli.run(["status"]) == 1
    assert "doesn't support that yet" in capsys.readouterr().err
    assert cli.run(["logs"]) == 1
    assert "doesn't support that yet" in capsys.readouterr().err


def test_ago_reads_naturally():
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    assert cli.ago(None) == "never"
    assert cli.ago((now - timedelta(seconds=2)).isoformat(), now) == "just now"
    assert cli.ago((now - timedelta(seconds=40)).isoformat(), now) == "40 s ago"
    assert cli.ago((now - timedelta(minutes=7)).isoformat(), now) == "7 min ago"
    assert cli.ago((now - timedelta(hours=5)).isoformat(), now) == "5 h ago"
    assert cli.ago((now - timedelta(days=3)).isoformat(), now) == "3 d ago"


# ---------- logs ----------


def test_logs_prints_the_latest_lines_and_never_treats_them_as_markup(api, linked, capsys):
    assert cli.run(["logs"]) == 0
    out = capsys.readouterr().out
    assert "booting" in out and "[red]not markup[/red]" in out
    assert api.log_requests[0]["lines"] == ["100"] and "after" not in api.log_requests[0]


def test_logs_sends_its_filters(api, linked):
    cli.run(["logs", "--source", "build", "--since", "2h", "-n", "20"])
    assert api.log_requests[0] == {"source": ["build"], "since": ["2h"], "lines": ["20"]}


@pytest.mark.parametrize("since", ["2", "2weeks", "h", "1.5h"])
def test_a_bad_since_is_refused_before_any_request(api, linked, capsys, since):
    assert cli.run(["logs", "--since", since]) == 1
    assert "--since must look like" in capsys.readouterr().err
    assert api.log_requests == []


def test_following_polls_with_the_cursor_until_interrupted(api, linked, capsys, monkeypatch):
    calls = {"n": 0}

    def sleep_then_stop(seconds):
        calls["n"] += 1
        if calls["n"] > 3:
            raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_sleep", sleep_then_stop)
    assert cli.run(["logs", "-f", "--source", "app"]) == 0  # Ctrl-C is how a follow normally ends
    out = capsys.readouterr().out
    assert "booting" in out and "it broke" in out
    polls = api.log_requests
    assert "after" not in polls[0] and polls[1]["after"] == ["2"] and polls[2]["after"] == ["3"]
    assert all(p["source"] == ["app"] for p in polls)


def test_an_empty_log_says_so(api, linked, capsys):
    api.log_script = [{"lines": [], "cursor": 0}]
    assert cli.run(["logs"]) == 0
    assert "No log lines match" in capsys.readouterr().out


def test_following_survives_network_blips_but_not_a_dead_server(api, linked, monkeypatch):
    real_get = Client.get
    state = {"fail": 2}

    def flaky(self, path):
        if "after=" in path and state["fail"] > 0:
            state["fail"] -= 1
            raise ApiError(0, "unreachable", "down")
        return real_get(self, path)

    stop = {"n": 0}

    def stop_soon(seconds):
        stop["n"] += 1
        if stop["n"] > 6:
            raise KeyboardInterrupt

    monkeypatch.setattr(Client, "get", flaky)
    monkeypatch.setattr(cli, "_sleep", stop_soon)
    assert cli.run(["logs", "-f"]) == 0
    state["fail"], stop["n"] = 99, 0
    assert cli.run(["logs", "-f"]) == 1  # more than five failures in a row is not a blip


def test_the_commands_are_no_longer_coming_soon(api, linked, capsys):
    cli.run(["status"])
    cli.run(["logs"])
    assert "isn't available yet" not in capsys.readouterr().err
