"""OnlineLaTeX HTTP 429 responses are retried and eventually reported clearly."""

import io
import pytest
import responses
from click.testing import CliRunner

from olsync import auth as olauth
from olsync import olclient
from olsync import olsync as cli
from olsync.olclient import OverleafClient, RateLimitError

BASE = "https://www.onlinelatex.com"


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    waits = []
    monkeypatch.setattr(olclient, "RATE_LIMIT_WAITS", (5, 10, 20))
    monkeypatch.setattr(olclient.time, "sleep", waits.append)
    return waits


@pytest.fixture
def rsps():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


def rate_limited(rsps, method, url, times):
    for _ in range(times):
        rsps.add(method, url, status=429, body="Rate limit reached, please try again later")


def test_project_list_is_retried_after_a_rate_limit(rsps, no_waiting):
    rate_limited(rsps, responses.POST, f"{BASE}/api/project", 2)
    rsps.add(responses.POST, f"{BASE}/api/project", json={"projects": [{"id": "p1", "name": "Thesis"}]})
    assert OverleafClient({}, "c").get_project("Thesis")["id"] == "p1"
    assert no_waiting == [5, 10]


def test_zip_download_is_retried_after_a_rate_limit(rsps, no_waiting):
    rate_limited(rsps, responses.GET, f"{BASE}/project/p1/download/zip", 1)
    rsps.add(responses.GET, f"{BASE}/project/p1/download/zip", body=b"PK")
    assert OverleafClient({}, "c").download_project("p1") == b"PK"
    assert no_waiting == [5]


def test_upload_is_retried_after_a_rate_limit(rsps, no_waiting):
    infos = {"rootFolder": [{"_id": "root", "docs": [], "fileRefs": [], "folders": []}]}
    rate_limited(rsps, responses.POST, f"{BASE}/project/p1/upload", 1)
    rsps.add(responses.POST, f"{BASE}/project/p1/upload", json={"success": True})
    assert OverleafClient({}, "c").upload_file("p1", infos, "a.tex", 1, io.BytesIO(b"a")) is True
    assert len([c for c in rsps.calls if "/upload" in c.request.url]) == 2


def test_persistent_rate_limit_raises_a_clear_error(rsps, no_waiting):
    rate_limited(rsps, responses.GET, f"{BASE}/project/p1/download/zip", 10)
    with pytest.raises(RateLimitError, match="rate limit"):
        OverleafClient({}, "c").download_project("p1")
    assert no_waiting == [5, 10, 20]


def test_cli_explains_a_persistent_rate_limit(rsps, tmp_path, monkeypatch):
    rate_limited(rsps, responses.POST, f"{BASE}/api/project", 10)
    auth = tmp_path / ".olauth-onlinelatex"
    olauth.save_auth(auth, {"onlatex.sid": "s"}, "c", BASE)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli.main, ["list", "--auth-file", str(auth)])
    assert result.exit_code != 0
    assert "rate limit" in result.output.lower()
