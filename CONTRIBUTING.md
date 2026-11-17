# Contributing

## Development

Requires Python 3.11 or newer.

```bash
git clone https://github.com/ychengbao/onlinelatex-sync.git
cd onlinelatex-sync
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -e ".[dev]"
```

The installed `onlinelatex` command uses your working copy. Use a separate environment from `overleaf-sync`, since both packages contain an `olsync` module.

## Verification

```bash
pytest
ruff check .
ruff format --check .
```

Tests use temporary directories and simulated HTTP responses; they require no account or network. CI runs tests on Linux, macOS, and Windows, and checks package builds. For a bug fix, add a regression test that fails before the fix.

Live testing requires a separate, disposable OnlineLaTeX project. Obtain permission before using someone else's account or project. Do not add credentials or paper contents to tests, commits, screenshots, or issue reports.

## Pull requests

- Keep each change focused and describe the behavior it changes.
- Update the README and the OnlineLaTeX section of the changelog for user-visible changes.
- Preserve upstream attribution and POSIX-style paths in server requests.
- Run the verification commands above and report what passed.

See [RELEASING.md](RELEASING.md) for release preparation.
