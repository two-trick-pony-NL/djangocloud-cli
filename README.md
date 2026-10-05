# djangocloud-cli

Command-line client for DjangoCloud. Standard library only.

```
pip install djangocloud-cli
djangocloud login
djangocloud deploy
djangocloud logs -f
```

Inside a Django project, add `djangocloud_cli` to `INSTALLED_APPS` and use `python manage.py djangocloud <command>`.

**Status:** early. `help`, `login`, `logout`, `whoami`, `link`, `unlink` and the interactive setup in `deploy` work
(they need the DjangoCloud API). Uploading, building and deploying, `logs` and `status` are next.

## First run

```
$ djangocloud deploy
You're not signed in yet.
Open https://djangocloud.dev/dashboard/cli/?code=ABCD-EFGH and check that the code is ABCD-EFGH.
✓ Signed in as you@example.com
? Which project is this?  (arrow keys)
? Server size  Nano  0.25 vCPU, 0.5 GB RAM  $10/month
? This will cost $10 per month. Continue? Yes
✓ Linked to my-shop (.djangocloud/config.json)
```

`.djangocloud/config.json` says which project this folder deploys to. It holds no secrets and is git-ignored by a
`.gitignore` inside the folder. Later runs skip every prompt.

## CI (GitHub Actions)

Create a token under **Command line → Token for CI** in the dashboard and store it as the repository secret
`DJANGOCLOUD_TOKEN`. `--no-input` makes the CLI fail with a clear message instead of ever prompting or opening a browser.

```yaml
- run: pip install djangocloud-cli
- run: djangocloud deploy --no-input --project my-shop
  env:
    DJANGOCLOUD_TOKEN: ${{ secrets.DJANGOCLOUD_TOKEN }}
```

## Configuration

| Variable | Purpose |
|---|---|
| `DJANGOCLOUD_TOKEN` | API token for CI; takes precedence over the stored login |
| `DJANGOCLOUD_NO_INPUT` | Same as `--no-input` (set it to `1`) |
| `DJANGOCLOUD_API` | API base URL (default `https://djangocloud.dev/api/v1`) |

The token from `djangocloud login` is stored in `~/.config/djangocloud/credentials.json` (mode 600).

## Development

```
uv sync
uv run pytest
uv run ruff check
```
