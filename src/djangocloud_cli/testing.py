"""Work out how a Django project runs its tests, so the command can be filled in instead of asked for.

Read-only and forgiving: every file is optional, and nothing here runs your code. What it looks at, roughly in the
order it matters:

* the test runner: pytest (with or without pytest-django) or Django's own `manage.py test`
* how the project's environment is started: `uv run`, `poetry run`, `pipenv run`, or the active environment
* speed: pytest-xdist (`-n auto`) or Django's `--parallel`
* a dedicated test settings module (`settings/test.py`, `settings_test.py`, ...), which it points the runner at
* things that will probably bite: pytest without pytest-django, tests that need a real database server
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SKIP_DIRS = {".venv", "venv", "env", "node_modules", ".git", "site-packages", "staticfiles", "__pycache__", ".tox"}
TEST_SETTINGS_NAMES = ("test", "testing", "settings_test", "test_settings", "settings_testing", "ci")


@dataclass
class TestSetup:
    __test__ = False  # not a pytest test class, despite the name

    command: str = ""
    runner: str = ""  # "pytest", "django" or "" when nothing was found
    label: str = ""  # one line for people: "pytest + pytest-django, 14 test files"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def found(self) -> bool:
        return bool(self.command)


def _read(path: Path) -> str:
    try:
        return path.read_text(errors="ignore")
    except OSError:
        return ""


def _dependency_text(root: Path) -> str:
    """Everything that can declare dependencies, lower-cased, with _ and . folded into - like package names."""
    parts = [
        _read(root / name)
        for name in ("pyproject.toml", "setup.cfg", "setup.py", "Pipfile", "tox.ini", "uv.lock", "poetry.lock")
    ]
    for pattern in ("requirements*.txt", "requirements/*.txt", "requirements/*.in", "requirements*.in"):
        parts += [_read(p) for p in sorted(root.glob(pattern))]
    return re.sub(r"[_.]", "-", "\n".join(parts).lower())


def _has(deps: str, package: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(package)}(?![\w-])", deps) is not None


def _files(root: Path, pattern: str, depth: int = 4):
    for path in sorted(root.rglob(pattern)):
        rel = path.relative_to(root)
        if len(rel.parts) <= depth and not (set(rel.parts) & SKIP_DIRS):
            yield rel


def _test_files(root: Path) -> list[Path]:
    found = set(_files(root, "test_*.py")) | set(_files(root, "*_test.py")) | set(_files(root, "tests.py"))
    found |= {p for p in _files(root, "*.py") if p.parent.name == "tests" and p.name != "__init__.py"}
    return sorted(found)


def _pytest_configured(root: Path) -> bool:
    if any((root / name).is_file() for name in ("pytest.ini", ".pytest.ini")):
        return True
    if "[tool.pytest" in _read(root / "pyproject.toml") or "[tool:pytest]" in _read(root / "setup.cfg"):
        return True
    return "[pytest]" in _read(root / "tox.ini")


def _environment_prefix(root: Path) -> tuple[str, str]:
    """(command prefix, reason) so the tests run inside the project's own environment."""
    if (root / "uv.lock").is_file() and (root / "pyproject.toml").is_file():
        return "uv run ", "uv project (uv.lock)"
    if (root / "poetry.lock").is_file():
        return "poetry run ", "Poetry project (poetry.lock)"
    if (root / "Pipfile.lock").is_file() or (root / "Pipfile").is_file():
        return "pipenv run ", "Pipenv project"
    return "", ""


def _test_settings_module(root: Path, settings_module: str) -> str:
    """A settings module meant for tests, next to the real one (config/settings/test.py, config/settings_test.py)."""
    if not settings_module:
        return ""  # without the real settings' location there is no telling which folder to look in
    candidates = []
    base = settings_module.rsplit(".", 1)[0] if "." in settings_module else ""
    folder = root / base.replace(".", "/") if base else root
    for name in TEST_SETTINGS_NAMES:
        candidates += [folder / f"{name}.py", folder / "settings" / f"{name}.py"]
    for path in candidates:
        if path.is_file():
            parts = path.relative_to(root).with_suffix("").parts
            return ".".join(parts)
    return ""


def _needs_database_server(root: Path, settings_module: str) -> str:
    """'postgresql' or 'mysql' when the settings only talk to a server (no sqlite, no DATABASE_URL switch)."""
    if not settings_module:
        return ""
    text = _read(root / (settings_module.replace(".", "/") + ".py")) or _read(
        root / settings_module.replace(".", "/") / "__init__.py"
    )
    if not text or "sqlite" in text.lower():
        return ""
    for engine, name in (("postgres", "PostgreSQL"), ("mysql", "MySQL"), ("mariadb", "MariaDB")):
        if engine in text.lower():
            return name
    return ""


def _cpu_count() -> int:
    import os

    return os.cpu_count() or 1


def detect(root: Path, settings_module: str = "") -> TestSetup:
    """The test command for this project, with the reasons for it and anything the developer should know."""
    files = _test_files(root)
    deps = _dependency_text(root)
    pytest_ini = _pytest_configured(root)
    uses_pytest = _has(deps, "pytest") or pytest_ini or (root / "conftest.py").is_file()
    has_django = (root / "manage.py").is_file()
    setup = TestSetup()

    if not files and not uses_pytest:
        setup.warnings.append("No tests found (no test_*.py, tests.py or tests/ folder).")
        return setup

    prefix, env_reason = _environment_prefix(root)
    if env_reason:
        setup.reasons.append(env_reason + f", so the tests run with '{prefix.strip()}'")

    names = []
    if uses_pytest:
        setup.runner = "pytest"
        names.append("pytest")
        py = "python -m pytest" if not prefix else "pytest"
        command = f"{py} -q"
        if _has(deps, "pytest-xdist"):
            command += " -n auto"
            names.append("pytest-xdist")
            setup.reasons.append("pytest-xdist is installed, so the tests run in parallel (-n auto)")
        if _has(deps, "pytest-django"):
            names.append("pytest-django")
        elif has_django:
            setup.warnings.append(
                "pytest is used but pytest-django is not in your dependencies. Django tests usually need it: "
                "add pytest-django and a DJANGO_SETTINGS_MODULE to your pytest settings."
            )
        if _has(deps, "pytest-env"):
            names.append("pytest-env")
        if _has(deps, "pytest-cov") or _has(deps, "coverage"):
            setup.reasons.append("coverage is installed; add --cov to the command if you want it on every deploy")
        test_settings = _test_settings_module(root, settings_module)
        if test_settings and "DJANGO_SETTINGS_MODULE" not in _read(root / "pytest.ini") + _read(
            root / "pyproject.toml"
        ):
            command += f" --ds={test_settings}"
            setup.reasons.append(f"found test settings ({test_settings}), so --ds points pytest at them")
    elif has_django:
        setup.runner = "django"
        names.append("Django's test runner")
        py = "python manage.py"
        command = f"{py} test"
        if _cpu_count() > 1:
            command += " --parallel"
            setup.reasons.append(
                "Django runs its tests in parallel with --parallel (remove it if your tests share state)"
            )
        test_settings = _test_settings_module(root, settings_module)
        if test_settings:
            command += f" --settings={test_settings}"
            setup.reasons.append(f"found test settings ({test_settings}), so --settings points Django at them")
    else:
        setup.warnings.append("Tests were found, but not manage.py or a pytest setup to run them.")
        return setup

    setup.command = prefix + command
    plural = "s" if len(files) != 1 else ""
    setup.label = f"{' + '.join(names)}, {len(files)} test file{plural}" if files else " + ".join(names)

    if (root / "tox.ini").is_file() or (root / "noxfile.py").is_file():
        setup.reasons.append("tox/nox config found; this runs the tests directly instead (edit test_command to use it)")
    server = _needs_database_server(root, settings_module)
    if server:
        setup.warnings.append(
            f"Your settings use {server}. The tests run on your machine, so a {server} server must be reachable "
            "there, or switch the tests to SQLite (a test settings module, or DATABASE_URL in test_command)."
        )
    return setup
