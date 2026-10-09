import json

import pytest

from djangocloud_cli import cli, config, link, superuser
from djangocloud_cli.api import ApiError

FAKE_DJANGO = {
    "django/__init__.py": "def setup():\n    pass\n",
    "django/contrib/__init__.py": "",
    "django/contrib/auth/__init__.py": """
class F:
    def __init__(self, label, relation=False):
        self.verbose_name, self.is_relation, self.many_to_many = label, relation, False

class Meta:
    label = "accounts.Member"
    def get_field(self, name):
        return {"email": F("email address"), "first_name": F("first name"), "team": F("team", True)}[name]

class Member:
    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["first_name", "team"]
    _meta = Meta()

def get_user_model():
    return Member
""",
}


def flat(text: str) -> str:
    """Rich wraps long lines at the terminal width; compare without caring where."""
    return " ".join(text.split())


class Stub:
    def __init__(self, fail_delete=False):
        self.sent, self.fail_delete = [], fail_delete

    def put(self, path, data=None):
        self.sent.append(("PUT", path, data))
        return {}

    def delete(self, path, data=None):
        self.sent.append(("DELETE", path, data))
        if self.fail_delete:
            raise ApiError(500, "boom", "the server said no")
        return {"removed": data["names"]}


@pytest.fixture
def project_dir(tmp_path, api):
    (tmp_path / "manage.py").write_text("")
    (tmp_path / "requirements.txt").write_text("django\n")
    link.save(tmp_path, {"id": 1, "slug": "my-shop"}, config.api_url())
    link.save_build(tmp_path, {"wsgi_module": "config.wsgi:application", "requirements_file": "requirements.txt"})
    config.save_token(api.token)
    return tmp_path


def use(monkeypatch, model=None, stub=None):
    stub = stub or Stub()
    monkeypatch.setattr(cli, "make_client", lambda: stub)
    monkeypatch.setattr(cli, "ensure_login", lambda client: None)
    monkeypatch.setattr(superuser, "discover", lambda root, settings="": model or superuser.UserModel())
    return stub


def answers(monkeypatch, texts, passwords=("s3cret", "s3cret"), deploy=False):
    queue, secrets = list(texts), list(passwords)
    monkeypatch.setattr(cli, "text", lambda message, default="": queue.pop(0))
    monkeypatch.setattr(cli, "password", lambda message: secrets.pop(0))
    monkeypatch.setattr(cli, "confirm", lambda message, default=True: deploy)


# ---- which variables ----


def test_variable_names_follow_djangos_convention():
    assert superuser.variable_name("email") == "DJANGO_SUPERUSER_EMAIL"
    assert superuser.variable_name("first_name") == "DJANGO_SUPERUSER_FIRST_NAME"
    assert superuser.PASSWORD_VAR == "DJANGO_SUPERUSER_PASSWORD"


def test_the_project_is_asked_which_fields_its_custom_user_model_needs(tmp_path):
    for name, text in FAKE_DJANGO.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    model = superuser.discover(tmp_path)
    assert not model.guessed and model.label == "accounts.Member"
    assert model.username["name"] == "email"
    assert [f["name"] for f in model.required] == ["first_name", "team"]
    assert [f["name"] for f in model.unsupported] == ["team"]  # a relation can't come from an environment variable


def test_when_the_project_cannot_be_loaded_djangos_defaults_are_assumed_and_flagged(tmp_path):
    model = superuser.discover(tmp_path)  # no Django here (or no settings): the probe fails
    assert model.guessed and model.username["name"] == "username" and [f["name"] for f in model.required] == ["email"]
    assert model.unsupported == []


def test_the_project_environment_is_used_when_there_is_one(tmp_path):
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "bin" / "python").write_text("")
    assert superuser.python_command(tmp_path) == [str(tmp_path / ".venv" / "bin" / "python")]


# ---- djangocloud superuser ----


def test_the_default_user_model_asks_for_username_email_and_a_password(project_dir, monkeypatch, capsys):
    stub = use(monkeypatch)
    answers(monkeypatch, ["admin", "admin@example.com"])
    assert cli.run(["superuser"]) == 0
    ((method, path, body),) = stub.sent
    assert (method, path) == ("PUT", "/projects/1/env") and body["replace"] is False
    assert body["variables"] == {
        "DJANGO_SUPERUSER_USERNAME": "admin",
        "DJANGO_SUPERUSER_EMAIL": "admin@example.com",
        "DJANGO_SUPERUSER_PASSWORD": "s3cret",
    }
    assert link.load_build(project_dir)["create_superuser"] is True
    assert "next deploy" in capsys.readouterr().out


def test_a_custom_user_model_gets_its_own_fields(project_dir, monkeypatch):
    model = superuser.UserModel(
        label="accounts.Member",
        username={"name": "email", "kind": "plain", "label": "email address"},
        required=[{"name": "first_name", "kind": "plain", "label": "first name"}],
    )
    stub = use(monkeypatch, model)
    answers(monkeypatch, ["ada@example.com", "Ada"])
    assert cli.run(["superuser"]) == 0
    assert set(stub.sent[0][2]["variables"]) == {
        "DJANGO_SUPERUSER_EMAIL",
        "DJANGO_SUPERUSER_FIRST_NAME",
        "DJANGO_SUPERUSER_PASSWORD",
    }


def test_a_required_relation_is_explained_and_nothing_is_sent(project_dir, monkeypatch, capsys):
    model = superuser.UserModel(
        label="accounts.Member",
        required=[{"name": "team", "kind": "fk", "label": "team"}],
    )
    stub = use(monkeypatch, model)
    answers(monkeypatch, [])
    assert cli.run(["superuser"]) == 1
    err = flat(capsys.readouterr().err)
    assert "needs team" in err and "createsuperuser" in err and stub.sent == []
    assert "create_superuser" not in link.load_build(project_dir)


def test_mismatching_passwords_three_times_gives_up(project_dir, monkeypatch, capsys):
    stub = use(monkeypatch)
    answers(monkeypatch, ["admin", "a@b.c"], passwords=["a", "b"] * 3)
    assert cli.run(["superuser"]) == 1
    assert "didn't match" in capsys.readouterr().err and stub.sent == []


def test_an_empty_answer_is_refused(project_dir, monkeypatch, capsys):
    stub = use(monkeypatch)
    answers(monkeypatch, ["", "a@b.c"])
    assert cli.run(["superuser"]) == 1
    assert "can't be empty" in capsys.readouterr().err and stub.sent == []


def test_a_guessed_model_is_flagged(project_dir, monkeypatch, capsys):
    use(monkeypatch, superuser.UserModel(guessed=True))
    answers(monkeypatch, ["admin", "a@b.c"])
    cli.run(["superuser"])
    assert "Django's default fields are assumed" in flat(capsys.readouterr().out)


def test_it_offers_to_deploy_right_away(project_dir, monkeypatch):
    use(monkeypatch)
    answers(monkeypatch, ["admin", "a@b.c"], deploy=True)
    ran = []
    monkeypatch.setattr(cli, "run", lambda argv, prog=None: ran.append(argv) or 0)
    assert cli.cmd_superuser(cli.build_parser().parse_args(["superuser"]), project_dir) == 0
    assert ran == [["deploy"]]


# ---- after the deploy is live ----


def test_the_password_is_removed_and_the_setting_switched_off_once_live(project_dir):
    stub = Stub()
    build = {**link.load_build(project_dir), "create_superuser": True}
    link.save_build(project_dir, build)
    cli.finish_superuser(stub, {"id": 1, "slug": "my-shop"}, project_dir, build)
    assert stub.sent == [("DELETE", "/projects/1/env", {"names": ["DJANGO_SUPERUSER_PASSWORD"]})]
    assert link.load_build(project_dir)["create_superuser"] is False


def test_if_the_password_cannot_be_removed_it_says_how_and_keeps_the_setting(project_dir, capsys):
    build = {**link.load_build(project_dir), "create_superuser": True}
    link.save_build(project_dir, build)
    cli.finish_superuser(Stub(fail_delete=True), {"id": 1, "slug": "my-shop"}, project_dir, build)
    assert "env remove DJANGO_SUPERUSER_PASSWORD" in flat(capsys.readouterr().err)
    assert link.load_build(project_dir)["create_superuser"] is True


def test_a_live_deploy_with_the_superuser_setting_cleans_up_afterwards(api, project_dir):
    build = {**link.load_build(project_dir), "create_superuser": True}
    link.save_build(project_dir, build)
    assert cli.run(["deploy", "--name", "Shop", "--size", "nano", "--yes"]) == 0
    assert api.env_removed == [{"names": ["DJANGO_SUPERUSER_PASSWORD"]}]
    assert link.load_build(project_dir)["create_superuser"] is False
    ((_, _, data),) = api.uploads
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        shipped = json.loads(tar.extractfile(".djangocloud/config.json").read())
    assert shipped["build"]["create_superuser"] is True  # what the server saw is what was switched on for the build


def test_a_normal_deploy_removes_nothing(api, project_dir):
    assert cli.run(["deploy", "--name", "Shop", "--size", "nano", "--yes"]) == 0
    assert api.env_removed == []


# ---- env remove ----


def test_env_remove_sends_the_names(project_dir, monkeypatch, capsys):
    stub = use(monkeypatch)
    assert cli.run(["env", "remove", "A", "B"]) == 0
    assert stub.sent == [("DELETE", "/projects/1/env", {"names": ["A", "B"]})]
    assert "Removed A, B" in capsys.readouterr().out
