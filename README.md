# djangocloud-cli
<img width="1200" height="630" alt="og-image" src="https://github.com/user-attachments/assets/061d5725-dfba-49b9-906e-79a3e938dfb7" />


**Deploying a Django app shouldn't be a hassle.**

No Dockerfiles. No load balancers. No server to babysit. You add one package to your project, sign in, and
[DjangoCloud](https://djangocloud.dev) takes care of the rest, running your app on AWS.

```
pip install djangocloud-cli
python manage.py djangocloud login
python manage.py djangocloud deploy
```

That's the whole idea: a few commands, from the project you already have.

> **Early release.** Sign-in, linking a project and the interactive setup work today. The upload-and-deploy step is
> still being built, and `deploy` will say so rather than pretend. Follow along at![Uploading og-image.png…]()

> [djangocloud.dev](https://djangocloud.dev).

## Get started

1. **Install it** in your Django project's environment:

   ```
   pip install djangocloud-cli
   ```

2. **Add it to `INSTALLED_APPS`** in your settings:

   ```python
   INSTALLED_APPS = [
       # ...
       "djangocloud_cli",
   ]
   ```

3. **Sign in.** No password to type into a terminal: you approve a short code in your browser.

   ```
   python manage.py djangocloud login
   ```

4. **Deploy.** The first time, it asks a couple of friendly questions (which project, what size of server) and
   shows the monthly price before anything is created. After that it remembers.

   ```
   python manage.py djangocloud deploy
   ```

You need a [DjangoCloud](https://djangocloud.dev) account with a card on file to create projects.

## What it looks like

```
$ python manage.py djangocloud deploy
You're not signed in yet.
Open https://djangocloud.dev/dashboard/cli/?code=ABCD-EFGH and check that the code is ABCD-EFGH.
✓ Signed in as you@example.com
? Which project is this?  Create a new project
? Server size  Nano  0.25 vCPU, 0.5 GB RAM  $10/month
? This will cost $10 per month. Continue? Yes
✓ Linked to my-shop (.djangocloud/config.json)
```

## Features

### Everything runs through `manage.py`

All commands live under `python manage.py djangocloud`. Run it with no arguments, or use `help`, to see them all.

| Command | What it does |
|---|---|
| `login` | Sign in by approving a code in your browser |
| `logout` | Forget the stored token |
| `whoami` | Show who you're signed in as, and which project this folder deploys to |
| `link` | Pick or create the project this folder deploys to |
| `unlink` | Detach this folder from its project |
| `deploy` | Link the folder if needed, then deploy *(upload and build: coming)* |
| `logs` | Show a project's logs *(coming)* |
| `status` | Show the current release and its state *(coming)* |
| `help [command]` | Help for everything, or for one command |

A standalone `djangocloud` command is installed as well, with the same commands and no `manage.py`. It's handy when
your project's settings won't load.

### Smooth, guided setup

Arrow-key menus, clear prices up front and no surprises. Nothing is created until you confirm.

### Your folder remembers its project

`.djangocloud/config.json` records which project a folder deploys to. It holds no secrets, and a `.gitignore` inside
the folder keeps it out of your repository. After the first run there are no prompts.

### Safe sign-in

Login uses a short code you approve in the browser, so no password or key is ever typed into the terminal. The token
is stored in `~/.config/djangocloud/credentials.json`, readable only by you. You can see and revoke tokens any time
under **Command line** in your dashboard.

### Works in CI, no questions asked

For GitHub Actions and similar, create a token under **Command line → Token for CI** and save it as the repository
secret `DJANGOCLOUD_TOKEN`. `--no-input` makes the CLI never prompt and never open a browser, and fail with a clear
message if something is missing.

```yaml
- run: pip install djangocloud-cli
- run: python manage.py djangocloud deploy --no-input --project my-shop
  env:
    DJANGOCLOUD_TOKEN: ${{ secrets.DJANGOCLOUD_TOKEN }}
```

To create a project from CI, give it everything up front: `--name "My Shop" --size nano`.

### Configuration

| Variable | Purpose |
|---|---|
| `DJANGOCLOUD_TOKEN` | API token for CI. Takes precedence over the stored login |
| `DJANGOCLOUD_NO_INPUT` | Same as `--no-input` (set it to `1`) |
| `DJANGOCLOUD_API` | API base URL (default `https://djangocloud.dev/api/v1`) |

## Requirements

Python 3.10 or newer and Django 4.2 or newer.

## Development

```
uv sync
uv run pytest
uv run ruff check
```

Every push to `main` is released automatically: the tests run, the patch version goes up by one (0.1.1 becomes
0.1.2), the package is published to PyPI and a GitHub Release is created. There are no version numbers to edit.

For a bigger bump, tag it yourself before the next push (`git tag v0.2.0 && git push --tags`) and releases continue
from there. Put `[skip release]` in a commit message to skip releasing that commit.

## License

MIT. See [LICENSE](LICENSE).
