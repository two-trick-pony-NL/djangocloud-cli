"""Minimal JSON client for the DjangoCloud API (urllib: no HTTP dependency to conflict with)."""

import json
import urllib.error
import urllib.request

from . import __version__

TIMEOUT = 30


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class Client:
    def __init__(self, base_url: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, data: dict | None = None) -> dict:
        headers = {"Accept": "application/json", "User-Agent": f"djangocloud-cli/{__version__}"}
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except ValueError:
                payload = {}
            raise ApiError(exc.code, payload.get("error", "http_error"), payload.get("message", exc.reason)) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(0, "unreachable", f"Couldn't reach {self.base_url}: {getattr(exc, 'reason', exc)}") from None

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, data: dict | None = None) -> dict:
        return self.request("POST", path, data or {})
