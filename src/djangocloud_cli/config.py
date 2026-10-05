"""Where the CLI keeps its API token, and how it finds one.

Order: the DJANGOCLOUD_TOKEN environment variable (CI), then the credentials file written by
`cloud login`. The token is a bearer secret, so the file is created readable by its owner only.
"""

import json
import os
import stat
from pathlib import Path

TOKEN_ENV = "DJANGOCLOUD_TOKEN"
API_ENV = "DJANGOCLOUD_API"
DEFAULT_API = "https://djangocloud.dev/api/v1"


def credentials_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "djangocloud" / "credentials.json"


def api_url() -> str:
    return os.environ.get(API_ENV, DEFAULT_API).rstrip("/")


def load_token() -> str | None:
    token = os.environ.get(TOKEN_ENV)
    if token:
        return token
    try:
        return json.loads(credentials_path().read_text()).get("token")
    except (OSError, ValueError):
        return None


def save_token(token: str) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start so the token is never briefly world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as handle:
        json.dump({"token": token}, handle)
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    return path


def clear_token() -> bool:
    try:
        credentials_path().unlink()
    except FileNotFoundError:
        return False
    return True
