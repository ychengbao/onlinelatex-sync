"""Server selection: --server, ONLINELATEX_SERVER, saved login, or www.onlinelatex.com."""

from pathlib import Path

import pytest
import responses
from click.testing import CliRunner

from olsync import auth as olauth
from olsync import olsync as cli

FIXTURES = Path(__file__).parent.parent / "fixtures"
OVERLEAF = "https://www.onlinelatex.com"
CE = "http://overleaf.example.test"


class RecordingClient:
    """Fake OverleafClient that records which server it was created for."""

    created = []

    def __init__(self, cookie=None, csrf=None, base_url=OVERLEAF):
        self.cookie, self.csrf, self.base_url = cookie, csrf, base_url
        RecordingClient.created.append(self)

    def all_projects(self):
        return [{"id": "p1", "name": "Thesis", "lastUpdated": "2026-01-02T10:00:00.000Z"}]

    def get_project(self, name):
        return self.all_projects()[0]

    def get_project_infos(self, project_id):
        return {"rootFolder": [{"_id": "root", "docs": [], "fileRefs": [], "folders": []}]}

    def download_project(self, project_id):
        import io
        import zipfile

        buf = io.BytesIO()
        zipfile.ZipFile(buf, "w").close()
        return buf.getvalue()

    def download_pdf(self, project_id):
        return "output.pdf", b"%PDF-1.5"


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    project_dir = tmp_path / "Thesis"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.delenv("ONLINELATEX_SERVER", raising=False)
    monkeypatch.setattr(cli, "OverleafClient", RecordingClient)
    RecordingClient.created = []
    return project_dir


def write_auth(path, server=OVERLEAF):
    olauth.save_auth(path, {"onlatex.sid": "s"}, "c", server)


def server_used():
    return RecordingClient.created[-1].base_url


@pytest.mark.parametrize("args", [["list"], ["-r"], ["download"]], ids=["list", "sync", "download"])
def test_server_stored_at_login_is_used(workdir, args):
    write_auth(workdir / ".olauth-onlinelatex", server=CE)
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    assert server_used() == CE


def test_default_login_uses_onlinelatex(workdir):
    write_auth(workdir / ".olauth-onlinelatex")
    assert CliRunner().invoke(cli.main, ["list"]).exit_code == 0
    assert server_used() == OVERLEAF


@pytest.mark.parametrize(
    "args",
    [["--server", CE, "list"], ["list", "--server", CE], ["--server", CE, "-r"], ["download", "--server", CE]],
    ids=["group-option", "command-option", "sync", "download"],
)
def test_server_option_overrides_the_auth_file(workdir, args):
    write_auth(workdir / ".olauth-onlinelatex", server="http://other.example.test")
    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    assert server_used() == CE


def test_server_from_environment(workdir, monkeypatch):
    write_auth(workdir / ".olauth-onlinelatex")
    monkeypatch.setenv("ONLINELATEX_SERVER", CE)
    assert CliRunner().invoke(cli.main, ["list"]).exit_code == 0
    assert server_used() == CE


def test_trailing_slash_in_server_is_ignored(workdir):
    write_auth(workdir / ".olauth-onlinelatex")
    assert CliRunner().invoke(cli.main, ["list", "--server", CE + "/"]).exit_code == 0
    assert server_used().rstrip("/") == CE


# --- login ------------------------------------------------------------------------------------------------------


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def dashboard(rsps, server):
    rsps.add(
        responses.GET,
        f"{server}/project",
        body=(FIXTURES / "dashboard_2026.html").read_text(),
        content_type="text/html",
    )


@pytest.mark.parametrize(
    "args", [["--server", CE, "login"], ["login", "--server", CE]], ids=["group-option", "command-option"]
)
def test_cookie_login_uses_and_stores_selected_server(rsps, tmp_path, args):
    dashboard(rsps, CE)
    auth = tmp_path / ".olauth-onlinelatex"
    result = CliRunner().invoke(cli.main, [*args, "--auth-file", str(auth)], input="v\n")
    assert result.exit_code == 0, result.output
    assert olauth.load_auth(auth) == {"cookies": {"onlatex.sid": "v"}, "csrf": "csrf-from-page", "server": CE}


def test_cookie_login_defaults_to_onlinelatex(rsps, tmp_path, monkeypatch):
    monkeypatch.delenv("ONLINELATEX_SERVER", raising=False)
    dashboard(rsps, OVERLEAF)
    auth = tmp_path / ".olauth-onlinelatex"
    result = CliRunner().invoke(cli.main, ["login", "--auth-file", str(auth)], input="v\n")
    assert result.exit_code == 0, result.output
    assert olauth.load_auth(auth) == {
        "cookies": {"onlatex.sid": "v"},
        "csrf": "csrf-from-page",
        "server": OVERLEAF,
    }
