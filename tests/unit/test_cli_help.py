"""Task guidance is available without a login or network access."""

import pytest
from click.testing import CliRunner

from olsync import olsync as cli


@pytest.mark.parametrize(
    ("args", "phrases"),
    [
        (
            [],
            [
                "not a background watcher",
                "does not propagate deletions",
                "default: skip",
                "no automatic text merge",
                "deleting files present only at the destination",
                'onlinelatex -n "My Paper"',
                ".onlinelatex-sync-state.json",
                "before applying --path",
                "not remote downloads",
            ],
        ),
        (
            ["login"],
            [
                "Application/Storage",
                "Cookies",
                "onlatex.sid",
                "session expires",
                "Never share or commit",
                "ONLINELATEX_COOKIE",
            ],
        ),
        (["create"], ["does not upload local files", 'onlinelatex create "My Paper"']),
        (["list"], ["Archived and trashed projects are excluded", "duplicate names"]),
        (
            ["download"],
            [
                "does not upload local changes",
                "main document and compiler",
                "existing file with that name is replaced",
                'onlinelatex -n "My Paper"',
                "mkdir -p build",
            ],
        ),
    ],
)
def test_help_explains_task_without_loading_a_client(monkeypatch, args, phrases):
    def no_client(*args, **kwargs):
        pytest.fail("Help must not load credentials or contact the server")

    monkeypatch.setattr(cli, "load_client", no_client)
    result = CliRunner().invoke(cli.main, [*args, "--help"])
    assert result.exit_code == 0, result.output
    text = " ".join(result.output.split())
    for phrase in phrases:
        assert phrase in text
