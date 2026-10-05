import json
import threading
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from djangocloud_cli import config


class FakeApi:
    """Just enough of the DjangoCloud API for the CLI, over real HTTP."""

    def __init__(self):
        self.polls_until_approved = 1
        self.projects = []
        self.card = True
        self.aws = True
        self.uploads = []  # (project_id, fields, tarball bytes or None)
        self.release_error = None  # (status, body) to answer the upload with
        self.release_script = [
            {
                "status": "building",
                "done": False,
                "ok": False,
                "logs": [{"id": 1, "level": "info", "message": "Building v1"}],
            },
            {
                "status": "active",
                "done": True,
                "ok": True,
                "logs": [{"id": 2, "level": "info", "message": "v1 is live"}],
            },
        ]
        self.requests = []
        self.token = "dcl_testtoken"
        self.release_polls = 0

    def handler(api):
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
                raw = self.rfile.read(length) or b"{}"
                if self.headers.get("Content-Type", "").startswith("multipart/form-data"):
                    message = BytesParser().parsebytes(
                        b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + raw
                    )
                    fields, data = {}, None
                    for part in message.get_payload():
                        name = part.get_param("name", header="content-disposition")
                        if part.get_filename():
                            data = part.get_payload(decode=True)
                        else:
                            fields[name] = part.get_payload(decode=True).decode()
                    return {"_multipart": True, "fields": fields, "data": data}
                return json.loads(raw)

            def _release(self, body):
                if not self._authed():
                    return
                if api.release_error:
                    return self._reply(*api.release_error)
                project_id = int(self.path.split("/")[4])
                api.uploads.append((project_id, body["fields"], body["data"]))
                return self._reply(202, {"id": 1, "version": 1, "status": "queued", "done": False, "ok": False})

            def _authed(self):
                if self.headers.get("Authorization") == f"Bearer {api.token}":
                    return True
                self._reply(401, {"error": "unauthorized", "message": "Missing or invalid token."})
                return False

            def do_GET(self):
                api.requests.append(("GET", self.path))
                parsed = urlparse(self.path)
                if parsed.path == "/api/v1/build-config":
                    return self._reply(200, {
                        "path": ".djangocloud/config.json",
                        "defaults": {"wsgi_module": "", "django_settings_module": "", "python_version": "3.13",
                                     "package_manager": "pip", "requirements_file": "requirements.txt", "root": ".",
                                     "port": 8000},
                        "fields": [{"name": "python_version", "choices": ["3.10", "3.11", "3.12", "3.13"]}],
                    })  # fmt: skip
                if parsed.path.startswith("/api/v1/releases/"):
                    if not self._authed():
                        return
                    after = int(parse_qs(parsed.query).get("after", ["0"])[0])
                    step = dict(api.release_script[min(api.release_polls, len(api.release_script) - 1)])
                    api.release_polls += 1
                    logs = [line for line in step.pop("logs") if line["id"] > after]
                    return self._reply(200, {"id": 1, "version": 1, **step, "logs": logs,
                                             "cursor": logs[-1]["id"] if logs else after})  # fmt: skip
                if self.path == "/api/v1/sizes":
                    return self._reply(200, {"sizes": [
                        {"power": "nano", "label": "Nano", "vcpu": 0.25, "ram_gb": 0.5, "price_cents": 1000},
                        {"power": "micro", "label": "Micro", "vcpu": 0.25, "ram_gb": 1, "price_cents": 1500},
                    ]})  # fmt: skip
                if not self._authed():
                    return
                if self.path == "/api/v1/me":
                    return self._reply(200, {"email": "a@example.com", "subscription_active": api.card,
                                             "suspended": False, "aws_connected": api.aws,
                                             "ready_to_deploy": api.card and api.aws})  # fmt: skip
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
                if self.path.startswith("/api/v1/projects/") and self.path.endswith("/releases"):
                    return self._release(body)
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
