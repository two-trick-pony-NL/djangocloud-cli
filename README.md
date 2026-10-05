# djangocloud-cli

Command-line client for DjangoCloud. Standard library only.

```
pip install djangocloud-cli
cloud login
cloud deploy
cloud logs -f
```

Inside a Django project, add `djangocloud_cli` to `INSTALLED_APPS` and use `python manage.py cloud <command>`.

**Status:** early skeleton. `help`, `logout` and `whoami` work; `login`, `deploy`, `logs` and `status` wait on the
DjangoCloud API.

## Configuration

| Variable | Purpose |
|---|---|
| `DJANGOCLOUD_TOKEN` | API token for CI; takes precedence over the stored login |
| `DJANGOCLOUD_API` | API base URL (default `https://djangocloud.dev/api/v1`) |

The token from `cloud login` is stored in `~/.config/djangocloud/credentials.json` (mode 600).

## Development

```
uv sync
uv run pytest
uv run ruff check
```
