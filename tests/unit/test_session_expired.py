"""An expired or invalid session gives a clear "log in again" message instead of "Project could not be queried"."""

import pytest
import responses
from click.testing import CliRunner

from olsync import auth as olauth
from olsync import olclient
from olsync import olsync as cli
from olsync.olclient import OverleafClient, SessionExpiredError

BASE = "https://www.onlinelatex.com"


@pytest.fixture
def expired(monkeypatch):
    monkeypatch.setattr(olclient, "RATE_LIMIT_WAITS", ())
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        rsps.add(responses.POST, f"{BASE}/api/project", status=401)
        yield rsps


@pytest.mark.parametrize("call", [lambda c: c.all_projects(), lambda c: c.get_project("Thesis")])
def test_client_raises_session_expired(expired, call):
    with pytest.raises(SessionExpiredError, match="onlinelatex login"):
        call(OverleafClient({"onlatex.sid": "old"}, "c"))


@pytest.mark.parametrize("args", [["list"], ["-r"], ["download"]], ids=["list", "sync", "download"])
def test_cli_explains_an_expired_session(expired, tmp_path, monkeypatch, args):
    project_dir = tmp_path / "Thesis"
    project_dir.mkdir()
    olauth.save_auth(project_dir / ".olauth-onlinelatex", {"onlatex.sid": "old"}, "c", BASE)
    monkeypatch.chdir(project_dir)
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code != 0
    assert "login expired" in result.output and "onlinelatex login" in result.output
