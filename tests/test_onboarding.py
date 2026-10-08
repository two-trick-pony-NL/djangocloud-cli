"""The guided setup (account, card, hosted or your own AWS, AWS keys), `scale` and `teardown`."""

import pytest

from djangocloud_cli import auth, cli, config, link, onboarding


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(cli, "_sleep", lambda s: None)
    monkeypatch.setattr(onboarding, "_sleep", lambda s: None)
    monkeypatch.setattr(onboarding, "_open", lambda url: opened.append(url))
    opened.clear()
    for name in (onboarding.AWS_KEY_ENV, onboarding.AWS_SECRET_ENV):
        monkeypatch.delenv(name, raising=False)


opened: list[str] = []


@pytest.fixture
def terminal(monkeypatch):
    """Pretend someone is at the keyboard; `answers` are consumed in order by every prompt."""

    class Answers(list):
        asked: list[str]

    answers = Answers()
    asked: list[str] = []

    def answer(message, *args, **kwargs):
        asked.append(message)
        return answers.pop(0)

    for module in (cli, onboarding):
        monkeypatch.setattr(module, "interactive", lambda: True)
    for name in ("select", "text", "confirm"):
        for module in (cli, onboarding):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, answer)
    monkeypatch.setattr(onboarding, "password", answer)
    answers.asked = asked
    return answers


@pytest.fixture
def signed_in(api):
    config.save_token(api.token)
    return api


# ---------- setup ----------


def test_a_new_account_gets_a_card_then_hosting_then_aws_keys(signed_in, terminal, capsys):
    api = signed_in
    api.card, api.aws, api.setup_step, api.hosted_open = False, False, "card", False
    terminal.extend(["AKIAABCDEFGHIJKLMNOP", "s" * 40, "eu-west-1"])  # no hosting question while it's closed
    terminal.insert(0, False)  # "Show the IAM policy here?"
    assert cli.run(["setup"]) == 0
    out = capsys.readouterr().out
    assert opened == ["https://checkout.example.test/cs_1"]
    assert "Card added" in out and "Hosting isn't open yet" in out and "Connected AWS account 123456789012" in out
    assert [r[1] for r in api.requests if r[0] == "POST" and "billing" in r[1]] == [
        "/api/v1/billing/checkout",
        "/api/v1/billing/confirm",
        "/api/v1/billing/confirm",
    ]
    assert api.aws_saved == [
        {"access_key_id": "AKIAABCDEFGHIJKLMNOP", "secret_access_key": "s" * 40, "region": "eu-west-1"}
    ]
    assert "All set" in out and "your own AWS account" in out


def test_choosing_hosted_asks_for_no_keys(signed_in, terminal, capsys):
    api = signed_in
    api.setup_step = "hosting"
    terminal.append(True)  # hosted
    assert cli.run(["setup"]) == 0
    assert api.aws_saved == [] and api.plan == "company"
    assert "hosted by us" in capsys.readouterr().out
    assert terminal.asked == ["Where should your apps run?"]


def test_choosing_your_own_aws_asks_for_the_keys_and_never_echoes_the_secret(signed_in, terminal, capsys):
    api = signed_in
    api.setup_step = "hosting"
    terminal.extend([False, False, "AKIAABCDEFGHIJKLMNOP", "topsecret" * 5, "us-east-1"])
    assert cli.run(["setup"]) == 0
    assert api.aws_saved[0]["region"] == "us-east-1"
    captured = capsys.readouterr()
    assert "topsecret" not in captured.out + captured.err


def test_keys_in_the_environment_can_be_used_instead_of_typing(signed_in, terminal, monkeypatch):
    api = signed_in
    api.setup_step = "hosting"
    monkeypatch.setenv(onboarding.AWS_KEY_ENV, "AKIAENVENVENVENVENV1")
    monkeypatch.setenv(onboarding.AWS_SECRET_ENV, "e" * 40)
    terminal.extend([False, False, True, "eu-west-1"])  # own cloud, no policy, use env keys, region
    assert cli.run(["setup"]) == 0
    assert api.aws_saved[0]["access_key_id"] == "AKIAENVENVENVENVENV1"


def test_aws_rejecting_the_keys_is_a_clear_error_and_nothing_is_marked_done(signed_in, terminal, capsys):
    api = signed_in
    api.setup_step = "hosting"
    api.aws_error = (400, {"error": "invalid_aws_credentials", "message": "AWS rejected these keys."})
    terminal.extend([False, False, "AKIAABCDEFGHIJKLMNOP", "s" * 40, "eu-west-1"])
    assert cli.run(["setup"]) == 1
    assert "AWS rejected these keys." in capsys.readouterr().err


def test_a_finished_account_is_just_told_so(signed_in, terminal, capsys):
    signed_in.setup_step = "ready"
    assert cli.run(["setup"]) == 0
    assert "All set" in capsys.readouterr().out and terminal.asked == []


def test_hosted_flag_skips_the_question_and_a_closed_hosted_is_explained(signed_in, terminal, capsys):
    api = signed_in
    api.setup_step, api.hosted_open = "hosting", False
    assert cli.run(["setup", "--hosted"]) == 1
    assert "Not open yet" in capsys.readouterr().err


def test_without_a_terminal_setup_needs_flags_and_env_keys(signed_in, capsys, monkeypatch):
    api = signed_in
    api.setup_step = "hosting"
    assert cli.run(["--no-input", "setup"]) == 1
    assert "--hosted or --own-cloud" in capsys.readouterr().err
    assert cli.run(["--no-input", "setup", "--own-cloud"]) == 1
    assert onboarding.AWS_KEY_ENV in capsys.readouterr().err
    monkeypatch.setenv(onboarding.AWS_KEY_ENV, "AKIAABCDEFGHIJKLMNOP")
    monkeypatch.setenv(onboarding.AWS_SECRET_ENV, "s" * 40)
    assert cli.run(["--no-input", "setup", "--own-cloud", "--region", "eu-west-1"]) == 0
    assert api.aws_saved[0]["region"] == "eu-west-1"


def test_without_a_terminal_a_missing_card_points_at_billing(signed_in, capsys):
    signed_in.setup_step, signed_in.card = "card", False
    assert cli.run(["--no-input", "setup"]) == 1
    assert "/dashboard/billing/" in capsys.readouterr().err


def test_an_older_server_without_guided_setup_says_so(signed_in, capsys):
    assert cli.run(["setup"]) == 1
    assert "doesn't support guided setup" in capsys.readouterr().err


def test_waiting_for_the_card_gives_up_politely(signed_in, terminal, capsys, monkeypatch):
    signed_in.setup_step, signed_in.card, signed_in.card_polls_until_active = "card", False, 10**6
    monkeypatch.setattr(onboarding, "CARD_TIMEOUT", -1)
    assert cli.run(["setup"]) == 1
    assert "Didn't see a card" in capsys.readouterr().err


# ---------- account creation ----------


def test_signing_up_opens_the_signup_page_that_returns_to_the_approval(api, terminal, capsys):
    api.setup_step = "ready"
    terminal.append(True)  # "No, create one"
    assert cli.run(["setup"]) == 0
    out = capsys.readouterr().out
    assert "Create your account at" in out
    assert "https://example.test/signup/?next=%2Fdashboard%2Fcli%2F%3Fcode%3DABCD-EFGH" in out.replace("\n", "")


def test_signup_url_keeps_the_code_through_the_redirect():
    url = auth.signup_url("https://djangocloud.dev/dashboard/cli/?code=ABCD-EFGH")
    assert url == "https://djangocloud.dev/signup/?next=%2Fdashboard%2Fcli%2F%3Fcode%3DABCD-EFGH"


def test_deploying_for_the_first_time_finishes_setup_before_creating_a_project(signed_in, terminal, tmp_path):
    api = signed_in
    api.setup_step, api.card, api.aws = "hosting", True, False
    terminal.extend([False, False, "AKIAABCDEFGHIJKLMNOP", "s" * 40, "eu-west-1"])
    assert cli.run(["link", "--name", "Shop", "--size", "nano", "--yes"]) == 0
    assert api.aws_saved and api.projects[0]["slug"] == "shop"


# ---------- scale ----------


@pytest.fixture
def linked(signed_in, tmp_path):
    link.save(tmp_path, {"id": 1, "slug": "my-shop"}, config.api_url())
    return signed_in


def test_scale_changes_size_and_instances_and_waits_for_it(linked, capsys):
    api = linked
    assert cli.run(["scale", "--size", "micro", "--instances", "3", "--yes"]) == 0
    assert api.scale_requests == [{"power": "micro", "scale": 3}]
    assert "now runs Micro × 3" in capsys.readouterr().out  # noqa: RUF001


def test_scale_instances_only_keeps_the_size(linked):
    assert cli.run(["scale", "--instances", "2", "-y"]) == 0
    assert linked.scale_requests == [{"power": "nano", "scale": 2}]


def test_scale_to_what_it_already_is_does_nothing(linked, capsys):
    assert cli.run(["scale", "--size", "nano", "--instances", "1", "-y"]) == 0
    assert linked.scale_requests == [] and "already Nano" in capsys.readouterr().out


def test_scale_validates_before_calling_the_server(linked, capsys):
    assert cli.run(["scale", "--size", "huge", "-y"]) == 1
    assert "Unknown size" in capsys.readouterr().err
    assert cli.run(["scale", "--instances", "99", "-y"]) == 1
    assert "between 1 and 20" in capsys.readouterr().err
    assert linked.scale_requests == []


def test_scale_prompts_when_given_no_flags_and_asks_before_applying(linked, terminal, capsys):
    terminal.extend(["micro", "2", True])
    assert cli.run(["scale"]) == 0
    assert linked.scale_requests == [{"power": "micro", "scale": 2}]
    assert terminal.asked[-1] == "Apply this change?"


def test_declining_the_confirmation_changes_nothing(linked, terminal, capsys):
    terminal.append(False)
    assert cli.run(["scale", "--instances", "2"]) == 1
    assert linked.scale_requests == [] and "Nothing was changed" in capsys.readouterr().err


def test_a_failed_resize_exits_nonzero_with_the_reason(linked, capsys):
    linked.resize_error = "AWS refused the new size."
    assert cli.run(["scale", "--instances", "2", "-y"]) == 1
    assert "AWS refused the new size." in capsys.readouterr().err


def test_no_wait_returns_right_away(linked, capsys):
    assert cli.run(["scale", "--instances", "2", "-y", "--no-wait"]) == 0
    assert linked.resize_polls == 0 and "Queued" in capsys.readouterr().out


def test_scale_billing_problem_shows_the_fix_link(linked, capsys, monkeypatch):
    def refuse(self, path, data=None):
        raise cli.ApiError(402, "payment_required", "No card on file yet.", {"billing_url": "https://x/billing"})

    monkeypatch.setattr(cli.Client, "post", refuse)
    assert cli.run(["scale", "--instances", "2", "-y"]) == 1
    assert "https://x/billing" in capsys.readouterr().err


# ---------- teardown ----------


def test_teardown_with_yes_deletes_and_unlinks_the_folder(linked, tmp_path, capsys):
    assert cli.run(["teardown", "--yes"]) == 0
    assert linked.deleted == ["/api/v1/projects/1"]
    assert linked.requests[-1][2] == {"confirm": "my-shop"}
    assert link.load(tmp_path) is None
    assert "being removed in the background" in capsys.readouterr().out


def test_teardown_makes_you_type_the_project_name(linked, terminal, tmp_path, capsys):
    terminal.append("nope")
    assert cli.run(["teardown"]) == 1
    assert linked.deleted == [] and link.load(tmp_path) is not None
    terminal.append("my-shop")
    assert cli.run(["teardown"]) == 0
    assert linked.deleted


def test_teardown_in_ci_needs_an_explicit_yes(linked, capsys):
    assert cli.run(["--no-input", "teardown"]) == 1
    assert "--yes" in capsys.readouterr().err and linked.deleted == []


def test_teardown_by_slug_keeps_another_folders_link(api, tmp_path, capsys):
    config.save_token(api.token)
    api.projects.append({"id": 1, "slug": "my-shop", "name": "My Shop", "power": "nano"})
    link.save(tmp_path, {"id": 7, "slug": "other"}, config.api_url())
    assert cli.run(["teardown", "--project", "my-shop", "--yes"]) == 0
    assert link.load(tmp_path)["slug"] == "other"
