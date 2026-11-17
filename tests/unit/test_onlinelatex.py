import io
import os
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from click.testing import CliRunner
from olsync import syncstate
from olsync.olclient import BASE_URL, OverleafClient, SessionExpiredError
from olsync.olsync import DEFAULT_AUTH_FILE, main


class ClientTests(unittest.TestCase):
    def test_online_defaults_are_separate(self):
        self.assertEqual(BASE_URL, "https://www.onlinelatex.com")
        self.assertEqual(DEFAULT_AUTH_FILE, ".olauth-onlinelatex")
        self.assertEqual(syncstate.STATE_FILE, ".onlinelatex-sync-state.json")

    @patch("olsync.olclient._request")
    def test_projects_use_api_not_empty_dashboard_metadata(self, request):
        projects = [{"id": "p1", "name": "Paper"}, {"id": "p2", "name": "Old", "archived": True}]
        request.return_value.json.return_value = {"totalSize": 2, "projects": projects}
        request.return_value.url = BASE_URL + "/api/project"
        request.return_value.status_code = 200
        client = OverleafClient({"onlatex.sid": "test-cookie"}, "test-csrf")
        self.assertEqual(client.all_projects(), projects[:1])
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", BASE_URL + "/api/project"))
        self.assertEqual(kwargs["headers"]["X-Csrf-Token"], "test-csrf")
        self.assertEqual(kwargs["json"], {"sort": "lastUpdated"})

    @patch("olsync.olclient._request")
    def test_expired_session_requests_online_login(self, request):
        request.return_value.status_code = 401
        with self.assertRaisesRegex(SessionExpiredError, "onlinelatex login"):
            OverleafClient({"onlatex.sid": "expired"}, "csrf").all_projects()

    @patch("olsync.olclient._request")
    def test_create_blank_project(self, request):
        request.return_value.json.return_value = {"project_id": "new-id"}
        client = OverleafClient({"onlatex.sid": "test-cookie"}, "test-csrf")
        self.assertEqual(client.create_project("New paper"), "new-id")
        self.assertEqual(request.call_args.args[1], BASE_URL + "/project/new")
        self.assertEqual(request.call_args.kwargs["json"]["template"], "none")
        self.assertEqual(request.call_args.kwargs["json"]["projectName"], "New paper")

    @patch("olsync.olclient._request")
    def test_compile_and_download_pdf(self, request):
        compile_response = Mock(ok=True)
        compile_response.content = b'{"status":"success","outputFiles":[{"type":"pdf","path":"output.pdf","url":"/project/p1/output/output.pdf"}],"compileGroup":"standard","clsiServerId":"clsi"}'
        pdf_response = Mock(ok=True, content=b"%PDF-1.5\ncompiled")
        request.side_effect = [compile_response, pdf_response]
        name, content = OverleafClient({"onlatex.sid": "test-cookie"}, "csrf").download_pdf("p1")
        self.assertEqual(name, "output.pdf")
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertEqual(request.call_args.args, ("GET", BASE_URL + "/project/p1/output/output.pdf"))
        self.assertEqual(request.call_args.kwargs["params"], {"compileGroup": "standard", "clsiserverid": "clsi"})

    @patch("olsync.olclient._request")
    def test_compile_failure_is_explained_by_cli(self, request):
        from requests import HTTPError

        request.return_value.content = b'{"status":"timedout"}'
        with self.assertRaisesRegex(HTTPError, "compile status: timedout"):
            OverleafClient({"onlatex.sid": "test-cookie"}, "csrf").download_pdf("p1")
        client = Mock()
        client.get_project.return_value = {"id": "p1"}
        client.download_pdf.side_effect = HTTPError("OnlineLaTeX compile status: timedout.")
        with patch("olsync.olsync.load_client", return_value=client):
            result = CliRunner().invoke(main, ["download", "-n", "Paper"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("compile status: timedout", result.output)

    @patch("olsync.olsync.cookie_login_handler", return_value=True)
    def test_login_defaults_to_online_cookie(self, login):
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(main, ["login"], input="test-cookie\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(login.call_args.args, ("test-cookie", DEFAULT_AUTH_FILE, BASE_URL, ["onlatex.sid"]))


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()
        self.previous_cwd = os.getcwd()
        self.context = self.runner.isolated_filesystem()
        self.context.__enter__()
        self.remote = {"main.tex": b"base"}
        Path("main.tex").write_bytes(b"base")
        Path(".olignore").write_text("main.pdf\n*.aux\n")
        self.client = Mock(base_url=BASE_URL)
        self.client.get_project.return_value = {"id": "p1", "name": "Paper"}
        self.client.get_project_infos.return_value = {"rootFolder": []}
        self.client.download_project.side_effect = self.archive
        self.client.upload_file.side_effect = self.upload
        self.client.delete_file.side_effect = self.delete
        self.client_patch = patch("olsync.olsync.load_client", return_value=self.client)
        self.client_patch.start()

    def tearDown(self):
        self.client_patch.stop()
        os.chdir(self.previous_cwd)
        self.context.__exit__(None, None, None)

    def archive(self, project_id):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as archive:
            for name, content in self.remote.items():
                archive.writestr(name, content)
        return data.getvalue()

    def upload(self, project_id, infos, name, size, file):
        self.remote[name] = file.read()
        return True

    def delete(self, project_id, infos, name):
        del self.remote[name]
        return True

    def run_sync(self, *args, input=None):
        result = self.runner.invoke(main, ["-n", "Paper", *args], input=input)
        self.assertEqual(result.exit_code, 0, result.output)
        return result

    def test_upload_download_conflict_and_ignored_output(self):
        Path(".olsync-state.json").write_text("overleaf-state-untouched")
        Path(".olauth").write_text("overleaf-auth-untouched")
        Path("main.pdf").write_bytes(b"build output")
        self.run_sync()
        Path("main.tex").write_bytes(b"local edit")
        self.run_sync("-l")
        self.assertEqual(self.remote["main.tex"], b"local edit")
        self.remote["main.tex"] = b"coauthor edit"
        self.run_sync("-r")
        self.assertEqual(Path("main.tex").read_bytes(), b"coauthor edit")
        self.remote["main.tex"] = b"coauthor second edit"
        Path("main.tex").write_bytes(b"local second edit")
        result = self.run_sync(input="s\n")
        self.assertIn("Conflict", result.output)
        self.assertEqual(self.remote["main.tex"], b"coauthor second edit")
        self.assertEqual(Path("main.tex").read_bytes(), b"local second edit")
        self.run_sync(input="r\n")
        self.assertEqual(Path("main.tex").read_bytes(), b"coauthor second edit")
        self.assertNotIn("main.pdf", self.remote)
        self.assertEqual(Path(".olsync-state.json").read_text(), "overleaf-state-untouched")
        self.assertEqual(Path(".olauth").read_text(), "overleaf-auth-untouched")
        self.assertTrue(Path(".onlinelatex-sync-state.json").is_file())

    def test_two_way_transfers_edits_on_either_side(self):
        self.run_sync()
        Path("main.tex").write_bytes(b"local edit")
        self.run_sync()
        self.assertEqual(self.remote["main.tex"], b"local edit")
        self.remote["main.tex"] = b"remote edit"
        self.run_sync()
        self.assertEqual(Path("main.tex").read_bytes(), b"remote edit")
        self.client.upload_file.reset_mock()
        self.run_sync()
        self.client.upload_file.assert_not_called()

    def test_first_upload_requires_confirmation(self):
        Path("main.tex").write_bytes(b"local paper")
        self.run_sync("-l", input="n\n")
        self.assertEqual(self.remote["main.tex"], b"base")
        self.run_sync("-l", input="y\n")
        self.assertEqual(self.remote["main.tex"], b"local paper")

    def test_one_way_deletion_requires_confirmation(self):
        self.run_sync()
        Path("main.tex").unlink()
        self.run_sync("-l", input="i\n")
        self.assertIn("main.tex", self.remote)
        self.run_sync("-l", input="d\n")
        self.assertNotIn("main.tex", self.remote)

    def test_failed_upload_is_not_recorded_as_success(self):
        self.run_sync()
        before = Path(syncstate.STATE_FILE).read_bytes()
        Path("main.tex").write_bytes(b"changed")
        self.client.upload_file.side_effect = None
        self.client.upload_file.return_value = False
        result = self.runner.invoke(main, ["-l", "-n", "Paper"])
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(Path(syncstate.STATE_FILE).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
