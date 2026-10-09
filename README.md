<img width="800" alt="og-image" src="https://github.com/user-attachments/assets/061d5725-dfba-49b9-906e-79a3e938dfb7" />


**Deploying a Django app shouldn't be a hassle.**

No Dockerfiles. No load balancers. No server to babysit. You add one package to your project, sign in, and
[DjangoCloud](https://djangocloud.dev) takes care of the rest, running your app on AWS.

```
pip install djangocloud-cli
djangocloud login
djangocloud deploy
```

No settings to change. (Prefer `python manage.py djangocloud ...`? See [Two ways to run it](#two-ways-to-run-it).)

That's the whole idea: a few commands, from the project you already have.

> **Early release.** Sign-in, linking, packaging and uploading work, and the deploy streams its progress back to
> your terminal. Image builds on DjangoCloud's side are still being finished, so a deploy may stop at that step.
> Follow along at [djangocloud.dev](https://djangocloud.dev).

You need a [DjangoCloud](https://djangocloud.dev) account with a card on file to create projects.

## What it looks like

```
$ djangocloud deploy
You're not signed in yet.
Open https://djangocloud.dev/dashboard/cli/?code=ABCD-EFGH and check that the code is ABCD-EFGH.
✓ Signed in as you@example.com
? Which project is this?  Create a new project
? Server size  Nano  0.25 vCPU, 0.5 GB RAM  ~$7/month on AWS
? AWS bills you about $7/month for this server, directly in your own AWS account. Continue? Yes
✓ Linked to my-shop (.djangocloud/config.json)
Deploying my-shop
✓ Wrote build settings to .djangocloud/config.json
  found wsgi_module = config.wsgi:application
✓ Packed 148 files (212 KB). .env and .git are never uploaded.
✓ Uploaded. Release v1 started.
  Building v1
  v1 is live
✓ v1 is live.
```

## Features

Run `djangocloud` on its own for the menu of commands, `djangocloud help` for every command with all its options, and
`djangocloud <command>` without a subcommand (for example `djangocloud db`) for that command's own menu.

### Start a new project

```bash
djangocloud new my-shop
cd my-shop
djangocloud deploy
```

`new` takes only the name. It looks up the latest Django LTS release, runs Django's own `django-admin startproject` for
it (Django is installed just for that, away from your environment, so it needs a network connection), and creates
`my-shop/`. Two things differ from a plain `startproject`:

- **`DATABASES`**: a local SQLite file on your computer, and the Postgres database DjangoCloud creates for you as soon as
  the app runs there. DjangoCloud adds the `DJANGOCLOUD_HOSTED_DB_*` variables to the deployment when you select and
  connect a database, and the settings switch to them.
- **`djangocloud_cli`** is in `INSTALLED_APPS` and `djangocloud-cli` in `requirements.txt`, so
  `python manage.py djangocloud <command>` works too.

- **An empty `.env`** (kept out of git, never uploaded with your code) with a comment on how to use it: add `KEY=value`
  lines and send them with `djangocloud env push .env`.

Everything else a deployed app needs, such as static files and allowed hosts, DjangoCloud adds when it builds the image.
When a new LTS comes out, `new` uses it without a CLI update. Inside an existing project the command is not shown, and
refuses to run.

### Two ways to run it

**`djangocloud <command>`** works as soon as the package is installed. There is nothing to add to your project, and it
still works when your settings won't load.

With [uv](https://docs.astral.sh/uv/) you don't need `manage.py` either. In a project that has `djangocloud-cli` as a
dependency, run `uv run djangocloud <command>`. From anywhere, without installing it:
`uvx --from djangocloud-cli djangocloud <command>`.

**`python manage.py djangocloud <command>`** does the same thing from inside your project, but Django only finds a
management command in an installed app. Add the app first, or you will see `Unknown command: 'djangocloud'`:

```python
INSTALLED_APPS = [
    ...,
    "djangocloud_cli",
]
```

The rest of this page writes `djangocloud`; use whichever form you prefer. Run it with no arguments, or use `help`,
to see every command.

| Command | What it does |
|---|---|
| `setup` | Guided first run: create your account, add a card, choose hosted or your own AWS, connect your AWS keys |
| `login` | Sign in by approving a code in your browser |
| `logout` | Forget the stored token |
| `whoami` | Show who you're signed in as, and which project this folder deploys to |
| `link` | Pick or create the project this folder deploys to |
| `unlink` | Detach this folder from its project |
| `deploy` | Link the folder if needed, pack it, upload it and stream the release until it is live (`--github` deploys the linked repo's latest commit instead) |
| `logs` | Show a project's logs; `-f` follows them, `--source app\|build\|release`, `--since 2h`, `-n 200` |
| `status` | Is it live and answering? Shows the URL, size and latest releases; `--json` for scripts |
| `env push` | Set environment variables from a `.env` file, or from named variables in CI (`--from-env`); `--prune` also removes the rest, `--dry-run` shows what would change |
| `env list` | Show the names of a project's variables (never their values) |
| `rollback` | Go back to an earlier release: `rollback 3`, or pick from a list. `-y` skips the question, `--no-wait` returns once queued |
| `scale` | Change the server size and number of instances: `--size small --instances 3`; `-y` skips the question, `--no-wait` returns once queued |
| `teardown` | Delete a project and what it created in AWS. You type its name to confirm (`--yes` for scripts) |
| `help [command]` | Every command with all of its options, or the help for one command |

### Status and logs

```
$ djangocloud status
My Shop  ● Live
  v2 is live.
  URL       https://my-shop.example.com
  Size      Nano × 1 · eu-central-1
  Checked   3 min ago

  Releases
    v2   active      2 h ago  b3f9c1a  live
    v1   superseded  1 d ago

$ djangocloud logs -f --source app
```

`status` exits with 1 when the server is not responding, so it works as a check in a script. Both commands act on the
project this folder is linked to, or on `--project <slug>`. `logs -f` keeps streaming until you press Ctrl-C.

### Guided setup

`djangocloud setup` (also offered automatically before your first project) walks through it, and picks up where it
stopped if you run it again:

1. **Account.** No account yet? Choose "No, create one": the sign-up page opens in your browser and returns to the
   login approval. Nothing is typed into the terminal.
2. **Card.** Opens Stripe Checkout in your browser and waits until the card is on file.
3. **Hosted or your own AWS.** Hosted means we run it in an AWS environment made for you; there are no keys to hand
   over. Your own AWS asks for an IAM access key (the secret is hidden as you type, and goes to DjangoCloud once, over
   HTTPS), checks it against AWS, and asks for a region. You can print the IAM policy the key needs to see exactly what it is allowed to do.

In scripts: `djangocloud --no-input setup --own-cloud --region eu-west-1` with `AWS_ACCESS_KEY_ID` and
`AWS_SECRET_ACCESS_KEY` in the environment (the keys are never accepted as flags). The card step needs a browser.

### Smooth, guided project creation

Arrow-key menus, clear prices up front and no surprises. Nothing is created until you confirm.

### Your folder remembers its project

`.djangocloud/config.json` records which project a folder deploys to, plus your build settings. It holds no secrets, so
**commit it**: a CI checkout then knows its project and `djangocloud --no-input deploy` needs no flags. After the first
run there are no prompts. (Versions up to 0.1.12 hid the folder with a `.gitignore` inside it; the next run removes that
file if it is the one the CLI wrote, and leaves any other alone.)

### What gets uploaded

The folder is packed into a `.tar.gz` that is the same every time for the same files. In a git repository that is
what git sees (your `.gitignore` is respected); otherwise junk is skipped. Whatever `.gitignore` says, these never go
up: `.env` and `.env.*` (except `.env.example`), `.git`, virtualenvs, `node_modules`, `*.sqlite3`, `*.pem`, `*.key` and
caches. Set your environment variables in the dashboard, not in the upload.

### Build settings

The first deploy detects your setup and writes it to the `"build"` block of `.djangocloud/config.json`. That file is
then the source of truth, so edit it to change how your app is built:

```json
{
  "build": {
    "wsgi_module": "config.wsgi:application",
    "django_settings_module": "config.settings",
    "python_version": "3.13",
    "package_manager": "pip",
    "requirements_file": "requirements.txt",
    "system_packages": ["libpq-dev"],
    "collectstatic": true,
    "release_command": "python manage.py migrate --noinput",
    "port": 8000,
    "workers": 2,
    "healthcheck_path": "/"
  }
}
```

The server checks every setting and lists all problems at once. The full list with defaults is at
`/api/v1/build-config`. If your WSGI app can't be detected in CI, pass `--wsgi-module config.wsgi:application`.

### Autoscaling and usage alerts

```bash
djangocloud autoscale on --min 2 --max 6     # add and remove instances by load (off keeps the range)
djangocloud autoscale                        # show it
djangocloud alerts on --cpu 80 --memory 85 --downtime
djangocloud alerts off
```

Autoscaling is deliberately slow and careful: one instance at a time, out when CPU averages over 70% for 5 minutes
(or memory over 85%), in only after 30 quiet minutes, never outside your range. A scaling change that fails turns it
off and says why. Alerts email you when the 10-minute average of CPU or memory stays over your limits (and, with
`--downtime`, when the server stops answering), at most every 6 hours while it lasts.

### Server load

```
$ djangocloud metrics --since 6h
my-shop  Nano × 2
  CPU    ▁▁▂▂▃▃▅▇█▆▄▃▂▂▁▁▂▃▃▂  now 18%  peak 71%  avg 24%
  Memory ▃▃▃▃▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄  now 41%  peak 44%  avg 40%
```

CPU and memory as a percentage of the server size, from the same samples as the dashboard (every 5 minutes, kept for
30 days). `--json` prints the raw samples for scripts.

### Your database

For a managed database DjangoCloud runs for you:

```bash
djangocloud db status       # state, size, public access, endpoint, last snapshot
djangocloud db public on    # open it to the internet for ONE hour, then it locks again by itself
djangocloud db public off   # lock it now (this cuts off your app too, so it asks first)
djangocloud db snapshot     # take a snapshot and wait until it is ready
```

Public access never stays open, snapshots are kept until you delete them, and the CLI never shows credentials.

### Tests before every deploy

The first interactive deploy looks at your project and, if it finds tests, offers to run them before each deploy. It
works out the command for your setup, so there is nothing to type:

- **pytest** or **Django's own runner** (`manage.py test`), and **pytest-django** when it is installed
- your environment: `uv run`, `poetry run` or `pipenv run` when the project uses them
- speed: `-n auto` with pytest-xdist, `--parallel` with Django's runner
- a dedicated test settings module (`settings_test.py`, `settings/test.py`, ...), passed as `--ds` or `--settings`
- warnings for what usually goes wrong: pytest without pytest-django, tests that need a PostgreSQL or MySQL server

```bash
djangocloud tests            # what is set, and whether the project requires passing tests
djangocloud tests on         # run the tests before every deploy (the command is detected)
djangocloud tests off
djangocloud tests --command "make check"
djangocloud tests --require on   # the project refuses deploys unless their tests passed
djangocloud test             # run the tests now, the way a deploy would
djangocloud deploy --skip-tests
```

Prefer a switch in the browser? **Settings → Tests before deploys** in the dashboard turns the project's requirement
on or off (the same as `tests --require`) and shows the last reported result. Whether *this folder* runs tests is the
`run_tests` setting, which lives with your code.

The settings live in `.djangocloud/config.json` (`run_tests`, `test_command`). The answer to the question is written
there, so you are asked once. Scripts and CI (`--yes`, no terminal) are never asked. A failing test stops the deploy
before anything is uploaded; the result (passed, skipped, how long) is recorded on the release and shows in the
dashboard. It is reported by the CLI, so "require passing tests" guards against mistakes, not against someone who
sends the flag without running anything.

### Keeping the CLI up to date

Every answer from DjangoCloud tells the CLI whether it is current. After a command you may see a **notice** from us
(for example about a change that is coming), a hint that a **newer version** exists, or, when your version is older
than the minimum the service supports, **Upgrade needed**: the command stops before changing anything and says how to
upgrade:

```bash
pip install -U djangocloud-cli
uv tool upgrade djangocloud-cli     # if you installed it with uv
```

### Rolling back

```
$ djangocloud rollback
? Roll back to  v1    superseded  1 d ago    b3f9c1a
my-shop: v2 → v1. Its image and variables come back as a new release; database migrations are not reversed.
? Roll back? Yes
  v3 is live
✓ v3 is live.
```

A rollback redeploys the older release's image, with the environment variables it had then, as a **new** release, so the
history stays a straight line. Nothing is rebuilt, so it is live in a minute or two. Give the number to skip the menu
(`djangocloud rollback 1`); in CI that is required, together with `--no-input`. Releases that failed, or whose image
has been cleaned up, can't be rolled back to, and the server says why. Database migrations are never reversed, so keep
them backwards compatible.

### Environment variables

Your app's settings (secret key, database URL, API keys) live on the project as encrypted environment variables, not
in the upload. Set them in the dashboard, or from the command line:

```
$ djangocloud env push production.env
my-shop: 1 new, 28 to overwrite (values are never shown).
  new: SENTRY_DSN
  overwrite: ALLOWED_HOSTS, DATABASE_URL, DJANGO_SECRET_KEY, ...
✓ Saved 29 variable(s). They apply from the next deploy.
```

- **Nothing is ever printed back.** The output lists names only, and the server never returns a value.
- **Merge by default.** Variables you don't send are left alone. Pass `--prune` to remove everything else, which asks
  first (`-y` skips the question). Variables DjangoCloud sets itself, like `DJANGOCLOUD_HOSTED_DB_*` for a database it
  created, are never removed.
- **All or nothing.** If a line in the file is invalid, nothing is sent, and the error names the line.
- **Try it first** with `--dry-run`: it shows which names are new and which would be overwritten, and changes nothing.
- **Applies from the next deploy.** Run `djangocloud deploy` afterwards.
- `djangocloud env list` shows the names currently set. Use `--project <slug>` on either command to act on a
  project other than the linked one.

The file is read as a `.env` file: comments, `export KEY=value`, single and double quotes and multi-line quoted values
(such as a PEM key) work, and `$VAR` is not expanded. Use `-` to read from standard input.

#### From CI: keep your secrets in one place

In GitHub Actions, secrets aren't files, so name the variables to send. Map each secret to a variable of the same name
and list the names with `--from-env`. Empty values are skipped (and reported by name), so optional secrets can stay
unset:

```yaml
- run: >
    uvx --from djangocloud-cli djangocloud --no-input env push
    --from-env DJANGO_SECRET_KEY DATABASE_URL STRIPE_SECRET_KEY SENTRY_DSN
  env:
    DJANGOCLOUD_TOKEN: ${{ secrets.DJANGOCLOUD_TOKEN }}
    DJANGO_SECRET_KEY: ${{ secrets.DJANGO_SECRET_KEY }}
    DATABASE_URL: ${{ secrets.DATABASE_URL }}
    STRIPE_SECRET_KEY: ${{ secrets.STRIPE_SECRET_KEY }}
    SENTRY_DSN: ${{ secrets.SENTRY_DSN }}
- run: uvx --from djangocloud-cli djangocloud --no-input deploy
  env:
    DJANGOCLOUD_TOKEN: ${{ secrets.DJANGOCLOUD_TOKEN }}
```

Rotating a secret is then: change it in GitHub, re-run the workflow. `env push` needs a DjangoCloud server that
supports it; an older one answers "doesn't support that yet".

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
- run: djangocloud deploy --no-input --project my-shop
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
uv run ruff check          # includes security (bandit), pytest-style, pathlib and simplification rules
uv run ruff format --check
```

Every push to `main` is released automatically: the tests run, the patch version goes up by one (0.1.1 becomes
0.1.2), the package is published to PyPI and a GitHub Release is created. There are no version numbers to edit.

For a bigger bump, tag it yourself before the next push (`git tag v0.2.0 && git push --tags`) and releases continue
from there. Put `[skip release]` in a commit message to skip releasing that commit.

## License

MIT. See [LICENSE](LICENSE).
