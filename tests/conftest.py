import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from djangocloud_cli import config


class FakeApi:
    """Just enough of the DjangoCloud API for the CLI, over real HTTP."""

    def __init__(self):
        self.polls_until_approved = 1
        self.projects = []
        self.card = True
        self.requests = []
        self.token = "dcl_testtoken"

    def handler(api):  # noqa: N805 - `api` is the FakeApi, this builds the request handler class
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status, body):
                payload = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _body(self):
                length = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(length) or b"{}")

            def _authed(self):
                if self.headers.get("Authorization") == f"Bearer {api.token}":
                    return True
                self._reply(401, {"error": "unauthorized", "message": "Missing or invalid token."})
                return False

            def do_GET(self):
                api.requests.append(("GET", self.path))
                if self.path == "/api/v1/sizes":
                    return self._reply(200, {"sizes": [
                        {"power": "nano", "label": "Nano", "vcpu": 0.25, "ram_gb": 0.5, "price_cents": 1000},
                        {"power": "micro", "label": "Micro", "vcpu": 0.25, "ram_gb": 1, "price_cents": 1500},
                    ]})  # fmt: skip
                if not self._authed():
                    return
                if self.path == "/api/v1/me":
                    return self._reply(200, {"email": "a@example.com"})
                if self.path == "/api/v1/projects":
                    return self._reply(200, {"projects": api.projects})
                self._reply(404, {"error": "not_found", "message": "nope"})

            def do_POST(self):
                body = self._body()
                api.requests.append(("POST", self.path, body))
                if self.path == "/api/v1/auth/device":
                    return self._reply(200, {
                        "device_code": "dev", "user_code": "ABCD-EFGH", "interval": 0, "expires_in": 60,
                        "verification_uri_complete": "https://example.test/dashboard/cli/?code=ABCD-EFGH",
                    })  # fmt: skip
                if self.path == "/api/v1/auth/token":
                    if api.polls_until_approved > 0:
                        api.polls_until_approved -= 1
                        return self._reply(400, {"error": "authorization_pending", "message": "wait"})
                    return self._reply(200, {"access_token": api.token, "token_type": "bearer"})
                if not self._authed():
                    return
                if self.path == "/api/v1/projects":
                    if not api.card:
                        return self._reply(402, {"error": "payment_required", "message": "Add a card first."})
                    project = {"id": len(api.projects) + 1, "slug": body["name"].lower().replace(" ", "-"),
                               "name": body["name"], "power": body["power"]}  # fmt: skip
                    api.projects.append(project)
                    return self._reply(201, project)
                self._reply(404, {"error": "not_found", "message": "nope"})

        return Handler


@pytest.fixture
def api(monkeypatch, tmp_path):
    fake = FakeApi()
    server = HTTPServer(("127.0.0.1", 0), fake.handler())
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv(config.API_ENV, f"http://127.0.0.1:{server.server_port}/api/v1")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv(config.TOKEN_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "manage.py").write_text("")  # makes tmp_path look like a Django project root
    yield fake
    server.shutdown()
