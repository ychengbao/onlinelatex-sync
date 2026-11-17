"""Project listing through OnlineLaTeX's JSON API."""

import pytest
import responses

from olsync.olclient import AmbiguousProjectError, OverleafClient

PROJECT_URL = "https://www.onlinelatex.com/api/project"


@pytest.fixture
def projects_api():
    with responses.RequestsMock() as rsps:
        yield lambda projects: rsps.add(
            responses.POST, PROJECT_URL, json={"totalSize": len(projects), "projects": projects}
        )


def test_filter_projects_drops_archived_and_trashed():
    projects = [{"name": "a"}, {"name": "b", "archived": True}, {"name": "c", "trashed": True}]
    assert [p["name"] for p in OverleafClient.filter_projects(projects)] == ["a"]


def test_filter_projects_matches_extra_attributes():
    projects = [{"name": "a"}, {"name": "b"}]
    assert [p["name"] for p in OverleafClient.filter_projects(projects, {"name": "b"})] == ["b"]


def test_all_projects_uses_json_api(projects_api):
    projects_api(
        [
            {"id": "p1", "name": "Thesis"},
            {"id": "p2", "name": "Paper"},
            {"id": "p3", "name": "Archived", "archived": True},
        ]
    )
    client = OverleafClient({"onlatex.sid": "session"}, "csrf")
    assert sorted(p["name"] for p in client.all_projects()) == ["Paper", "Thesis"]


def test_project_api_request_shape():
    with responses.RequestsMock() as rsps:
        rsps.add(responses.POST, PROJECT_URL, json={"totalSize": 0, "projects": []})
        OverleafClient({"onlatex.sid": "session"}, "csrf-token").all_projects()
        request = rsps.calls[0].request
    assert request.headers["X-Csrf-Token"] == "csrf-token"
    assert request.headers["Cookie"] == "onlatex.sid=session"
    assert request.body == b'{"sort": "lastUpdated"}'


def test_get_project(projects_api):
    projects_api([{"id": "p1", "name": "Thesis"}, {"id": "p2", "name": "Paper"}])
    assert OverleafClient({}, "csrf").get_project("Thesis")["id"] == "p1"


def test_get_project_unknown_name_returns_none(projects_api):
    projects_api([{"id": "p1", "name": "Thesis"}])
    assert OverleafClient({}, "csrf").get_project("Nope") is None


def test_duplicate_active_project_names_are_rejected(projects_api):
    projects_api([{"id": "p1", "name": "Paper"}, {"id": "p2", "name": "Paper"}])
    with pytest.raises(AmbiguousProjectError, match="Multiple active projects"):
        OverleafClient({}, "csrf").get_project("Paper")


def test_inactive_duplicates_do_not_make_a_project_ambiguous(projects_api):
    projects_api(
        [
            {"id": "p1", "name": "Paper"},
            {"id": "p2", "name": "Paper", "archived": True},
            {"id": "p3", "name": "Paper", "trashed": True},
        ]
    )
    assert OverleafClient({}, "csrf").get_project("Paper")["id"] == "p1"


def test_cli_explains_ambiguous_project_names(projects_api):
    import click

    from olsync import olsync as cli

    projects_api([{"id": "p1", "name": "Paper"}, {"id": "p2", "name": "Paper"}])
    client = OverleafClient({}, "csrf")
    with pytest.raises(click.ClickException, match="Multiple active projects"):
        cli.execute_action(lambda: client.get_project("Paper"), "Querying", "Done", "Query failed")
