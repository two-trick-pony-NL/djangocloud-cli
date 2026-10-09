import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import quote

import pytest

from djangocloud_cli import api, cli, config

ME = {
    "email": "a@example.com", "subscription_active": True, "suspended": False, "aws_connected": True,
    "can_host": False, "ready_to_deploy": True,
}  # fmt: skip


@pytest.fixture
def server(monkeypatch, tmp_path):
    """A server whose answers carry (or refuse with) the version advice headers."""
    state = {"status": 200, "headers": {}, "body": ME}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            payload = json.dumps(state["body"]).encode()
            self.send_response(state["status"])
            for key, value in state["headers"].items():
                self.send_header(key, value)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv(config.API_ENV, f"http://127.0.0.1:{httpd.server_port}/api/v1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "manage.py").write_text("")
    config.save_token("dcl_x")
    yield state
    httpd.shutdown()


def test_a_notice_and_a_newer_version_are_shown_after_the_command(server, capsys):
    server["headers"] = {
        "X-DjangoCloud-Notice": quote("0.4 renames 'scale --size'. Upgrade before Friday."),
        "X-DjangoCloud-Latest-Version": "0.4.0",
        "X-DjangoCloud-Upgrade-Command": "uv tool upgrade djangocloud-cli",
    }
    assert cli.run(["whoami"]) == 0
    err = capsys.readouterr().err
    assert "renames 'scale --size'" in err and "0.4.0" in err and "uv tool upgrade djangocloud-cli" in err


def test_nothing_extra_is_printed_when_the_server_has_no_advice(server, capsys):
    assert cli.run(["whoami"]) == 0
    assert capsys.readouterr().err == ""


def test_advice_does_not_carry_over_between_commands(server, capsys):
    server["headers"] = {"X-DjangoCloud-Notice": quote("Heads up")}
    cli.run(["whoami"])
    capsys.readouterr()
    server["headers"] = {}
    cli.run(["whoami"])
    assert "Heads up" not in capsys.readouterr().err


def test_a_too_old_cli_is_told_to_upgrade_and_how(server, capsys):
    server["status"] = 426
    server["body"] = {
        "error": "upgrade_required",
        "message": "This version of the DjangoCloud CLI is no longer supported (version 0.3.0 or newer is needed). "
        "Upgrade with: pip install -U djangocloud-cli",
    }
    assert cli.run(["whoami"]) == 1
    err = capsys.readouterr().err
    assert "Upgrade needed" in err and "pip install -U djangocloud-cli" in err
    assert api.ADVICE.get("notice") is None
