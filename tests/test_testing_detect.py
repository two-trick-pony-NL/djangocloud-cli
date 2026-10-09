import pytest

from djangocloud_cli import testing


@pytest.fixture(autouse=True)
def two_cpus(monkeypatch):
    monkeypatch.setattr(testing, "_cpu_count", lambda: 4)


def make(root, files):
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


DJANGO = {"manage.py": "import os\n", "app/tests.py": "from django.test import TestCase\n"}


def test_a_project_without_tests_finds_nothing_and_says_so(tmp_path):
    setup = testing.detect(make(tmp_path, {"manage.py": ""}))
    assert not setup.found and "No tests found" in setup.warnings[0]


def test_djangos_own_runner_is_used_when_there_is_no_pytest(tmp_path):
    setup = testing.detect(make(tmp_path, DJANGO))
    assert setup.runner == "django" and setup.command == "python manage.py test --parallel"
    assert "1 test file" in setup.label and any("--parallel" in r for r in setup.reasons)


def test_djangos_runner_is_not_parallel_on_a_single_cpu(tmp_path, monkeypatch):
    monkeypatch.setattr(testing, "_cpu_count", lambda: 1)
    assert testing.detect(make(tmp_path, DJANGO)).command == "python manage.py test"


def test_pytest_django_with_xdist_runs_in_parallel(tmp_path):
    root = make(tmp_path, {**DJANGO, "requirements.txt": "Django\npytest\npytest-django\npytest-xdist\n"})
    setup = testing.detect(root)
    assert setup.runner == "pytest" and setup.command == "python -m pytest -q -n auto"
    assert "pytest-django" in setup.label and "pytest-xdist" in setup.label and setup.warnings == []


def test_underscores_and_dots_in_package_names_still_match(tmp_path):
    root = make(tmp_path, {**DJANGO, "requirements.txt": "pytest_django==4.8\npytest.xdist\npytest\n"})
    assert "-n auto" in testing.detect(root).command


def test_a_package_that_merely_contains_pytest_in_its_name_is_not_pytest(tmp_path):
    root = make(tmp_path, {**DJANGO, "requirements.txt": "Django\nmy-pytestlike-tool\n"})
    assert testing.detect(root).runner == "django"


def test_pytest_without_pytest_django_warns(tmp_path):
    root = make(tmp_path, {**DJANGO, "requirements.txt": "Django\npytest\n"})
    setup = testing.detect(root)
    assert setup.runner == "pytest" and any("pytest-django" in w for w in setup.warnings)


def test_a_pytest_ini_alone_is_enough_to_choose_pytest(tmp_path):
    root = make(tmp_path, {**DJANGO, "pytest.ini": "[pytest]\nDJANGO_SETTINGS_MODULE = config.settings\n"})
    assert testing.detect(root).runner == "pytest"


def test_pyproject_pytest_settings_are_recognised(tmp_path):
    root = make(tmp_path, {**DJANGO, "pyproject.toml": "[tool.pytest.ini_options]\naddopts = '-q'\n"})
    assert testing.detect(root).runner == "pytest"


def test_uv_projects_run_inside_their_environment(tmp_path):
    files = {**DJANGO, "pyproject.toml": "[project]\ndependencies=['pytest','pytest-django']\n", "uv.lock": ""}
    setup = testing.detect(make(tmp_path, files))
    assert setup.command == "uv run pytest -q" and any("uv" in r for r in setup.reasons)


def test_poetry_and_pipenv_get_their_own_prefix(tmp_path):
    poetry = make(tmp_path / "a", {**DJANGO, "poetry.lock": "", "pyproject.toml": "pytest = '*'"})
    assert testing.detect(poetry).command == "poetry run pytest -q"
    pipenv = make(tmp_path / "b", {**DJANGO, "Pipfile": "[packages]\npytest='*'\n"})
    assert testing.detect(pipenv).command == "pipenv run pytest -q"


def test_the_django_runner_inside_uv_still_calls_python(tmp_path):
    files = {**DJANGO, "pyproject.toml": "[project]\ndependencies=['django']\n", "uv.lock": ""}
    assert testing.detect(make(tmp_path, files)).command == "uv run python manage.py test --parallel"


def test_a_test_settings_module_is_pointed_at(tmp_path):
    files = {**DJANGO, "config/settings.py": "", "config/settings_test.py": "from .settings import *\n"}
    setup = testing.detect(make(tmp_path, files), "config.settings")
    assert setup.command.endswith("--settings=config.settings_test")
    pytest_files = {**files, "requirements.txt": "pytest\npytest-django\n"}
    assert testing.detect(make(tmp_path / "p", pytest_files), "config.settings").command.endswith(
        "--ds=config.settings_test"
    )


def test_pytest_ini_that_already_names_settings_is_left_alone(tmp_path):
    files = {
        **DJANGO,
        "config/settings_test.py": "",
        "pytest.ini": "[pytest]\nDJANGO_SETTINGS_MODULE = config.settings_test\n",
    }
    assert "--ds" not in testing.detect(make(tmp_path, files), "config.settings").command


def test_a_server_database_gets_a_warning_but_sqlite_does_not(tmp_path):
    postgres = make(tmp_path / "a", {**DJANGO, "config/settings.py": "ENGINE = 'django.db.backends.postgresql'\n"})
    assert any("PostgreSQL" in w for w in testing.detect(postgres, "config.settings").warnings)
    mixed = make(tmp_path / "b", {**DJANGO, "config/settings.py": "ENGINE = 'postgresql'  # or sqlite when testing\n"})
    assert testing.detect(mixed, "config.settings").warnings == []


def test_virtualenvs_and_site_packages_do_not_count_as_your_tests(tmp_path):
    files = {"manage.py": "", ".venv/lib/site-packages/foo/tests.py": "", "node_modules/x/test_x.py": ""}
    assert not testing.detect(make(tmp_path, files)).found


def test_tox_is_mentioned_but_not_used(tmp_path):
    setup = testing.detect(make(tmp_path, {**DJANGO, "tox.ini": "[tox]\n"}))
    assert any("tox" in r for r in setup.reasons) and "tox" not in setup.command


def test_tests_without_manage_py_or_pytest_cannot_be_run_automatically(tmp_path):
    setup = testing.detect(make(tmp_path, {"app/tests.py": ""}))
    assert not setup.found and setup.warnings
