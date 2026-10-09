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


REAL_LATEST_LTS = scaffold.latest_lts  # the autouse fixture below fakes it for the CLI; these tests need the real one


@pytest.fixture(autouse=True)
def fake_django(tmp_path, monkeypatch):
    """No network and no real Django: PyPI is faked, and `django-admin startproject` writes a stock-looking project."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scaffold, "latest_lts", lambda releases=None: "5.2")
    calls = []

    def generate(series, package, folder):
        calls.append((series, package, folder))
        (folder / package).mkdir(parents=True)
        (folder / "manage.py").write_text(MANAGE.format(name=package))
        (folder / package / "__init__.py").write_text("")
        (folder / package / "settings.py").write_text(STOCK_SETTINGS.format(name=package))
        (folder / package / "wsgi.py").write_text(WSGI)
        (folder / package / "asgi.py").write_text("application = get_asgi_application()\n")

    monkeypatch.setattr(scaffold, "generate", generate)
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


def test_the_requirement_is_this_version_or_newer_and_unpinned_for_a_checkout():
    assert scaffold.cli_requirement("0.1.17") == "djangocloud-cli>=0.1.17"
    assert scaffold.cli_requirement("0.2.3+g1a2b3c") == "djangocloud-cli>=0.2.3"
    assert scaffold.cli_requirement("0.0.0+unknown") == "djangocloud-cli"


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
