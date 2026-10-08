"""Minimal JSON client for the DjangoCloud API (urllib: no HTTP dependency to conflict with)."""

import json
import urllib.error
import urllib.request
import uuid

from . import __version__

TIMEOUT = 30
UPLOAD_TIMEOUT = 300


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, payload: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.payload = payload or {}  # the whole error body: carries hints like billing_url / aws_url


class Client:
    def __init__(self, base_url: str, token: str | None = None):
        if not base_url.lower().startswith(("http://", "https://")):
            raise ValueError(f"The API address must start with http:// or https://, not {base_url!r}.")
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(
        self, method: str, path: str, data: dict | None = None, *, raw: tuple[bytes, str] | None = None
    ) -> dict:
        """JSON in/out. `raw` is (body, content_type) for an upload that isn't JSON."""
        headers = {"Accept": "application/json", "User-Agent": f"djangocloud-cli/{__version__}"}
        body = None
        if raw is not None:
            body, headers["Content-Type"] = raw
        elif data is not None:
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self.base_url + path, data=body, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - scheme checked in __init__
                req, timeout=UPLOAD_TIMEOUT if raw else TIMEOUT
            ) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read())
            except ValueError:
                payload = {}
            raise ApiError(
                exc.code, payload.get("error", "http_error"), payload.get("message", exc.reason), payload
            ) from None
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(0, "unreachable", f"Couldn't reach {self.base_url}: {getattr(exc, 'reason', exc)}") from None

    def get(self, path: str) -> dict:
        return self.request("GET", path)

    def post(self, path: str, data: dict | None = None) -> dict:
        return self.request("POST", path, data or {})

    def put(self, path: str, data: dict | None = None) -> dict:
        return self.request("PUT", path, data or {})

    def delete(self, path: str, data: dict | None = None) -> dict:
        return self.request("DELETE", path, data or {})

    def upload(
        self, path: str, *, fields: dict[str, str], file_field: str, filename: str, content: bytes | None
    ) -> dict:
        """POST multipart/form-data with one file (omit the file with content=None)."""
        boundary = "----djangocloud" + uuid.uuid4().hex
        parts = []
        for name, value in fields.items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        if content is not None:
            head = (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
                "Content-Type: application/gzip\r\n\r\n"
            )
            parts += [head.encode(), content, b"\r\n"]
        parts.append(f"--{boundary}--\r\n".encode())
        return self.request("POST", path, raw=(b"".join(parts), f"multipart/form-data; boundary={boundary}"))
