# Repository guidance

This fork targets onlinelatex.com. Keep upstream copyright notices and Git history. Package metadata lives in `pyproject.toml`; the version comes from `olsync/__init__.py`.

## Working conventions

- Use plain functions and the surrounding style. Keep the installed/development import fallbacks in the Python modules.
- Keep server paths POSIX-style, including on Windows.
- For bug fixes, add a failing regression test before changing behavior.
- Update `README.md` and the OnlineLaTeX section of `CHANGELOG.md` for user-visible changes.
- Run `pytest`, `ruff check .`, and `ruff format --check .` before handing off changes. See `CONTRIBUTING.md` for setup and `RELEASING.md` for releases.

## Sync invariants

- Login uses `onlatex.sid`; project listing uses POST `/api/project`.
- Two-way sync never deletes files. Conflicts require a local/remote/skip choice, defaulting to skip.
- Record sync state only for successful transfers. Uploads may be normalized by the server, so the remote baseline is read back after upload.
- `.olignore` filters local files, not the remote archive. Dotfiles are excluded from uploads.
- `--auth-file` is resolved before changing into the sync directory. The default credentials and state files are `.olauth-onlinelatex` and `.onlinelatex-sync-state.json`.

## Credentials and live operations

Automated tests must run without network or a real account. Never commit credentials or paper contents. Live account operations require explicit permission and a designated project; unit tests must not stop or launch user services.
