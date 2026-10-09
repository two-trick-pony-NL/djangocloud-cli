import sys

import pytest

from djangocloud_cli import cli, config, link


@pytest.fixture
def project_dir(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "wsgi.py").write_text("application = None\n")
    (tmp_path / "manage.py").write_text("os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n")
    (tmp_path / "requirements.txt").write_text("django\n")
    return tmp_path


def deploy(api, *extra):
    config.save_token(api.token)
    return cli.run(["deploy", "--name", "Shop", "--size", "nano", "--yes", *extra])


def set_build(project_dir, **settings):
    link.save(project_dir, {"id": 1, "slug": "my-shop"}, config.api_url())
    build = {"wsgi_module": "config.wsgi:application", "requirements_file": "requirements.txt", **settings}
    link.save_build(project_dir, build)


PASS = f"{sys.executable} -c 'print(\"all good\")'"
FAIL = f"{sys.executable} -c 'import sys; print(\"boom\"); sys.exit(3)'"


def uploaded_fields(api):
    ((_, fields, _),) = api.uploads
    return fields


# ---- djangocloud test ----


def test_the_test_command_runs_and_its_output_is_shown(project_dir, capsys, api):
    set_build(project_dir, test_command=PASS)
    assert cli.run(["test"]) == 0
    out = capsys.readouterr().out
    assert "all good" in out and "Tests passed" in out


def test_a_failing_test_command_exits_nonzero(project_dir, capsys, api):
    set_build(project_dir, test_command=FAIL)
    assert cli.run(["test"]) == 1
    assert "exit 3" in capsys.readouterr().out


def test_extra_arguments_reach_the_command(project_dir, capsys, api):
    set_build(project_dir, test_command=f"{sys.executable} -c 'import sys; print(sys.argv[1:])'")
    assert cli.run(["test", "--", "-k", "login"]) == 0
    assert "['-k', 'login']" in capsys.readouterr().out


# ---- deploy ----


def test_a_failing_test_stops_the_deploy_before_anything_is_uploaded(api, project_dir, capsys):
    set_build(project_dir, run_tests=True, test_command=FAIL)
    assert deploy(api) == 1
    assert api.uploads == [] and "nothing was deployed" in capsys.readouterr().err


def test_passing_tests_let_the_deploy_go_on_and_are_reported_to_the_server(api, project_dir, capsys):
    set_build(project_dir, run_tests=True, test_command=PASS)
    assert deploy(api) == 0
    fields = uploaded_fields(api)
    assert fields["tests"] == "passed" and fields["tests_command"] == PASS and fields["tests_seconds"].isdigit()
    assert "Tests passed" in capsys.readouterr().out


def test_tests_are_not_run_when_off_and_the_server_is_told_none(api, project_dir):
    set_build(project_dir, run_tests=False, test_command=FAIL)  # would fail if it ran
    assert deploy(api) == 0
    assert uploaded_fields(api)["tests"] == "none"


def test_skip_tests_deploys_anyway_and_says_skipped(api, project_dir, capsys):
    set_build(project_dir, run_tests=True, test_command=FAIL)
    assert deploy(api, "--skip-tests") == 0
    assert uploaded_fields(api)["tests"] == "skipped"
    assert "Skipping the tests" in capsys.readouterr().out


def test_a_deploy_is_never_asked_about_tests_in_a_script(api, project_dir):
    set_build(project_dir, test_command=FAIL)  # no run_tests key at all
    assert deploy(api) == 0  # --yes and no terminal: not asked, not run
    assert "run_tests" not in link.load_build(project_dir)
    assert uploaded_fields(api)["tests"] == "none"


# ---- asking once ----


def interactive_answer(monkeypatch, answer):
    monkeypatch.setattr(cli, "interactive", lambda: True)
    asked = []
    monkeypatch.setattr(cli, "confirm", lambda message, default=True: asked.append(message) or answer)
    return asked


def pytest_project(project_dir):
    (project_dir / "requirements.txt").write_text("django\npytest\npytest-django\n")
    (project_dir / "tests").mkdir()
    (project_dir / "tests" / "test_a.py").write_text("def test_a(): pass\n")


def test_it_asks_once_writes_the_answer_and_never_asks_again(project_dir, monkeypatch):
    pytest_project(project_dir)
    link.save(project_dir, {"id": 1, "slug": "s"}, "https://x/api/v1")  # creates .djangocloud/
    asked = interactive_answer(monkeypatch, True)
    build = {"wsgi_module": "config.wsgi:application"}
    result = cli.ask_about_tests(project_dir, build)
    assert result["run_tests"] is True and "pytest" in result["test_command"] and len(asked) == 1
    assert link.load_build(project_dir)["run_tests"] is True  # written, so the next deploy does not ask
    cli.ask_about_tests(project_dir, result)
    assert len(asked) == 1  # the key is there now


def test_a_no_is_remembered_too(project_dir, monkeypatch):
    pytest_project(project_dir)
    link.save(project_dir, {"id": 1, "slug": "s"}, "https://x/api/v1")
    interactive_answer(monkeypatch, False)
    assert cli.ask_about_tests(project_dir, {"wsgi_module": "x:y"})["run_tests"] is False


def test_nothing_is_asked_when_there_are_no_tests(project_dir, monkeypatch):
    asked = interactive_answer(monkeypatch, True)
    assert "run_tests" not in cli.ask_about_tests(project_dir, {"wsgi_module": "x:y"}) and asked == []


# ---- djangocloud tests ----


def test_tests_on_detects_the_command_and_off_turns_it_back(project_dir, api, capsys):
    pytest_project(project_dir)
    set_build(project_dir)
    assert cli.run(["tests", "on"]) == 0
    build = link.load_build(project_dir)
    assert build["run_tests"] is True and build["test_command"].startswith("python -m pytest")
    assert cli.run(["tests", "off"]) == 0
    assert link.load_build(project_dir)["run_tests"] is False


def test_tests_command_sets_it_by_hand(project_dir, api):
    set_build(project_dir)
    assert cli.run(["tests", "--command", "make check"]) == 0
    assert link.load_build(project_dir)["test_command"] == "make check"


def test_require_sets_the_project_policy_and_warns_when_this_folder_would_be_refused(project_dir, api, capsys):
    config.save_token(api.token)
    set_build(project_dir, run_tests=False)
    api.tests_policy = {"require": False, "last": None}
    assert cli.run(["tests", "--require", "on"]) == 0
    assert api.tests_policy["require"] is True
    assert "will be refused" in capsys.readouterr().out


def test_a_refused_deploy_explains_how_to_fix_it(api, project_dir, capsys):
    set_build(project_dir, run_tests=False)
    api.release_error = (
        422,
        {"error": "tests_required", "message": "This project only accepts deploys whose tests passed."},
    )
    assert deploy(api) == 1
    assert "only accepts deploys whose tests passed" in capsys.readouterr().err
