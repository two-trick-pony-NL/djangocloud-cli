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
        self.can_host = False
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
        self.rollback_requests = []
        self.rollback_error = None  # (status, body) to answer POST /projects/<id>/rollback with
        self.env_existing = []  # names already on the project
        self.env_saved = []  # bodies of PUT /projects/<id>/env
        self.env_error = None  # (status, body) to answer PUT /projects/<id>/env with
        self.plan = "starter"
        self.scale_requests = []
        self.resize_polls = 0
        self.deleted = []
        self.aws_saved = []
        self.setup_step = None  # "card" | "hosting" | "ready"; None answers like a server without guided setup
        self.hosted_open = True
        self.card_polls_until_active = 1
        self.aws_error = None  # (status, body) to answer PUT /aws with
        self.resize_states = ["applying", "idle"]  # what GET /projects/<id> says after a scale request
        self.resize_error = ""
        self.token = "dcl_testtoken"
        self.release_polls = 0
        self.old_server = False  # answer the new endpoints like a server that predates them (an HTML 404)
        release = {"status": "active", "git_sha": "b" * 40, "created_at": "2026-10-07T10:00:00+00:00"}
        older = {"status": "superseded", "git_sha": "", "created_at": "2026-10-06T10:00:00+00:00"}
        self.detail = {
            "id": 1,
            "slug": "my-shop",
            "name": "My Shop",
            "region": "eu-central-1",
            "power": "nano",
            "scale": 1,
            "tier": "connect",
            "url": "https://my-shop.example.test",
            "status": {"label": "Live", "tone": "green", "detail": "v2 is live."},
            "health": {"state": "healthy", "checked_at": None, "since": None, "error": ""},
            "live_release": {"id": 2, "version": 2, "status": "active"},
            "releases": [{"id": 2, "version": 2, **release}, {"id": 1, "version": 1, **older}],
        }
        self.log_requests = []
        self.log_polls = 0
        line = {"at": "2026-10-07T10:00:00+00:00", "source": "app", "level": "info"}
        first = [{"id": 1, **line, "message": "booting"}, {"id": 2, **line, "message": "[red]not markup[/red]"}]
        self.log_script = [
            {"lines": first, "cursor": 2},
            {"lines": [{"id": 3, **line, "level": "error", "message": "it broke"}], "cursor": 3},
            {"lines": [], "cursor": 3},
        ]

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

            def _reply_html_404(self):
                body = b"<h1>Not Found</h1>"
                self.send_response(404)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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
                if parsed.path.startswith("/api/v1/projects/") and parsed.path.split("/")[4:5] != ["", "releases"]:
                    if api.old_server:
                        body = b"<h1>Not Found</h1>"
                        self.send_response(404)
                        self.send_header("Content-Type", "text/html")
                        self.send_header("Content-Length", str(len(body)))
                        self.end_headers()
                        self.wfile.write(body)
                        return
                    if not self._authed():
                        return
                    if parsed.path.endswith("/env"):
                        return self._reply(200, {"variables": [{"key": k, "updated_at": "2026-10-08T10:00:00+00:00"}
                                                               for k in api.env_existing], "max": 200})  # fmt: skip
                    if parsed.path.endswith("/logs"):
                        api.log_requests.append(parse_qs(parsed.query))
                        step = api.log_script[min(api.log_polls, len(api.log_script) - 1)]
                        api.log_polls += 1
                        return self._reply(200, step)
                    detail = dict(api.detail)
                    if api.scale_requests:
                        state = api.resize_states[min(api.resize_polls, len(api.resize_states) - 1)]
                        api.resize_polls += 1
                        done = state == "idle"
                        detail["resize"] = {
                            "status": "failed" if api.resize_error else state,
                            "error": api.resize_error,
                            "power": "",
                            "scale": 0,
                        }
                        if done and not api.resize_error:
                            detail.update(power=api.scale_requests[-1]["power"], scale=api.scale_requests[-1]["scale"])
                    else:
                        detail["resize"] = {"status": "idle", "error": "", "power": "", "scale": 0}
                    return self._reply(200, detail)
                if self.path == "/api/v1/sizes":
                    return self._reply(200, {"sizes": [
                        {"power": "nano", "label": "Nano", "vcpu": 0.25, "ram_gb": 0.5, "price_cents": 1000},
                        {"power": "micro", "label": "Micro", "vcpu": 0.25, "ram_gb": 1, "price_cents": 1500},
                    ]})  # fmt: skip
                if not self._authed():
                    return
                if self.path == "/api/v1/me":
                    extra = {"setup_step": api.setup_step, "plan": api.plan, "hosted_open": api.hosted_open}
                    return self._reply(200, {"email": "a@example.com", "subscription_active": api.card,
                                             "suspended": False, "aws_connected": api.aws,
                                             "can_host": api.can_host,
                                             "ready_to_deploy": api.card and api.aws,
                                             **({k: v for k, v in extra.items() if api.setup_step} or {})})  # fmt: skip
                if self.path == "/api/v1/aws":
                    return self._reply(200, {"connected": api.aws, "region": "eu-west-1" if api.aws else "",
                                             "regions": [{"code": "eu-west-1", "label": "Europe (Ireland)"},
                                                         {"code": "us-east-1", "label": "US East (N. Virginia)"}],
                                             "policy": {"Statement": []}})  # fmt: skip
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
                if self.path == "/api/v1/billing/checkout":
                    return self._reply(200, {"active": False, "url": "https://checkout.example.test/cs_1",
                                             "session_id": "cs_1"})  # fmt: skip
                if self.path == "/api/v1/billing/confirm":
                    if api.card_polls_until_active > 0:
                        api.card_polls_until_active -= 1
                        return self._reply(200, {"subscription_active": False, "suspended": False})
                    api.card, api.setup_step = True, "hosting"
                    return self._reply(200, {"subscription_active": True, "suspended": False})
                if self.path == "/api/v1/setup/hosting":
                    if body["hosting"] == "hosted":
                        if not api.hosted_open:
                            return self._reply(403, {"error": "hosted_not_available", "message": "Not open yet."})
                        api.plan, api.setup_step, api.can_host = "company", "ready", True
                    else:
                        api.plan = "starter"
                    return self._reply(200, {})
                if self.path.endswith("/rollback"):
                    if api.rollback_error:
                        return self._reply(*api.rollback_error)
                    api.rollback_requests.append(body)
                    return self._reply(202, {"id": 9, "version": 3, "status": "queued", "done": False, "ok": False,
                                             "git_sha": "", "created_at": "2026-10-08T10:00:00+00:00"})  # fmt: skip
                if self.path.endswith("/scale"):
                    api.scale_requests.append(body)
                    pending = {"status": "pending", "error": "", "power": "", "scale": 0}
                    return self._reply(202, {"id": 1, "slug": "my-shop", "power": "nano", "scale": 1,
                                             "resize": pending})  # fmt: skip
                if self.path.startswith("/api/v1/projects/") and self.path.endswith("/releases"):
                    return self._release(body)
                if self.path == "/api/v1/projects":
                    if not api.card:
                        return self._reply(402, {"error": "payment_required", "message": "Add a card first."})
                    project = {"id": len(api.projects) + 1, "slug": body["name"].lower().replace(" ", "-"),
                               "name": body["name"], "power": body["power"],
                               "hosted": bool(body.get("hosted"))}  # fmt: skip
                    api.projects.append(project)
                    return self._reply(201, project)
                self._reply(404, {"error": "not_found", "message": "nope"})

            def do_PUT(self):
                body = self._body()
                api.requests.append(("PUT", self.path, body))
                if not self._authed():
                    return
                if self.path == "/api/v1/aws":
                    if api.aws_error:
                        return self._reply(*api.aws_error)
                    api.aws_saved.append(body)
                    api.aws, api.setup_step = True, "ready"
                    return self._reply(200, {"connected": True, "region": body["region"],
                                             "account_id": "123456789012"})  # fmt: skip
                if self.path.endswith("/env"):
                    if api.old_server:
                        return self._reply_html_404()
                    if api.env_error:
                        return self._reply(*api.env_error)
                    api.env_saved.append(body)
                    return self._reply(200, {"created": sorted(body["variables"]), "updated": [], "removed": []})
                self._reply(404, {"error": "not_found", "message": "nope"})

            def do_DELETE(self):
                body = self._body()
                api.requests.append(("DELETE", self.path, body))
                if not self._authed():
                    return
                if self.path.startswith("/api/v1/projects/"):
                    if body.get("confirm") != api.detail["slug"]:
                        return self._reply(400, {"error": "confirmation_required", "message": "Send the slug."})
                    api.deleted.append(self.path)
                    return self._reply(202, {"slug": body["confirm"], "deleted": True})
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
