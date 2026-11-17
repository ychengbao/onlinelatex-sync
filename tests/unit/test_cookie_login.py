"""Cookie-only OnlineLaTeX login through the hidden prompt or environment."""

from pathlib import Path

import responses
from click.testing import CliRunner

from olsync import auth as olauth
from olsync import olsync as cli

FIXTURES = Path(__file__).parent.parent / "fixtures"
PROJECT_URL = "https://www.onlinelatex.com/project"


def dashboard_ok(rsps):
    rsps.add(responses.GET, PROJECT_URL, body=(FIXTURES / "dashboard_2026.html").read_text(), content_type="text/html")


def test_login_with_cookie_from_hidden_prompt_writes_auth_file(tmp_path):
    with responses.RequestsMock() as rsps:
        dashboard_ok(rsps)
        auth = tmp_path / ".olauth-onlinelatex"
        result = CliRunner().invoke(cli.main, ["login", "--auth-file", str(auth)], input="s3cret\n")
        cookie_header = rsps.calls[0].request.headers["Cookie"]
    assert result.exit_code == 0, result.output
    assert "s3cret" not in result.output
    assert olauth.load_auth(auth) == {
        "cookies": {"onlatex.sid": "s3cret"},
        "csrf": "csrf-from-page",
        "server": "https://www.onlinelatex.com",
    }
    assert cookie_header == "onlatex.sid=s3cret"


def test_login_with_cookie_from_environment(tmp_path, monkeypatch):
    with responses.RequestsMock() as rsps:
        dashboard_ok(rsps)
        monkeypatch.setenv("ONLINELATEX_COOKIE", "from-env")
        auth = tmp_path / ".olauth-onlinelatex"
        result = CliRunner().invoke(cli.main, ["login", "--auth-file", str(auth)])
    assert result.exit_code == 0, result.output
    assert olauth.load_auth(auth)["cookies"] == {"onlatex.sid": "from-env"}


def test_login_with_invalid_cookie_fails_and_writes_nothing(tmp_path):
    with responses.RequestsMock() as rsps:
        rsps.add(responses.GET, PROJECT_URL, status=302, headers={"Location": "/login"})
        auth = tmp_path / ".olauth-onlinelatex"
        result = CliRunner().invoke(cli.main, ["login", "--auth-file", str(auth)], input="expired\n")
    assert result.exit_code != 0
    assert "expired or incomplete" in result.output
    assert not auth.exists()
