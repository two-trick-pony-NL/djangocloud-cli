import os
import runpy

import pytest

from djangocloud_cli import cli, detect, scaffold


@pytest.fixture(autouse=True)
def in_a_temp_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_new_creates_a_stock_project_in_a_folder_of_that_name(tmp_path, capsys):
    assert cli.run(["new", "my-shop"]) == 0
    folder = tmp_path / "my-shop"
    names = sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())
    assert names == [
        ".gitignore", "manage.py", "my_shop/__init__.py", "my_shop/asgi.py", "my_shop/settings.py",
        "my_shop/urls.py", "my_shop/wsgi.py", "requirements.txt",
    ]  # fmt: skip
    out = capsys.readouterr().out
    assert "Created my-shop" in out and "deploy" in out and "runserver" in out


def test_it_uses_the_latest_lts(tmp_path):
    cli.run(["new", "shop"])
    assert (tmp_path / "shop" / "requirements.txt").read_text() == f"Django~={scaffold.DJANGO_LTS}.0\n"
    assert f"using Django {scaffold.DJANGO_LTS}" in (tmp_path / "shop" / "shop" / "settings.py").read_text()
    assert scaffold.DJANGO_LTS == "5.2"


def test_every_python_file_compiles(tmp_path):
    cli.run(["new", "my-shop"])
    for path in (tmp_path / "my-shop").rglob("*.py"):
        compile(path.read_text(), str(path), "exec")


def test_the_settings_are_stock_except_for_the_database(tmp_path):
    cli.run(["new", "shop"])
    text = (tmp_path / "shop" / "shop" / "settings.py").read_text()
    for stock in ("DEBUG = True", "ALLOWED_HOSTS = []", 'ROOT_URLCONF = "shop.urls"', 'STATIC_URL = "static/"',
                  'WSGI_APPLICATION = "shop.wsgi.application"', "django.contrib.admin"):  # fmt: skip
        assert stock in text
    assert "DJANGOCLOUD_HOSTED_DB_NAME" in text and "django.db.backends.sqlite3" in text


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
    assert hosted["NAME"] == "app" and hosted["OPTIONS"] == {"sslmode": "require"}


def test_each_project_gets_its_own_secret_key(tmp_path):
    cli.run(["new", "a"])
    cli.run(["new", "b"])
    keys = [
        runpy.run_path(str(tmp_path / n / n / "settings.py"))["SECRET_KEY"]
        for n in ("a", "b")
    ]  # fmt: skip
    assert keys[0] != keys[1] and all(k.startswith("django-insecure-") for k in keys)


def test_the_deploy_detection_understands_the_new_project(tmp_path):
    cli.run(["new", "my-shop"])
    folder = tmp_path / "my-shop"
    assert detect.asgi_module(folder) == "my_shop.asgi:application"
    assert detect.wsgi_module(folder) == "my_shop.wsgi:application"
    assert detect.settings_module(folder) == "my_shop.settings"
    assert not detect.wsgi_wraps_the_app(folder, "my_shop.wsgi:application")  # so uvicorn is chosen


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


@pytest.mark.parametrize("name", ["test", "django", "os", "1shop", "my shop", "shöp", "class", "a/b", ".."])
def test_names_that_cannot_be_a_python_package_are_refused_clearly(name, capsys, tmp_path):
    assert cli.run(["new", name]) == 1
    assert capsys.readouterr().err.startswith("Error:") and not list(tmp_path.iterdir())
