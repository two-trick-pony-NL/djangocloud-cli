import io
import json
import subprocess
import tarfile

import pytest

from djangocloud_cli import cli, config, detect, link, package
from djangocloud_cli.api import ApiError, Client


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(cli, "_sleep", lambda s: None)


def names(data: bytes) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        return sorted(tar.getnames())


@pytest.fixture
def project_dir(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wsgi.py").write_text(
        "from django.core.wsgi import get_wsgi_application\napplication = get_wsgi_application()\n"
    )
    (tmp_path / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n")
    (tmp_path / "requirements.txt").write_text("django\n")
    (tmp_path / "app.py").write_text("print('hi')\n")
    return tmp_path


# ---------- packaging ----------


def test_secrets_and_junk_never_go_in_the_tarball(project_dir):
    (project_dir / ".env").write_text("SECRET=1\n")
    (project_dir / ".env.production").write_text("SECRET=2\n")
    (project_dir / ".env.example").write_text("SECRET=\n")
    (project_dir / "db.sqlite3").write_text("x")
    (project_dir / "key.pem").write_text("x")
    for folder in (".git", ".venv", "node_modules", "__pycache__"):
        (project_dir / folder).mkdir()
        (project_dir / folder / "x.py").write_text("x")
    (project_dir / "app.pyc").write_text("x")
    data, _ = package.build(project_dir)
    assert names(data) == [".env.example", "app.py", "config/wsgi.py", "manage.py", "requirements.txt"]


def test_gitignore_is_respected_inside_a_git_repo(project_dir):
    (project_dir / ".gitignore").write_text("secret_dir/\n*.log\n")
    (project_dir / "secret_dir").mkdir()
    (project_dir / "secret_dir" / "a.py").write_text("x")
    (project_dir / "debug.log").write_text("x")
    subprocess.run(["git", "init", "-q"], cwd=project_dir, check=True)
    data, _ = package.build(project_dir)
    assert "secret_dir/a.py" not in names(data) and "debug.log" not in names(data) and "app.py" in names(data)


def test_only_our_config_json_goes_up_from_the_djangocloud_folder(project_dir):
    link.save(project_dir, {"id": 3, "slug": "shop"}, "https://x/api/v1")
    link.save_build(project_dir, {"wsgi_module": "config.wsgi:application"})
    (project_dir / ".djangocloud" / "notes.txt").write_text("x")
    listed = names(package.build(project_dir)[0])
    assert ".djangocloud/config.json" in listed and ".djangocloud/.gitignore" not in listed
    assert ".djangocloud/notes.txt" not in listed


def test_the_same_files_always_give_the_same_bytes(project_dir):
    assert package.build(project_dir)[0] == package.build(project_dir)[0]


def test_a_folder_without_manage_py_and_an_oversized_project_are_refused(tmp_path, project_dir):
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "a.txt").write_text("x")
    with pytest.raises(package.PackageError, match=r"manage\.py"):
        package.build(empty)
    (project_dir / "big.bin").write_bytes(__import__("os").urandom(200_000))
    with pytest.raises(package.PackageError, match="limit"):
        package.build(project_dir, max_bytes=1000)


# ---------- detection ----------

SCHEMA = {"fields": [{"name": "python_version", "choices": ["3.10", "3.11", "3.12", "3.13"]}]}


def test_detects_wsgi_settings_and_package_manager(project_dir):
    found, notes = detect.detect(project_dir, SCHEMA)
    assert found == {"wsgi_module": "config.wsgi:application", "django_settings_module": "config.settings"}
    (project_dir / "uv.lock").write_text("")
    (project_dir / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.12"\n')
    found, _ = detect.detect(project_dir, SCHEMA)
    assert found["package_manager"] == "uv" and found["python_version"] == "3.12"


def test_python_version_file_wins_and_unknown_versions_are_ignored(project_dir):
    (project_dir / ".python-version").write_text("3.11.4\n")
    assert detect.python_version(project_dir, ["3.11", "3.12"]) == "3.11"
    (project_dir / ".python-version").write_text("3.9\n")
    assert detect.python_version(project_dir, ["3.11", "3.12"]) == ""


def test_virtualenv_wsgi_files_are_not_mistaken_for_yours(project_dir):
    (project_dir / ".venv" / "lib" / "x").mkdir(parents=True)
    (project_dir / ".venv" / "lib" / "x" / "wsgi.py").write_text("application = 1")
    (project_dir / "config" / "wsgi.py").unlink()
    assert detect.wsgi_module(project_dir) == ""


# ---------- build settings in config.json ----------


def test_saving_the_build_block_keeps_the_link_and_relinking_keeps_the_build(project_dir):
    link.save(project_dir, {"id": 3, "slug": "shop"}, "https://x/api/v1")
    link.save_build(project_dir, {"port": 9000})
    assert link.load(project_dir)["slug"] == "shop" and link.load_build(project_dir) == {"port": 9000}
    link.save(project_dir, {"id": 3, "slug": "shop"}, "https://x/api/v1")
    assert link.load_build(project_dir) == {"port": 9000}
    link.save(project_dir, {"id": 4, "slug": "other"}, "https://x/api/v1")
    assert link.load_build(project_dir) is None  # a different project starts fresh


# ---------- deploy, end to end against the fake API ----------


def deploy(api, *extra):
    config.save_token(api.token)
    return cli.run(["deploy", "--name", "Shop", "--size", "nano", "--yes", *extra])


def test_deploy_uploads_the_project_and_streams_the_release_to_live(api, project_dir, capsys):
    assert deploy(api) == 0
    out = capsys.readouterr().out
    assert "Building v1" in out and "v1 is live" in out and "Wrote build settings" in out
    ((project_id, fields, data),) = api.uploads
    assert project_id == 1 and "config/wsgi.py" in names(data) and ".djangocloud/config.json" in names(data)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        written = json.loads(tar.extractfile(".djangocloud/config.json").read())
    assert written["build"]["wsgi_module"] == "config.wsgi:application"
    assert written["build"]["django_settings_module"] == "config.settings"


def test_a_failed_release_exits_nonzero_and_shows_the_reason(api, project_dir, capsys):
    message = "Release v1 failed: wsgi_module is required"
    failed = {"status": "failed", "done": True, "ok": False, "logs": [{"id": 1, "level": "error", "message": message}]}
    api.release_script = [failed]
    assert deploy(api) == 1
    captured = capsys.readouterr()
    assert "wsgi_module is required" in captured.out and "failed" in captured.err


def test_second_deploy_reuses_your_edited_build_settings(api, project_dir):
    deploy(api)
    link.save_build(project_dir, {**link.load_build(project_dir), "port": 9999})
    api.release_polls = 0
    assert cli.run(["deploy"]) == 0
    assert api.uploads[-1][2] is not None
    with tarfile.open(fileobj=io.BytesIO(api.uploads[-1][2]), mode="r:gz") as tar:
        assert json.loads(tar.extractfile(".djangocloud/config.json").read())["build"]["port"] == 9999


def test_no_card_or_no_aws_is_explained_before_anything_is_packed(api, project_dir, capsys):
    config.save_token(api.token)
    link.save(project_dir, {"id": 1, "slug": "shop"}, config.api_url())
    api.card = False
    assert cli.run(["deploy"]) == 1
    assert "/dashboard/billing/" in capsys.readouterr().err
    api.card, api.aws = True, False
    assert cli.run(["deploy"]) == 1
    assert "/dashboard/aws/" in capsys.readouterr().err
    assert api.uploads == []


def test_server_refusals_show_the_message_and_the_fix_link(api, project_dir, capsys):
    api.release_error = (
        409,
        {"error": "aws_not_connected", "message": "Connect AWS.", "aws_url": "https://x/dashboard/aws/"},
    )
    assert deploy(api) == 1
    err = capsys.readouterr().err
    assert "Connect AWS." in err and "https://x/dashboard/aws/" in err


def test_a_deploy_already_running_is_reported(api, project_dir, capsys):
    api.release_error = (409, {"error": "deploy_in_progress", "message": "A deploy is already running."})
    assert deploy(api) == 1
    assert "already running" in capsys.readouterr().err


def test_missing_dependencies_and_missing_wsgi_are_clear_errors(api, project_dir, capsys):
    (project_dir / "requirements.txt").unlink()
    assert deploy(api) == 1 and "requirements.txt" in capsys.readouterr().err
    (project_dir / "requirements.txt").write_text("django\n")
    (project_dir / "config" / "wsgi.py").unlink()
    link.save(project_dir, {"id": 1, "slug": "shop"}, config.api_url())
    link.remove(project_dir)
    assert cli.run(["--no-input", "deploy", "--name", "Shop", "--size", "nano", "--yes"]) == 1
    assert "wsgi" in capsys.readouterr().err.lower()


def test_github_mode_uploads_no_file(api, project_dir):
    assert deploy(api, "--github") == 0
    ((project_id, fields, data),) = api.uploads
    assert data is None and project_id == 1


def test_ctrl_c_while_watching_says_the_deploy_continues(api, project_dir, capsys, monkeypatch):
    def interrupt(seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_sleep", interrupt)
    assert deploy(api) == 130
    assert "keeps running" in capsys.readouterr().err


def test_poll_survives_a_few_network_blips(api, project_dir, monkeypatch):
    real_get = Client.get
    calls = {"n": 0}

    def flaky(self, path):
        if path.startswith("/releases/"):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ApiError(0, "unreachable", "down")
        return real_get(self, path)

    monkeypatch.setattr(Client, "get", flaky)
    assert deploy(api) == 0


def test_the_size_menu_quotes_the_aws_price_when_the_server_sends_it():
    size = {"label": "Nano", "vcpu": 0.25, "ram_gb": 0.5, "price_cents": 1000, "aws_cents": 700}
    assert "$7" in cli._size_label(size) and "$10" not in cli._size_label(size)
    assert "$10" in cli._size_label({**size, "aws_cents": None} | {"aws_cents": 1000})


# ---- hosted projects (we run it for you) ------------------------------------------------------------------------


def posted_projects(api):
    return [r for r in api.requests if r[0] == "POST" and r[1] == "/api/v1/projects"]


def test_a_hosted_project_is_created_with_hosted_true_and_needs_no_aws_connection(api, project_dir):
    api.can_host, api.aws = True, False  # we host it: no AWS connection of theirs is needed
    assert deploy(api, "--hosted") == 0
    assert posted_projects(api)[0][2]["hosted"] is True


def test_asking_for_hosting_without_the_plan_explains_how_to_get_it_and_creates_nothing(api, project_dir, capsys):
    api.can_host = False
    assert deploy(api, "--hosted") != 0
    assert "Company or Enterprise" in capsys.readouterr().err
    assert posted_projects(api) == []


def test_own_cloud_stays_the_default_and_never_sends_hosted(api, project_dir):
    api.can_host = True
    deploy(api)
    assert "hosted" not in posted_projects(api)[0][2]


def test_a_card_in_good_standing_is_enough_to_deploy_a_hosted_project(api, project_dir):
    api.can_host, api.aws = True, False
    assert deploy(api, "--hosted") == 0  # preflight must not demand an AWS connection for a hosted project
