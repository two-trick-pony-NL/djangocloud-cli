import json

import pytest

from djangocloud_cli import cli, config, link
from djangocloud_cli.api import ApiError


class Stub:
    """Stands in for the API client: answers from `routes`, remembers what was sent."""

    def __init__(self, routes):
        self.routes, self.sent = routes, []

    def _answer(self, method, path, body=None):
        self.sent.append((method, path, body))
        answer = self.routes[(method, path.split("?")[0])]
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        if isinstance(answer, ApiError):
            raise answer
        return answer

    def get(self, path):
        return self._answer("GET", path)

    def post(self, path, data=None):
        return self._answer("POST", path, data)

    def put(self, path, data=None):
        return self._answer("PUT", path, data)


@pytest.fixture(autouse=True)
def fast(monkeypatch, api, tmp_path):
    monkeypatch.setattr(cli, "_sleep", lambda s: None)
    config.save_token(api.token)
    link.save(tmp_path, {"id": 1, "slug": "my-shop"}, config.api_url())
    monkeypatch.setattr(cli, "ensure_login", lambda client: None)


def use(monkeypatch, routes):
    stub = Stub(routes)
    monkeypatch.setattr(cli, "make_client", lambda: stub)
    return stub


P = "/projects/1"


# ---- autoscale ----


def test_autoscale_shows_its_state(monkeypatch, capsys):
    use(monkeypatch, {("GET", f"{P}/autoscale"): {"enabled": True, "min": 2, "max": 6, "note": ""}})
    assert cli.run(["autoscale"]) == 0
    assert "2 to 6 instances" in capsys.readouterr().out


def test_autoscale_on_sends_the_range(monkeypatch, capsys):
    saved = {"enabled": True, "min": 2, "max": 6}
    stub = use(monkeypatch, {("PUT", f"{P}/autoscale"): saved})
    assert cli.run(["autoscale", "on", "--min", "2", "--max", "6"]) == 0
    assert stub.sent == [("PUT", f"{P}/autoscale", {"enabled": True, "min": 2, "max": 6})]
    assert "Autoscaling is on" in capsys.readouterr().out


def test_autoscale_on_without_a_range_is_refused_before_asking_the_server(monkeypatch, capsys):
    stub = use(monkeypatch, {})
    assert cli.run(["autoscale", "on"]) == 1
    assert stub.sent == [] and "--min" in capsys.readouterr().err


def test_autoscale_off(monkeypatch, capsys):
    stub = use(monkeypatch, {("PUT", f"{P}/autoscale"): {"enabled": False, "min": 2, "max": 6}})
    assert cli.run(["autoscale", "off"]) == 0
    assert stub.sent[0][2] == {"enabled": False} and "off" in capsys.readouterr().out


# ---- alerts ----


def test_alerts_show_and_set(monkeypatch, capsys):
    current = {"enabled": True, "cpu": 80, "memory": 85, "downtime": False, "firing": True}
    stub = use(monkeypatch, {("GET", f"{P}/alerts"): current, ("PUT", f"{P}/alerts"): {**current, "downtime": True}})
    assert cli.run(["alerts"]) == 0
    assert "80%" in capsys.readouterr().out
    assert cli.run(["alerts", "on", "--cpu", "70", "--downtime"]) == 0
    assert stub.sent[-1][2] == {"cpu": 70, "downtime": True, "enabled": True}
    assert "stops answering" in capsys.readouterr().out


def test_alerts_refusals_are_shown(monkeypatch, capsys):
    refusal = ApiError(400, "invalid_limit", "Pick CPU and memory limits between 1 and 100 percent.")
    use(monkeypatch, {("PUT", f"{P}/alerts"): refusal})
    assert cli.run(["alerts", "on", "--cpu", "500"]) == 1
    assert "between 1 and 100" in capsys.readouterr().err


# ---- metrics ----


def test_metrics_draw_cpu_and_memory(monkeypatch, capsys):
    points = [{"at": "2026-10-09T10:00:00+00:00", "cpu": c, "memory": 40.0} for c in (10.0, 50.0, 90.0)]
    stub = use(
        monkeypatch, {("GET", f"{P}/metrics"): {"power": "nano", "scale": 2, "points": points, "latest": points[-1]}}
    )
    assert cli.run(["metrics", "--since", "6h"]) == 0
    out = capsys.readouterr().out
    assert "now 90%" in out and "peak 90%" in out and "▁" in out and "█" in out
    assert stub.sent[0][1] == f"{P}/metrics?since=6h"


def test_metrics_with_no_samples(monkeypatch, capsys):
    use(monkeypatch, {("GET", f"{P}/metrics"): {"power": "nano", "scale": 1, "points": [], "latest": None}})
    assert cli.run(["metrics"]) == 0
    assert "No samples" in capsys.readouterr().out


def test_sparkline_scales_and_thins():
    assert cli.sparkline([0, 100]) == "▁█"
    assert len(cli.sparkline([50.0] * 500, width=40)) == 40


# ---- db ----

DB = {
    "size": "micro", "ha": False, "ready": True, "removing": False, "state": "available", "public": False,
    "open_until": None, "network_pending": "", "snapshot_pending": False, "endpoint": "db.example:5432",
    "last_snapshot": {"name": "manual-x", "created_at": "2026-10-08T10:00:00+00:00", "state": "available"},
    "latest_restorable": "2026-10-09T10:00:00+00:00",
}  # fmt: skip


def test_db_status(monkeypatch, capsys):
    use(monkeypatch, {("GET", f"{P}/database"): DB})
    assert cli.run(["db", "status"]) == 0
    out = capsys.readouterr().out
    assert "locked" in out and "db.example:5432" in out and "manual-x" in out


def test_db_status_json(monkeypatch, capsys):
    use(monkeypatch, {("GET", f"{P}/database"): DB})
    assert cli.run(["db", "status", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["size"] == "micro"


def test_a_project_without_a_database_is_told_so(monkeypatch, capsys):
    use(monkeypatch, {("GET", f"{P}/database"): ApiError(404, "no_database", "This project has no database.")})
    assert cli.run(["db", "status"]) == 1
    assert "no database" in capsys.readouterr().err


def test_db_public_on_and_off(monkeypatch, capsys):
    stub = use(monkeypatch, {("GET", f"{P}/database"): DB, ("POST", f"{P}/database/network"): {}})
    assert cli.run(["db", "public", "on", "-y"]) == 0
    assert stub.sent[-1] == ("POST", f"{P}/database/network", {"public": True})
    assert cli.run(["db", "public", "off", "-y"]) == 0
    assert stub.sent[-1][2] == {"public": False}
    assert "cuts off" in capsys.readouterr().out


def test_db_public_asks_before_changing_and_cancel_changes_nothing(monkeypatch, capsys):
    stub = use(monkeypatch, {("GET", f"{P}/database"): DB})
    monkeypatch.setattr(cli, "confirm", lambda *a, **k: False)
    assert cli.run(["db", "public", "on"]) == 1
    assert all(call[0] == "GET" for call in stub.sent)


def test_db_public_without_a_state_only_shows_it(monkeypatch, capsys):
    use(monkeypatch, {("GET", f"{P}/database"): {**DB, "public": True, "open_until": "2026-10-09T11:30:00+00:00"}})
    assert cli.run(["db", "public"]) == 0
    assert "open to the internet" in capsys.readouterr().out


def test_db_snapshot_waits_until_it_is_done(monkeypatch, capsys):
    pending, done = (
        {**DB, "snapshot_pending": True},
        {**DB, "last_snapshot": {**DB["last_snapshot"], "name": "manual-new"}},
    )
    use(monkeypatch, {("GET", f"{P}/database"): [DB, pending, done], ("POST", f"{P}/database/snapshot"): {}})
    assert cli.run(["db", "snapshot"]) == 0
    assert "manual-new is ready" in capsys.readouterr().out


def test_db_snapshot_no_wait_returns_at_once(monkeypatch, capsys):
    stub = use(monkeypatch, {("GET", f"{P}/database"): DB, ("POST", f"{P}/database/snapshot"): {}})
    assert cli.run(["db", "snapshot", "--no-wait"]) == 0
    assert [c[0] for c in stub.sent] == ["GET", "POST"] and "queued" in capsys.readouterr().out


def test_a_second_snapshot_is_refused_clearly(monkeypatch, capsys):
    busy = ApiError(409, "snapshot_in_progress", "A snapshot is already being taken. Wait for it to finish.")
    use(monkeypatch, {("GET", f"{P}/database"): DB, ("POST", f"{P}/database/snapshot"): busy})
    assert cli.run(["db", "snapshot"]) == 1
    assert "already being taken" in capsys.readouterr().err
