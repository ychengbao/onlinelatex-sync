# OnlineLaTeX Sync

A command-line client for [OnlineLaTeX](https://www.onlinelatex.com). Sync local LaTeX projects with the website, create projects, and download compiled PDFs without an editor extension.

Forked from [overleaf-sync](https://github.com/moritzgloeckl/overleaf-sync) 2.0.1 by Moritz Glöckl. This is an unofficial client, not affiliated with OnlineLaTeX or Overleaf.

## Install

Requires Python 3.11 or newer. Install from GitHub with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/ychengbao/onlinelatex-sync.git
onlinelatex --version
```

Alternatively, use pipx:

```bash
pipx install git+https://github.com/ychengbao/onlinelatex-sync.git
```

Both keep dependencies isolated. The `onlinelatex` and original `ols` commands can coexist this way; the two packages share the internal `olsync` module name, so use separate environments.

## Login

Run commands from your local paper directory:

```bash
cd my-paper
onlinelatex login
onlinelatex list
```

1. Log in to `https://www.onlinelatex.com` in your browser.
2. Open developer tools → **Application/Storage → Cookies → https://www.onlinelatex.com**.
3. Copy the value of **`onlatex.sid`** and paste it into the terminal prompt. Input is hidden.

The login is saved in `.olauth-onlinelatex` in the current directory. Treat this file and the cookie as credentials: never share or commit them. Run `onlinelatex login` again when the session expires. Use `--auth-file PATH` to keep credentials elsewhere.

## Sync a project

Create an empty remote project, or use the exact name of an existing project:

```bash
onlinelatex create "My Paper"
onlinelatex -n "My Paper"
```

Without `-n`, the remote project name defaults to the local directory name. Rename duplicate project names on the website before syncing.

| Command | Action |
|---|---|
| `onlinelatex -n "My Paper"` | Two-way sync |
| `onlinelatex -l -n "My Paper"` | Upload local changes |
| `onlinelatex -r -n "My Paper"` | Download remote changes |
| `onlinelatex list` | List active projects |
| `onlinelatex download -n "My Paper"` | Compile remotely and download the PDF |

Each sync runs once; it is not a background watcher. Sync before editing to receive collaborators' changes, then sync again to upload yours.

### Conflicts and deletions

- When a file changes on only one side, two-way sync copies the change to the other side.
- When both sides change, or differ on the first sync, choose **local**, **remote**, or **skip**. The default is skip; there is no automatic text merge.
- Two-way sync does not propagate deletions. A file present on only one side is copied to the other.
- One-way modes (`-l` / `-r`) ask before deleting files from the destination. They also ask before overwriting changes not accounted for by the last sync.

The baseline is stored in `.onlinelatex-sync-state.json`. Keep it in the project directory between runs.

### Exclude build outputs

Create `.olignore` in the project directory, for example:

```text
main.pdf
output.pdf
*.aux
*.bbl
*.blg
*.log
*.out
*.fls
*.fdb_latexmk
*.synctex.gz
build/*
```

Use specific output PDF names, not `*.pdf`: figures may be PDFs too. Patterns use Python `fnmatch`; use `build/*`, not `build/`, for a directory. Dotfiles and hidden directories, including credentials and sync state, are excluded automatically.

`.olignore` filters local files. In upload-only mode, remote files outside that local file list may be offered for deletion; read the confirmation before accepting it.

### Download a compiled PDF

```bash
mkdir -p build
onlinelatex download -n "My Paper" --download-path build
```

The website's selected main document and compiler are used. The download command saves the PDF under the name returned by the server, typically `output.pdf`; an existing file with that name is replaced.

## Limitations

- Uses OnlineLaTeX's internal web interfaces, which may change without notice. Login uses `onlatex.sid`; project listing uses `/api/project`.
- Compile time and other account limits remain controlled by the website. This client does not bypass them.
- Sync is file-based, not a Git history or real-time collaborative editor.
- The automated tests use simulated servers. Real-account upload, source download, two-way sync, and PDF compilation/download have also been exercised on OnlineLaTeX.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development and test commands. Report bugs at [GitHub Issues](https://github.com/ychengbao/onlinelatex-sync/issues), with your CLI/Python versions, OS, command, and error output. Remove credentials and private project content before posting.

## License

[MIT](LICENSE). The upstream copyright notice is retained. Upstream release history is available in [CHANGELOG.md](CHANGELOG.md).
