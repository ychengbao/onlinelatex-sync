# Releasing

Releases are distributed through GitHub. There is no automatic PyPI publishing workflow.

1. Update `olsync/__init__.py` and the OnlineLaTeX section of `CHANGELOG.md`.
2. Run `pytest`, `ruff check .`, and `ruff format --check .`.
3. Build and validate the distributions:

   ```bash
   python -m pip install build twine
   python -m build
   python -m twine check --strict dist/*
   ```

4. Install the wheel in a fresh virtual environment. Check `onlinelatex --version`, `onlinelatex --help`, and `onlinelatex login --help`.
5. Merge the release changes into `master` after CI passes.
6. Tag that commit as `vX.Y.Z` and create a GitHub release with the corresponding changelog notes. Attach the wheel and source distribution if desired.

Upstream tags, if fetched, describe overleaf-sync releases, not OnlineLaTeX Sync releases.
