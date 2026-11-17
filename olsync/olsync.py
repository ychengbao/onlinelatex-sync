"""OnlineLaTeX command-line sync."""
##################################################
# MIT License
##################################################
# File: olsync.py
# Description: Overleaf Two-Way Sync
# Author: Moritz Glöckl
# License: MIT
##################################################

import click
from requests import HTTPError
from datetime import datetime
import os
import shutil
import sys
import uuid
from yaspin import yaspin
import zipfile
import io
import glob
import fnmatch
import traceback
from pathlib import Path

try:
    # Import for pip installation / wheel
    from olsync.olclient import BASE_URL, AmbiguousProjectError, OverleafClient, RateLimitError, SessionExpiredError
    from olsync import auth, syncstate
except ImportError:
    # Import for development
    from olclient import BASE_URL, AmbiguousProjectError, OverleafClient, RateLimitError, SessionExpiredError
    import auth
    import syncstate

SESSION_COOKIES = ["onlatex.sid"]

server_option = click.option(
    "--server",
    "server",
    default=None,
    envvar="ONLINELATEX_SERVER",
    metavar="URL",
    help="OnlineLaTeX server URL (default: https://www.onlinelatex.com). Can also be set with ONLINELATEX_SERVER.",
)

DEFAULT_AUTH_FILE = ".olauth-onlinelatex"


def auth_file_options(legacy_name):
    """
    --auth-file, plus the option name used before 2.0 (--store-path, or --path for login) as a hidden, deprecated alias
    """

    def decorator(f):
        f = click.option(legacy_name, "legacy_auth_file", default=None, hidden=True, type=click.Path(exists=False))(f)
        return click.option(
            "--auth-file",
            "auth_file",
            default=None,
            type=click.Path(exists=False),
            metavar="PATH",
            help=f"Login credentials (default: {DEFAULT_AUTH_FILE}). Relative paths use the directory where "
            "the command is started, before applying --path.",
        )(f)

    return decorator


def resolve_auth_file(ctx, auth_file, legacy_auth_file, legacy_name):
    """--auth-file of the command, else of the group (`onlinelatex --auth-file X list`), else the default"""
    root = ctx.find_root()
    candidates = [(auth_file, legacy_auth_file, legacy_name)]
    if root is not ctx:
        candidates.append((root.params.get("auth_file"), root.params.get("legacy_auth_file"), "--store-path"))
    for new, legacy, name in candidates:
        if legacy:
            click.echo(f"Warning: {name} is deprecated, use --auth-file instead.", err=True)
        if new or legacy:
            return new or legacy
    return DEFAULT_AUTH_FILE


def configure_console():
    """
    Windows consoles often default to a legacy codepage (e.g. cp1252) that can't
    encode the emoji/braille characters used for status output and the spinner,
    which crashes stdout.write() with UnicodeEncodeError. Force UTF-8 output.
    Done when the CLI starts rather than on import, so importing olsync has no side effects.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


@click.group(invoke_without_command=True)
@click.option(
    "-l",
    "--local-only",
    "local",
    is_flag=True,
    help="Upload local files; ask before deleting remote-only files.",
)
@click.option(
    "-r",
    "--remote-only",
    "remote",
    is_flag=True,
    help="Download remote source files; ask before deleting local-only files (not a PDF download).",
)
@click.option(
    "-n",
    "--name",
    "project_name",
    default="",
    help="OnlineLaTeX project name (default: the local directory name).",
)
@auth_file_options("--store-path")
@click.option(
    "-p", "--path", "sync_path", default=".", type=click.Path(exists=True), help="Path of the project to sync."
)
@click.option(
    "-i",
    "--olignore",
    "olignore_path",
    default=".olignore",
    type=click.Path(exists=False),
    help="Path to the .olignore file (relative to the sync path): fnmatch patterns of local files that are never "
    "uploaded, e.g. `*.log` or `out/*`. Filters local files, not remote downloads.",
)
@server_option
@click.option("-v", "--verbose", "verbose", is_flag=True, help="Enable extended error logging.")
@click.version_option(package_name="onlinelatex-sync")
@click.pass_context
def main(ctx, local, remote, project_name, auth_file, legacy_auth_file, sync_path, olignore_path, server, verbose):
    """
    Sync an OnlineLaTeX project with the current folder (or --path).

    Each command syncs once; it is not a background watcher. Without -l/-r,
    sync runs in both directions and does not propagate deletions: a file
    present on only one side is copied to the other.

    If both sides changed, or differ on the first sync, choose local, remote,
    or skip (default: skip). There is no automatic text merge. One-way modes
    (-l/-r) ask before deleting files present only at the destination and
    before overwriting destination edits not covered by the last sync.

    Use list to find the exact remote project name. Without -n, the name
    defaults to the local directory name. Sync does not create a project;
    use create first if needed. Keep .onlinelatex-sync-state.json between
    runs so changes can be compared with the last sync.

    Exclude build outputs such as main.pdf and output.pdf in .olignore.
    Do not exclude *.pdf if the paper uses PDF figures. In upload-only mode,
    remote files excluded from the local list may be offered for deletion.

    \b
    Example (run from your paper directory):
      onlinelatex login
      onlinelatex list
      onlinelatex -n "My Paper"

    Sync before editing to receive remote changes, then again to upload yours.
    For command-specific guidance, run onlinelatex COMMAND --help.
    """
    configure_console()
    if ctx.invoked_subcommand is None:
        overleaf_client = load_client(resolve_auth_file(ctx, auth_file, legacy_auth_file, "--store-path"), server)

        # Change the current directory to the specified sync path
        os.chdir(sync_path)

        project_name = project_name or os.path.basename(os.getcwd())
        project = execute_action(
            lambda: overleaf_client.get_project(project_name),
            "Querying project",
            "Project queried successfully.",
            "Project could not be queried.",
            verbose,
        )

        project_infos = execute_action(
            lambda: overleaf_client.get_project_infos(project["id"]),
            "Querying project details",
            "Project details queried successfully.",
            "Project details could not be queried.",
            verbose,
        )

        zip_file = execute_action(
            lambda: zipfile.ZipFile(io.BytesIO(overleaf_client.download_project(project["id"]))),
            "Downloading project",
            "Project downloaded successfully.",
            "Project could not be downloaded.",
            verbose,
        )

        if not (local or remote):
            two_way_sync(overleaf_client, project, project_infos, zip_file, olignore_path, verbose)
            return

        sync = False  # -l and -r below sync in one direction only
        state = load_sync_state(overleaf_client.base_url, project["id"])
        uploaded = {}

        def upload(name):
            uploaded[name] = upload_file(overleaf_client, project, project_infos, name)

        if remote:
            remote_files = zip_file.namelist()
            deleted_files = [] if sync else [f for f in olignore_keep_list(olignore_path) if f not in remote_files]
            written = sync_func(
                files_from=remote_files,
                deleted_files=deleted_files,
                create_file_at_to=lambda name: write_file(name, zip_file.read(name)),
                delete_file_at_to=lambda name: delete_file(name),
                create_file_at_from=upload,
                from_exists_in_to=lambda name: os.path.isfile(name),
                from_equal_to_to=lambda name: open(name, "rb").read() == zip_file.read(name),
                to_unchanged_since_last_sync=lambda name: unchanged_since_last_sync(
                    state, name, "local", read_file(name)
                ),
                from_name="remote",
                to_name="local",
                verbose=verbose,
            )
            record_state(overleaf_client, project, zip_file, state, written["to"], uploaded=uploaded)
        if local:
            # Computed once (#60)
            local_files = olignore_keep_list(olignore_path)
            deleted_files = [] if sync else [f for f in zip_file.namelist() if f not in local_files]
            written = sync_func(
                files_from=local_files,
                deleted_files=deleted_files,
                create_file_at_to=upload,
                delete_file_at_to=lambda name: overleaf_client.delete_file(project["id"], project_infos, name),
                create_file_at_from=lambda name: write_file(name, zip_file.read(name)),
                from_exists_in_to=lambda name: name in zip_file.namelist(),
                from_equal_to_to=lambda name: open(name, "rb").read() == zip_file.read(name),
                to_unchanged_since_last_sync=lambda name: unchanged_since_last_sync(
                    state, name, "remote", zip_file.read(name)
                ),
                from_name="local",
                to_name="remote",
                verbose=verbose,
            )
            record_state(overleaf_client, project, zip_file, state, written["to"], uploaded=uploaded)


@main.command()
@auth_file_options("--path")
@server_option
@click.option("-v", "--verbose", "verbose", is_flag=True, help="Enable extended error logging.")
@click.pass_context
def login(ctx, auth_file, legacy_auth_file, server, verbose):
    """Log in with the browser's onlatex.sid cookie (hidden input).

    Log in to https://www.onlinelatex.com in your browser. Open developer
    tools, then Application/Storage -> Cookies -> https://www.onlinelatex.com.
    Copy the VALUE of onlatex.sid and paste it at the terminal prompt.
    For --server, use the cookie from that server instead.

    Credentials are saved in .olauth-onlinelatex in the current directory
    unless --auth-file is specified. Never share or commit this file or the
    cookie. Run login again when the session expires.

    ONLINELATEX_COOKIE can supply the cookie instead of the hidden prompt.
    Do not put the cookie directly in a command saved to shell history.
    """
    server = resolve_login_server(ctx, server)
    cookie_path = resolve_auth_file(ctx, auth_file, legacy_auth_file, "--path")
    if os.path.isfile(cookie_path) and not click.confirm("Replace the saved OnlineLaTeX login?"):
        return
    cookie = os.environ.get("ONLINELATEX_COOKIE") or click.prompt("Value of the onlatex.sid cookie", hide_input=True)
    execute_action(
        lambda: cookie_login_handler(cookie.strip(), cookie_path, server, SESSION_COOKIES),
        "Login",
        f"Login successful. Saved to {cookie_path}.",
        "Login failed: the cookie is expired or incomplete.",
        verbose,
    )


@main.command(name="create")
@click.argument("name")
@auth_file_options("--store-path")
@server_option
@click.pass_context
def create_project(ctx, name, auth_file, legacy_auth_file, server):
    """Create an empty OnlineLaTeX project and print its URL.

    Requires login. This creates the remote project only; it does not upload
    local files or create a local directory. Choose a unique name so sync
    can select the project unambiguously.

    \b
    Example (run from your existing paper directory):
      onlinelatex create "My Paper"
      onlinelatex -n "My Paper"
    """
    client = load_client(
        resolve_auth_file(ctx, auth_file, legacy_auth_file, "--store-path"),
        server or ctx.find_root().params.get("server"),
    )
    project_id = execute_action(
        lambda: client.create_project(name),
        "Creating project",
        "Project created.",
        "Could not create project.",
    )
    click.echo(f"{client.base_url}/project/{project_id}")


@main.command(name="list")
@auth_file_options("--store-path")
@server_option
@click.option("-v", "--verbose", "verbose", is_flag=True, help="Enable extended error logging.")
@click.pass_context
def list_projects(ctx, auth_file, legacy_auth_file, server, verbose):
    """List your OnlineLaTeX projects, most recently changed first.

    Requires login. Archived and trashed projects are excluded. Use an exact
    name from this list with -n when syncing or downloading a PDF. If projects
    have duplicate names, rename them on the website before selecting one.
    """

    def query_projects():
        for index, p in enumerate(sorted(overleaf_client.all_projects(), key=lambda x: x["lastUpdated"], reverse=True)):
            if not index:
                click.echo("\n")
            click.echo(f"{datetime.fromisoformat(p['lastUpdated']).strftime('%m/%d/%Y, %H:%M:%S')} - {p['name']}")
        return True

    overleaf_client = load_client(
        resolve_auth_file(ctx, auth_file, legacy_auth_file, "--store-path"),
        server or ctx.find_root().params.get("server"),
    )

    click.clear()
    execute_action(
        query_projects,
        "Querying all projects",
        "Querying all projects successful.",
        "Querying all projects failed. Please try again.",
        verbose,
    )


@main.command(name="download")
@click.option(
    "-n",
    "--name",
    "project_name",
    default="",
    help="OnlineLaTeX project name (default: the local directory name).",
)
@click.option(
    "--download-path",
    "download_path",
    default=".",
    type=click.Path(exists=True),
    help="Existing folder to save the PDF in (default: the current folder).",
)
@auth_file_options("--store-path")
@server_option
@click.option("-v", "--verbose", "verbose", is_flag=True, help="Enable extended error logging.")
@click.pass_context
def download_pdf(ctx, project_name, download_path, auth_file, legacy_auth_file, server, verbose):
    """Compile the project on OnlineLaTeX and download the PDF.

    This compiles the remote sources; it does not upload local changes.
    Sync first to include your local edits. The website's selected main
    document and compiler are used, and its compile/account limits apply.

    Saves the server's filename (typically output.pdf) in --download-path;
    an existing file with that name is replaced. The folder must already
    exist. Use -r on the main command to download source files instead.

    \b
    Example (run from your paper directory):
      onlinelatex -n "My Paper"
      mkdir -p build
      onlinelatex download -n "My Paper" --download-path build

    Exclude build/* in .olignore to keep this PDF out of future uploads.
    """

    def download_project_pdf():
        nonlocal project_name
        project_name = project_name or os.path.basename(os.getcwd())
        project = execute_action(
            lambda: overleaf_client.get_project(project_name),
            "Querying project",
            "Project queried successfully.",
            "Project could not be queried.",
            verbose,
        )

        file_name, content = overleaf_client.download_pdf(project["id"])

        if file_name and content:
            # Change the current directory to the specified sync path
            os.chdir(download_path)
            open(file_name, "wb").write(content)

        return True

    overleaf_client = load_client(
        resolve_auth_file(ctx, auth_file, legacy_auth_file, "--store-path"),
        server or ctx.find_root().params.get("server"),
    )

    click.clear()

    execute_action(
        download_project_pdf,
        "Downloading project's PDF",
        "Downloading project's PDF successful.",
        "Downloading project's PDF failed. Please try again.",
        verbose,
    )


def resolve_login_server(ctx, server):
    """Resolve the server from command options, ONLINELATEX_SERVER, or the default."""
    return (server or ctx.find_root().params.get("server") or BASE_URL).rstrip("/")


def load_client(cookie_path, server=None):
    """
    Create a client from the persisted login. The server is --server/ONLINELATEX_SERVER if given, otherwise the
    server stored at login.
    """
    try:
        login = auth.load_auth(cookie_path)
    except auth.AuthFileError as e:
        raise click.ClickException(str(e))
    return OverleafClient(login["cookies"], login["csrf"], base_url=server or login["server"])


def cookie_login_handler(cookie, path, server, cookie_names):
    # Verify the session before saving credentials.
    for cookie_name in cookie_names:
        cookies = {cookie_name: cookie}
        csrf = OverleafClient(cookies, base_url=server).fetch_csrf()
        if csrf is not None:
            auth.save_auth(path, cookies, csrf, server)
            return True
    return False


def delete_file(path):
    _dir = os.path.dirname(path)
    if _dir == path:
        return

    if _dir != "" and not os.path.exists(_dir):
        return
    else:
        os.remove(path)


def upload_file(overleaf_client, project, project_infos, path):
    """
    Upload a local file, read once, so a save during the upload cannot mix two versions
    Params: overleaf_client, project, project_infos, path
    Returns: the hash of the uploaded bytes, for the sync state
    Raises: IOError if Overleaf rejects the upload, so a failed upload is neither reported nor recorded as synced
    """
    content = read_file(path)
    if not overleaf_client.upload_file(project["id"], project_infos, path, len(content), io.BytesIO(content)):
        raise IOError("OnlineLaTeX rejected the upload of %s" % path)
    return syncstate.file_hash(content)


def write_file(path, content):
    _dir = os.path.dirname(path)
    if _dir == path:
        return

    # path is a file
    if _dir != "" and not os.path.exists(_dir):
        os.makedirs(_dir)

    # Follow an existing symlink, as direct writes did, and replace its target atomically.
    target = os.path.realpath(path)
    tmp_path = os.path.join(os.path.dirname(target), ".olsync-download." + uuid.uuid4().hex)
    # Mode 0o666 like open(), so a new file gets the umask's permissions (mkstemp would make it 0o600)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o666)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        if os.path.exists(target):
            shutil.copymode(target, tmp_path)
        os.replace(tmp_path, target)
    except BaseException:
        os.unlink(tmp_path)
        raise


def read_file(path):
    with open(path, "rb") as f:
        return f.read()


def load_sync_state(server, project_id):
    """syncstate.load_state for the sync folder (the CWD); malformed history is ignored with a warning"""
    try:
        return syncstate.load_state(".", server, project_id)
    except syncstate.InvalidStateError:
        click.echo("Warning: ignoring invalid sync history; conflicting files will require confirmation.", err=True)
        return {}


def unchanged_since_last_sync(state, path, side, content):
    """True if `side` ("local" or "remote") of `path` still has the content recorded at the last sync"""
    return state.get(path, {}).get(side) == syncstate.file_hash(content)


def record_state(overleaf_client, project, zip_file, state, synced, uploaded):
    """
    Record the sync state after a one-way sync (-l/-r): transferred files get the hashes of the bytes that were sent or
    written, so edits made during the sync stay pending; files identical on both sides get their current hashes; other
    files (e.g. skipped ones) keep their previous state. After uploads, the uploaded files are read from the project
    again, so that Overleaf's own changes to them are not mistaken for remote edits later.
    Params: synced, the paths written on the target side; uploaded, {path: hash of the uploaded bytes}
    """
    remote = {name: syncstate.file_hash(zip_file.read(name)) for name in zip_file.namelist() if not name.endswith("/")}
    if uploaded:
        previous_remote = remote.copy()
        try:
            fresh = zipfile.ZipFile(io.BytesIO(overleaf_client.download_project(project["id"])))
            for path in uploaded:
                if path in fresh.namelist():
                    remote[path] = syncstate.file_hash(fresh.read(path))
                else:
                    remote.pop(path, None)
        except Exception:
            # The zip from before the sync has the old remote version of the uploaded files; use what was uploaded
            click.echo(
                "Warning: could not refresh the project after uploading; using the uploaded file contents for sync history.",
                err=True,
            )
            remote = previous_remote
            remote.update(uploaded)
    new_state = {}
    for path in sorted(set(remote) | set(state)):
        on_both_sides = path in remote and os.path.isfile(path)
        if on_both_sides:
            local = syncstate.file_hash(read_file(path))
            if path in uploaded or path in synced or local == remote[path]:
                new_state[path] = {
                    "local": uploaded.get(path, remote[path] if path in synced else local),
                    "remote": remote[path],
                }
                continue
        if path in state and (path in remote or os.path.isfile(path)):
            new_state[path] = state[path]
    syncstate.save_state(".", overleaf_client.base_url, project["id"], new_state)


def two_way_sync(overleaf_client, project, project_infos, zip_file, olignore_path, verbose=False):
    """
    Two-way sync that decides per file, based on the state of the last sync (see syncstate.py), instead of the
    project-wide lastUpdated timestamp. Changes on one side are synced; files changed on both sides, or differing
    without sync history, are only overwritten after asking.
    """

    def read(path):
        with open(path, "rb") as f:
            return f.read()

    server = overleaf_client.base_url
    remote_content = {name: zip_file.read(name) for name in zip_file.namelist() if not name.endswith("/")}
    # Local candidates: files allowed by .olignore, plus local copies of remote files (to compare them)
    local_paths = set(olignore_keep_list(olignore_path)) | {name for name in remote_content if os.path.isfile(name)}
    local = {path: syncstate.file_hash(read(path)) for path in local_paths}
    remote = {name: syncstate.file_hash(content) for name, content in remote_content.items()}
    state = load_sync_state(server, project["id"])
    plan = syncstate.plan_two_way(local, remote, state)

    click.echo("\nSyncing files between [local] and [remote]")
    click.echo("=" * 40)

    downloads, uploads, skipped = list(plan["download"]), list(plan["upload"]), []
    for path in plan["conflict"]:
        reason = (
            "was changed locally and on OnlineLaTeX since the last sync"
            if path in state
            else "differs between local and OnlineLaTeX, and there is no sync history for it yet"
        )
        choice = click.prompt(
            "\n-> Conflict: <%s> %s.\nKeep the [l]ocal version, keep the [r]emote version or [s]kip?" % (path, reason),
            default="s",
            type=click.Choice(["l", "r", "s"]),
        )
        {"l": uploads, "r": downloads, "s": skipped}[choice].append(path)

    failed = []
    for path in downloads:
        try:
            write_file(path, remote_content[path])
            local[path] = remote[path]
        except OSError:
            if verbose:
                print(traceback.format_exc())
            failed.append(path)
    for path in uploads:
        try:
            local[path] = upload_file(overleaf_client, project, project_infos, path)
            ok = True
        except Exception:
            if verbose:
                print(traceback.format_exc())
            ok = False
        if not ok:
            failed.append(path)

    for title, paths in [
        ("[DOWNLOAD] Following file(s) downloaded from [remote]", downloads),
        ("[UPLOAD] Following file(s) uploaded to [remote]", uploads),
        ("[SYNC] Following file(s) are up to date", plan["in_sync"]),
        ("[SKIP] Following conflicting file(s) have not been synced", skipped),
        ("[ERROR] Following file(s) could not be synced", failed),
    ]:
        if paths:
            click.echo("\n" + title)
            for path in paths:
                click.echo("\t%s" % path)

    # Record what both sides look like now. After uploads, read the project again, so that changes Overleaf makes
    # to uploaded files (e.g. line endings) are not mistaken for remote edits next time.
    if set(uploads) - set(failed):
        try:
            fresh = zipfile.ZipFile(io.BytesIO(overleaf_client.download_project(project["id"])))
            for path in set(uploads) - set(failed):
                if path in fresh.namelist():
                    remote[path] = syncstate.file_hash(fresh.read(path))
                else:
                    remote.pop(path, None)
        except Exception:
            if verbose:
                print(traceback.format_exc())
            for path in set(uploads) - set(failed):
                remote[path] = local[path]
    new_state = {}
    for path in sorted(set(local_paths) | set(remote) | set(downloads)):
        if path in skipped or path in failed:
            if path in state:
                new_state[path] = state[path]
        elif path in remote and os.path.isfile(path):
            new_state[path] = {
                "local": local[path],
                "remote": remote[path],
            }
    syncstate.save_state(".", server, project["id"], new_state)

    click.echo("")
    if failed:
        raise click.ClickException("%d file(s) could not be synced, see above" % len(failed))
    click.echo("✅  Synced files between [local] and [remote]")
    click.echo("")


def sync_func(
    files_from,
    deleted_files,
    create_file_at_to,
    delete_file_at_to,
    create_file_at_from,
    from_exists_in_to,
    from_equal_to_to,
    to_unchanged_since_last_sync,
    from_name,
    to_name,
    verbose=False,
):
    click.echo("\nSyncing files from [%s] to [%s]" % (from_name, to_name))
    click.echo("=" * 40)

    newly_add_list = []
    update_list = []
    delete_list = []
    restore_list = []
    not_restored_list = []
    not_sync_list = []
    synced_list = []

    for name in files_from:
        if from_exists_in_to(name):
            if not from_equal_to_to(name):
                if not to_unchanged_since_last_sync(name) and not click.confirm(
                    "\n-> Warning: file <%s> was changed on [%s] since the last sync (or there is no sync history for "
                    "it yet).\nOverwrite it with the version from [%s]?" % (name, to_name, from_name)
                ):
                    not_sync_list.append(name)
                    continue

                update_list.append(name)
            else:
                synced_list.append(name)
        else:
            newly_add_list.append(name)

    for name in deleted_files:
        delete_choice = click.prompt(
            "\n-> Warning: file <%s> does not exist on [%s] anymore (but it still exists on [%s])."
            "\nShould the file be [d]eleted, [r]estored or [i]gnored?" % (name, from_name, to_name),
            default="i",
            type=click.Choice(["d", "r", "i"]),
        )
        if delete_choice == "d":
            delete_list.append(name)
        elif delete_choice == "r":
            restore_list.append(name)
        elif delete_choice == "i":
            not_restored_list.append(name)

    click.echo("\n[NEW] Following new file(s) created on [%s]" % to_name)
    for name in newly_add_list:
        click.echo("\t%s" % name)
        try:
            create_file_at_to(name)
        except:
            if verbose:
                print(traceback.format_exc())
            raise click.ClickException("\n[ERROR] An error occurred while creating new file(s) on [%s]" % to_name)

    click.echo("\n[NEW] Following new file(s) created on [%s]" % from_name)
    for name in restore_list:
        click.echo("\t%s" % name)
        try:
            create_file_at_from(name)
        except:
            if verbose:
                print(traceback.format_exc())
            raise click.ClickException("\n[ERROR] An error occurred while creating new file(s) on [%s]" % from_name)

    click.echo("\n[UPDATE] Following file(s) updated on [%s]" % to_name)
    for name in update_list:
        click.echo("\t%s" % name)
        try:
            create_file_at_to(name)
        except:
            if verbose:
                print(traceback.format_exc())
            raise click.ClickException("\n[ERROR] An error occurred while updating file(s) on [%s]" % to_name)

    click.echo("\n[DELETE] Following file(s) deleted on [%s]" % to_name)
    for name in delete_list:
        click.echo("\t%s" % name)
        try:
            if delete_file_at_to(name) is False:
                raise OSError("Deletion was rejected")
        except:
            if verbose:
                print(traceback.format_exc())
            raise click.ClickException("\n[ERROR] An error occurred while deleting file(s) on [%s]" % to_name)

    click.echo("\n[SYNC] Following file(s) are up to date")
    for name in synced_list:
        click.echo("\t%s" % name)

    click.echo("\n[SKIP] Following file(s) on [%s] have not been synced to [%s]" % (from_name, to_name))
    for name in not_sync_list:
        click.echo("\t%s" % name)

    click.echo("\n[SKIP] Following file(s) on [%s] have not been synced to [%s]" % (to_name, from_name))
    for name in not_restored_list:
        click.echo("\t%s" % name)

    click.echo("")
    click.echo("✅  Synced files from [%s] to [%s]" % (from_name, to_name))
    click.echo("")

    # Which files were written on each side, for recording the sync state
    return {"to": newly_add_list + update_list, "from": restore_list}


def execute_action(action, progress_message, success_message, fail_message, verbose_error_logging=False):
    # Colours only on a terminal; yaspin warns otherwise (e.g. when the output is piped into a file)
    with yaspin(text=progress_message, color="green" if sys.stdout.isatty() else None) as spinner:
        try:
            success = action()
        except (HTTPError, RateLimitError, SessionExpiredError, AmbiguousProjectError) as e:
            # A clear reason is more helpful than the generic failure message
            spinner.fail("💥 ")
            raise click.ClickException(str(e))
        except click.ClickException:
            # Already explained, e.g. by a nested execute_action
            spinner.fail("💥 ")
            raise
        except:
            if verbose_error_logging:
                print(traceback.format_exc())
            success = False

        if success:
            spinner.write(success_message)
            spinner.ok("✅ ")
        else:
            spinner.fail("💥 ")
            raise click.ClickException(fail_message)

        return success


def olignore_keep_list(olignore_path):
    """
    The list of files to keep synced, with support for sub-folders.
    Should only be called when syncing from local to remote.
    """
    # get list of files recursively (ignore .* files)
    files = glob.glob("**", recursive=True)

    click.echo("=" * 40)
    if not os.path.isfile(olignore_path):
        click.echo("\nNotice: .olignore file does not exist, will sync all items.")
        keep_list = files
    else:
        click.echo("\n.olignore: using %s to filter items" % olignore_path)
        with open(olignore_path, "r") as f:
            # "./main.tex" and "main.tex" mean the same file, matched paths have no "./" prefix (#72)
            ignore_pattern = [pattern.removeprefix("./") for pattern in f.read().splitlines()]

        keep_list = [f for f in files if not any(fnmatch.fnmatch(f, ignore) for ignore in ignore_pattern)]

    keep_list = [Path(item).as_posix() for item in keep_list if not os.path.isdir(item)]
    return keep_list


if __name__ == "__main__":
    main()
