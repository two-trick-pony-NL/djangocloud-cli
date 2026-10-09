import os
import runpy

import pytest

from djangocloud_cli import cli, detect, scaffold

STOCK_SETTINGS = '''"""
Django settings for {name} project.
"""

from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "django-insecure-abc"
DEBUG = True
ALLOWED_HOSTS = []

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.staticfiles",
]

ROOT_URLCONF = "{name}.urls"


# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {{
    "default": {{
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }}
}}


# Password validation

AUTH_PASSWORD_VALIDATORS = []
'''

MANAGE = "os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{name}.settings')\n"
WSGI = "application = get_wsgi_application()\n"


REAL_GENERATE = scaffold.generate
REAL_INSTALL = scaffold.install_dependencies
REAL_LATEST_LTS = scaffold.latest_lts  # the autouse fixture below fakes it for the CLI; these tests need the real one


installs: list = []  # the folders `new` installed requirements into


@pytest.fixture(autouse=True)
def fake_django(tmp_path, monkeypatch):
    """No network and no real Django: PyPI is faked, and `django-admin startproject` writes a stock-looking project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scaffold, "latest_lts", lambda releases=None: "5.2")
    calls = []
    installs.clear()

    def generate(series, package, folder):
        calls.append((series, package, folder))
        if not folder.is_dir():  # what the real django-admin does
            raise scaffold.ScaffoldError(f"CommandError: Destination directory '{folder}' does not exist")
        (folder / package).mkdir()
        (folder / "manage.py").write_text(MANAGE.format(name=package))
        (folder / package / "__init__.py").write_text("")
        (folder / package / "settings.py").write_text(STOCK_SETTINGS.format(name=package))
        (folder / package / "wsgi.py").write_text(WSGI)
        (folder / package / "asgi.py").write_text("application = get_asgi_application()\n")

    monkeypatch.setattr(scaffold, "generate", generate)
    monkeypatch.setattr(scaffold, "install_dependencies", lambda folder: installs.append(folder) or "uv")
    return calls


def test_new_runs_djangos_own_startproject_for_the_latest_lts(tmp_path, capsys, fake_django):
    assert cli.run(["new", "my-shop"]) == 0
    assert fake_django == [("5.2", "my_shop", tmp_path / "my-shop")]
    out = capsys.readouterr().out
    assert "Django 5.2 (LTS)" in out and "Created my-shop" in out and "runserver" in out and "deploy" in out
    requirements = (tmp_path / "my-shop" / "requirements.txt").read_text().splitlines()
    assert requirements[0] == "Django~=5.2.0" and requirements[1].startswith("djangocloud-cli")
    assert "db.sqlite3" in (tmp_path / "my-shop" / ".gitignore").read_text()


def test_the_project_gets_a_short_readme_with_the_commands_that_matter(tmp_path):
    cli.run(["new", "my-shop"])
    readme = (tmp_path / "my-shop" / "README.md").read_text()
    assert readme.startswith("# my-shop") and "Django 5.2 (LTS)" in readme
    assert "https://docs.djangocloud.dev/" in readme and "gitbook" not in readme
    for text in ("python manage.py runserver", "djangocloud deploy", "djangocloud env push .env",
                 "djangocloud tests on", "my_shop/settings.py", "djangocloud logs -f"):  # fmt: skip
        assert text in readme


def test_an_empty_env_file_explains_how_to_send_it(tmp_path):
    from djangocloud_cli import envfile

    cli.run(["new", "shop"])
    env = (tmp_path / "shop" / ".env").read_text()
    assert "djangocloud env push .env" in env
    assert all(line.startswith("#") or not line.strip() for line in env.splitlines())  # comments only
    assert ".env" in (tmp_path / "shop" / ".gitignore").read_text().splitlines()  # kept out of git
    assert envfile.parse(env) == ({}, [])  # `env push .env` on it sends nothing


def test_only_the_database_block_and_our_app_differ_from_djangos_settings(tmp_path):
    cli.run(["new", "shop"])
    text = (tmp_path / "shop" / "shop" / "settings.py").read_text()
    expected = (
        STOCK_SETTINGS.format(name="shop")
        .replace("from pathlib import Path", "import os\nfrom pathlib import Path")
        .replace('    "django.contrib.staticfiles",\n]', '    "django.contrib.staticfiles",\n    "djangocloud_cli",\n]')
    )
    start = expected.index("DATABASES = {")
    end = expected.index("\n\n\n# Password validation")
    assert text == expected[:start] + scaffold.DATABASES_BLOCK.rstrip("\n") + expected[end:]
    for stock in ("DEBUG = True", "ALLOWED_HOSTS = []", 'ROOT_URLCONF = "shop.urls"'):
        assert stock in text


def test_locally_it_is_sqlite_and_on_djangocloud_it_switches_to_the_postgres_database(tmp_path, monkeypatch):
    cli.run(["new", "shop"])
    settings = tmp_path / "shop" / "shop" / "settings.py"
    for key in [k for k in os.environ if k.startswith("DJANGOCLOUD_HOSTED_DB_")]:
        monkeypatch.delenv(key)
    local = runpy.run_path(str(settings))["DATABASES"]["default"]
    assert local["ENGINE"].endswith("sqlite3") and str(local["NAME"]).endswith("db.sqlite3")
    for key, value in {"NAME": "app", "USER": "u", "PASSWORD": "p", "HOST": "db.example", "PORT": "5432"}.items():
        monkeypatch.setenv(f"DJANGOCLOUD_HOSTED_DB_{key}", value)
    hosted = runpy.run_path(str(settings))["DATABASES"]["default"]
    assert hosted["ENGINE"].endswith("postgresql") and hosted["HOST"] == "db.example"
    assert hosted["NAME"] == "app" and hosted["OPTIONS"] == {"sslmode": "require"} and hosted["CONN_MAX_AGE"] == 600


def test_the_deploy_detection_understands_the_new_project(tmp_path):
    cli.run(["new", "my-shop"])
    folder = tmp_path / "my-shop"
    assert detect.asgi_module(folder) == "my_shop.asgi:application"
    assert detect.wsgi_module(folder) == "my_shop.wsgi:application"
    assert detect.settings_module(folder) == "my_shop.settings"


def test_settings_that_no_longer_look_like_djangos_are_never_half_patched(tmp_path, capsys):
    with pytest.raises(scaffold.ScaffoldError, match="different DATABASES block"):
        scaffold.patch_settings("from pathlib import Path\nBASE_DIR = Path('.')\nDATABASES = dict()\n")


def test_patching_twice_is_harmless_on_the_import(tmp_path):
    once = scaffold.patch_settings(STOCK_SETTINGS.format(name="x"))
    assert once.count("import os\n") == 1
    assert once.startswith('"""') and "\nimport os\nfrom pathlib import Path\n" in once


# ---- our app in INSTALLED_APPS, and in the requirements ----


def test_the_app_is_added_to_installed_apps_once_and_at_the_end():
    once = scaffold.add_installed_app(STOCK_SETTINGS.format(name="x"))
    assert once.count('"djangocloud_cli"') == 1
    assert '    "django.contrib.staticfiles",\n    "djangocloud_cli",\n]\n' in once
    assert scaffold.add_installed_app(once) == once  # already there: unchanged


def test_settings_without_the_usual_installed_apps_list_are_left_alone():
    assert scaffold.add_installed_app("INSTALLED_APPS = some_function()\n") == "INSTALLED_APPS = some_function()\n"


def test_manage_py_djangocloud_works_in_the_new_project(tmp_path):
    cli.run(["new", "shop"])
    apps = runpy.run_path(str(tmp_path / "shop" / "shop" / "settings.py"))["INSTALLED_APPS"]
    assert apps[-1] == "djangocloud_cli"


def test_the_cli_requirement_is_never_pinned_to_a_just_released_version(tmp_path):
    assert scaffold.cli_requirement() == "djangocloud-cli"
    cli.run(["new", "shop"])
    lines = (tmp_path / "shop" / "requirements.txt").read_text().splitlines()
    assert lines == ["Django~=5.2.0", "djangocloud-cli"]  # no ">=0.1.x" that PyPI's mirrors might not have yet


# ---- which LTS ----


def release(*versions):
    return {v: [{"yanked": False}] for v in versions}


def test_the_latest_lts_is_the_highest_x_dot_2_series():
    assert REAL_LATEST_LTS(release("4.2.9", "5.0.1", "5.1.4", "5.2.0", "5.2.7", "5.3.0")) == "5.2"
    assert REAL_LATEST_LTS(release("5.2.7", "6.2.1", "6.0.3", "6.1.0")) == "6.2"


def test_prereleases_and_yanked_releases_do_not_count():
    releases = {**release("5.2.7", "6.2rc1", "6.2b1"), "6.2.0": [{"yanked": True}], "7.2.0": []}
    assert REAL_LATEST_LTS(releases) == "5.2"


def test_no_lts_at_all_is_an_error():
    with pytest.raises(scaffold.ScaffoldError):
        REAL_LATEST_LTS(release("5.1.0"))


def test_the_requirement_follows_the_series():
    assert scaffold.requirement("6.2") == "Django~=6.2.0"


# ---- names and folders ----


def test_an_existing_folder_with_files_is_never_touched(tmp_path, capsys):
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "mine.txt").write_text("keep me")
    assert cli.run(["new", "shop"]) == 1
    assert "already exists" in capsys.readouterr().err
    assert (tmp_path / "shop" / "mine.txt").read_text() == "keep me"
    assert not (tmp_path / "shop" / "manage.py").exists()


def test_an_empty_existing_folder_is_fine(tmp_path):
    (tmp_path / "shop").mkdir()
    assert cli.run(["new", "shop"]) == 0
    assert (tmp_path / "shop" / "manage.py").is_file()


@pytest.mark.parametrize("name", ["test", "django", "os", "json", "1shop", "my shop", "shöp", "class", "a/b", ".."])
def test_names_that_cannot_be_a_python_package_are_refused_clearly(name, capsys, tmp_path, fake_django):
    assert cli.run(["new", name]) == 1
    assert capsys.readouterr().err.startswith("Error:") and fake_django == [] and not list(tmp_path.iterdir())


def test_a_failing_django_run_is_a_clear_error_and_leaves_nothing_behind(tmp_path, monkeypatch, capsys):
    def broken(series, package, folder):
        raise scaffold.ScaffoldError("Couldn't create the Django 5.2 project: no matching distribution")

    monkeypatch.setattr(scaffold, "generate", broken)
    assert cli.run(["new", "shop"]) == 1
    assert "no matching distribution" in capsys.readouterr().err and not (tmp_path / "shop").exists()


# ---- not inside a project ----


def test_inside_a_project_new_is_hidden_from_the_help_and_refuses(tmp_path, capsys):
    (tmp_path / "manage.py").write_text("")
    assert cli.run([]) == 0
    menu = capsys.readouterr().out
    assert "deploy" in menu and "Create a new Django project" not in menu
    assert cli.run(["help"]) == 0
    assert "Create a new Django project" not in capsys.readouterr().out
    assert cli.run(["new", "other"]) == 1
    assert "already inside a Django project" in capsys.readouterr().err
    assert not (tmp_path / "other").exists()


def test_inside_a_linked_folder_it_is_hidden_too(tmp_path, capsys):
    (tmp_path / ".djangocloud").mkdir()
    (tmp_path / ".djangocloud" / "config.json").write_text("{}")
    assert cli.run(["new", "other"]) == 1
    assert "already inside" in capsys.readouterr().err


def test_outside_a_project_new_is_listed(tmp_path, capsys):
    assert cli.run([]) == 0
    assert "Create a new Django project" in capsys.readouterr().out


def test_the_folder_is_created_in_the_folder_we_are_in_before_django_runs(tmp_path, fake_django):
    sub = tmp_path / "work"
    sub.mkdir()
    os.chdir(sub)
    assert cli.run(["new", "fresh"]) == 0
    assert (sub / "fresh" / "manage.py").is_file() and not (tmp_path / "fresh").exists()
    assert fake_django[-1][2] == sub / "fresh"


def test_django_runs_from_the_folder_we_are_in(tmp_path, monkeypatch):
    seen = {}

    def run(command, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(scaffold.subprocess, "run", run)
    monkeypatch.setattr(scaffold.shutil, "which", lambda name: "/usr/bin/uvx")
    monkeypatch.setattr(scaffold, "generate", REAL_GENERATE)
    (tmp_path / "x").mkdir()
    REAL_GENERATE("5.2", "x", tmp_path / "x")
    assert seen["cwd"] == tmp_path


def test_a_failure_after_django_ran_removes_the_folder_it_made(tmp_path, monkeypatch, capsys):
    def odd(series, package, folder):
        (folder / package).mkdir()
        (folder / package / "settings.py").write_text("DATABASES = dict()\n")  # not Django's shape

    monkeypatch.setattr(scaffold, "generate", odd)
    assert cli.run(["new", "shop"]) == 1
    assert "different DATABASES block" in capsys.readouterr().err and not (tmp_path / "shop").exists()


def test_a_failure_in_an_existing_empty_folder_empties_it_again(tmp_path, monkeypatch):
    (tmp_path / "shop").mkdir()

    def odd(series, package, folder):
        (folder / package).mkdir()
        (folder / package / "settings.py").write_text("DATABASES = dict()\n")

    monkeypatch.setattr(scaffold, "generate", odd)
    assert cli.run(["new", "shop"]) == 1
    assert (tmp_path / "shop").is_dir() and not list((tmp_path / "shop").iterdir())


# ---- installing Django, so the project runs ----


def test_new_installs_django_into_the_projects_venv_by_default(tmp_path, capsys):
    assert cli.run(["new", "shop"]) == 0
    assert installs == [tmp_path / "shop"]
    out = capsys.readouterr().out
    assert "Installed Django 5.2" in out and "source .venv/bin/activate" in out


def test_no_install_skips_it_and_says_how_to_do_it_later(tmp_path, capsys):
    assert cli.run(["new", "shop", "--no-install"]) == 0
    assert installs == []
    assert "uv pip install -r requirements.txt" in capsys.readouterr().out


def test_a_failed_install_keeps_the_project_and_says_what_to_do(tmp_path, monkeypatch, capsys):
    def broken(folder):
        raise scaffold.ScaffoldError("Couldn't install the project's requirements: no network")

    monkeypatch.setattr(scaffold, "install_dependencies", broken)
    assert cli.run(["new", "shop"]) == 0  # the project exists and works; only the install is missing
    out = capsys.readouterr().out
    assert "no network" in out and "uv pip install -r requirements.txt" in out
    assert (tmp_path / "shop" / "manage.py").is_file()


def record_runs(monkeypatch):
    runs = []
    monkeypatch.setattr(scaffold.subprocess, "run", lambda command, **kw: runs.append((command, kw["cwd"])))
    return runs


def test_with_uv_it_makes_a_venv_and_installs_into_it(tmp_path, monkeypatch):
    runs = record_runs(monkeypatch)
    monkeypatch.setattr(scaffold.shutil, "which", lambda name: "/usr/bin/uv")
    assert REAL_INSTALL(tmp_path) == "uv"
    assert runs == [
        (["uv", "venv", "--quiet"], tmp_path),
        (["uv", "pip", "install", "--quiet", "-r", "requirements.txt"], tmp_path),
    ]


def test_without_uv_it_uses_the_python_running_the_cli(tmp_path, monkeypatch):
    runs = record_runs(monkeypatch)
    monkeypatch.setattr(scaffold.shutil, "which", lambda name: None)
    assert REAL_INSTALL(tmp_path) == "pip"
    assert runs[0][0][1:] == ["-m", "venv", ".venv"]
    assert runs[1][0][1:4] == ["-m", "pip", "install"] and runs[1][0][-2:] == ["-r", "requirements.txt"]
    assert all(cwd == tmp_path for _, cwd in runs)


# ---- asking for the name ----


def answers(monkeypatch, *names):
    queue = list(names)
    monkeypatch.setattr(cli, "interactive", lambda: True)
    asked = []
    monkeypatch.setattr(cli, "text", lambda message, default="": asked.append(message) or queue.pop(0))
    return asked


def test_without_a_name_it_asks_for_one(tmp_path, monkeypatch, capsys):
    asked = answers(monkeypatch, "  my-shop ")
    assert cli.run(["new"]) == 0
    assert len(asked) == 1 and "Project name" in asked[0]
    assert (tmp_path / "my-shop" / "manage.py").is_file()
    assert "Created my-shop" in capsys.readouterr().out


def test_a_name_on_the_command_line_is_not_asked_for_again(tmp_path, monkeypatch):
    asked = answers(monkeypatch)
    assert cli.run(["new", "shop"]) == 0
    assert asked == []


def test_a_bad_name_is_explained_and_asked_for_again(tmp_path, monkeypatch, capsys):
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "mine.txt").write_text("x")
    answers(monkeypatch, "django", "taken", "good-name")
    assert cli.run(["new"]) == 0
    out = capsys.readouterr().out
    assert "clashes" in out and "already exists" in out
    assert (tmp_path / "good-name" / "manage.py").is_file() and (tmp_path / "taken" / "mine.txt").exists()


def test_three_bad_names_in_a_row_give_up(tmp_path, monkeypatch, capsys):
    answers(monkeypatch, "django", "test", "os")
    assert cli.run(["new"]) == 1
    assert "No usable project name" in capsys.readouterr().err and not list(tmp_path.iterdir())


def test_without_a_name_and_without_a_terminal_it_says_what_to_type(tmp_path, capsys):
    assert cli.run(["new"]) == 1
    assert "Give the project a name" in capsys.readouterr().err and not list(tmp_path.iterdir())


def test_inside_a_project_it_refuses_before_asking(tmp_path, monkeypatch, capsys):
    (tmp_path / "manage.py").write_text("")
    asked = answers(monkeypatch)
    assert cli.run(["new"]) == 1
    assert asked == [] and "already inside a Django project" in capsys.readouterr().err
