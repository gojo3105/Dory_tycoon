"""Tests for the localhost control panel.

Run:  python3 AI_GAME_COMPANY/tests/test_server.py

This is the one file in the project that runs commands on behalf of a web
page, so most of what follows is adversarial: a wrong token, a foreign
Origin, a public Host header, an action name that is not on the allowlist, an
argument with a shell metacharacter in it, a second job while one is running.

A real server is started on an ephemeral port for the request-level tests -
the guards live in HTTP handling, and asserting on the functions underneath
them would not prove the endpoint is closed.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import server as srv  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
COMPANY = REPO / "AI_GAME_COMPANY"


class BindingTests(unittest.TestCase):
    def test_bound_to_loopback_and_never_all_interfaces(self):
        # A regression guard worth its line: changing this to 0.0.0.0 would
        # publish a run-arbitrary-builds endpoint to the local network.
        self.assertEqual("127.0.0.1", srv.LOOPBACK)
        self.assertNotIn("0.0.0.0", srv.LOOPBACK)

    def test_every_action_builds_its_own_argv(self):
        # Nothing from a request may reach a command line except through the
        # validated id, so an action that takes no argument must ignore it.
        for name, action in srv.ACTIONS.items():
            argv = action.build(REPO, "; rm -rf /")
            self.assertIsInstance(argv, list, name)
            if not action.needs:
                joined = " ".join(argv)
                self.assertNotIn("rm -rf", joined, name)
                self.assertNotIn(";", joined, name)

    def test_no_action_runs_through_a_shell(self):
        # Every argv[0] is a real executable, not a shell invocation.
        for name, action in srv.ACTIONS.items():
            first = action.build(REPO, "game01")[0]
            self.assertNotIn("sh", Path(first).name.split(".")[0].lower()[:2],
                             f"{name} looks like it shells out")


class ArgumentGuardTests(unittest.TestCase):
    def setUp(self):
        self.runner = srv.Runner(REPO, COMPANY)

    def guard(self, action_name: str, arg: str):
        return self.runner.valid_arg(srv.ACTIONS[action_name], arg)

    def test_rejects_shell_metacharacters(self):
        for bad in ("game01; whoami", "game01 && ls", "../../etc/passwd",
                    "game01|cat", "$(id)", "game01\nwhoami", ""):
            ok, why = self.guard("build", bad)
            self.assertFalse(ok, f"{bad!r} was accepted")
            self.assertTrue(why)

    def test_rejects_a_game_with_no_spec(self):
        ok, why = self.guard("build", "game99")
        self.assertFalse(ok)
        self.assertIn("game99", why)

    def test_accepts_a_game_that_has_a_spec(self):
        if not (REPO / "GameSpecs" / "game01.json").is_file():
            self.skipTest("no game01 spec committed")
        self.assertEqual((True, ""), self.guard("build", "game01"))

    def test_rejects_a_task_that_is_not_on_the_board(self):
        ok, why = self.guard("team-run", "NOPE-1")
        self.assertFalse(ok)

    def test_rejects_a_task_owned_by_claude(self):
        # Handing Claude's work to Codex is exactly what the board's owner
        # field exists to prevent; the panel must not route around it.
        ok, why = self.guard("team-run", "CLAUDE-UI1")
        self.assertFalse(ok)
        self.assertIn("Codex", why)

    def test_accepts_a_codex_task(self):
        board = COMPANY / "config" / "TASKBOARD.json"
        if not board.is_file():
            self.skipTest("no board committed")
        from company.orchestrator.teamwork import TaskBoard
        codex_ids = [t.id for t in TaskBoard.load(board).tasks if t.owner == "codex"]
        if not codex_ids:
            self.skipTest("no codex tasks on the board")
        self.assertEqual((True, ""), self.guard("team-run", codex_ids[0]))

    def test_an_action_without_an_argument_needs_no_validation(self):
        self.assertEqual((True, ""), self.guard("git-status", ""))

    def test_unknown_action_is_refused_before_anything_runs(self):
        job, why = self.runner.start("rm -rf /", "")
        self.assertIsNone(job)
        self.assertTrue(why)


class LiveServerTests(unittest.TestCase):
    """Boots a real server so the HTTP guards are what gets tested."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = Path(cls.tmp.name)
        # A working tree with just enough for collect() and the guards.
        (cls.repo / "GameSpecs").mkdir()
        shutil.copy(REPO / "GameSpecs" / "game01.json",
                    cls.repo / "GameSpecs" / "game01.json")
        (cls.repo / "Reports").mkdir()
        company = cls.repo / "AI_GAME_COMPANY"
        (company / "config").mkdir(parents=True)
        for name in ("HARDWARE_PROFILE.json", "company_policy.json",
                     "LICENSE_REGISTRY.json", "TASKBOARD.json"):
            source = COMPANY / "config" / name
            if source.is_file():
                shutil.copy(source, company / "config" / name)

        cls.token = "test-token-abcdefghijklmnop"
        cls.runner = srv.Runner(cls.repo, company)
        cls.httpd = ThreadingHTTPServer(
            (srv.LOOPBACK, 0), srv.make_handler(cls.runner, cls.token))
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def url(self, path: str) -> str:
        return f"http://{srv.LOOPBACK}:{self.port}{path}"

    def post(self, payload: dict, headers: dict | None = None, path: str = "/run"):
        request = urllib.request.Request(
            self.url(path), method="POST",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", **(headers or {})})
        try:
            # No proxy: the environment sets HTTPS_PROXY, and urllib would
            # otherwise try to reach loopback through it.
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, path: str):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(self.url(path))
        try:
            with opener.open(request, timeout=20) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    # ---- the page ----

    def test_serves_a_page_with_the_control_panel(self):
        status, body = self.get("/")
        self.assertEqual(200, status)
        page = body.decode("utf-8")
        self.assertIn("도리 AI 게임 스튜디오", page)
        self.assertIn("/plan-game", page)
        self.assertIn("/create-game", page)
        self.assertIn("AI 제어", page)
        self.assertIn("const TOKEN", page)

    def test_status_compatibility_endpoint_returns_runner_state(self):
        status, body = self.get("/api/status")
        self.assertEqual(200, status)
        payload = json.loads(body)
        self.assertFalse(payload["busy"])
        self.assertIsNone(payload["job"])

    def test_board_compatibility_endpoint_uses_existing_board_controls(self):
        status, body = self.get("/board")
        self.assertEqual(200, status)
        self.assertIn("tasks", json.loads(body))

        status, payload = self.post({
            "token": self.token,
            "task": "MISSING",
            "operation": "delete",
        }, path="/board")
        self.assertEqual(404, status)
        self.assertIn("작업판", payload["error"])

    def test_the_static_file_has_no_control_panel(self):
        # The published/written copy has nothing to POST to. Buttons there
        # would be controls that silently do nothing.
        from company.orchestrator import dashboard as dash
        page = dash.render(dash.collect(self.repo))
        self.assertNotIn("AI 제어", page)
        self.assertNotIn("const TOKEN", page)
        # The endpoint literal as the served script spells it. Not a bare
        # "/run": the page legitimately names Reports/runs/latest.txt.
        self.assertNotIn("'/run'", page)
        self.assertNotIn("data-act=", page)

    def test_unknown_path_is_404(self):
        self.assertEqual(404, self.get("/../../etc/passwd")[0])
        self.assertEqual(404, self.get("/secrets")[0])

    # ---- POST guards ----

    def test_a_wrong_token_is_refused(self):
        status, body = self.post({"token": "wrong", "action": "git-status"})
        self.assertEqual(403, status)
        self.assertIn("토큰", body["error"])

    def test_a_missing_token_is_refused(self):
        self.assertEqual(403, self.post({"action": "git-status"})[0])

    def test_a_cross_site_origin_is_refused_even_with_the_right_token(self):
        # Any page in the browser can reach loopback; the token is what stops
        # it, and the Origin check refuses it before the token is even read.
        status, body = self.post({"token": self.token, "action": "git-status"},
                                 headers={"Origin": "https://evil.example"})
        self.assertEqual(403, status)
        self.assertIn("cross-site", body["error"])

    def test_a_foreign_host_header_is_refused(self):
        status, _ = self.post({"token": self.token, "action": "git-status"},
                              headers={"Host": "gamefactory.example.com"})
        self.assertEqual(400, status)

    def test_our_own_origin_is_accepted(self):
        status, _ = self.post(
            {"token": self.token, "action": "git-status"},
            headers={"Origin": f"http://{srv.LOOPBACK}:{self.port}"})
        self.assertEqual(200, status)

    def test_garbage_json_is_refused(self):
        request = urllib.request.Request(
            self.url("/run"), method="POST", data=b"not json",
            headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            opener.open(request, timeout=10)
            self.fail("garbage body was accepted")
        except urllib.error.HTTPError as exc:
            self.assertEqual(400, exc.code)

    def test_an_oversized_body_is_refused(self):
        status, _ = self.post({"token": self.token, "action": "git-status",
                               "arg": "x" * (srv.MAX_BODY + 100)})
        self.assertEqual(400, status)

    def test_a_wrong_action_is_refused_with_the_right_token(self):
        status, body = self.post({"token": self.token, "action": "deploy"})
        self.assertEqual(409, status)

    def test_log_for_an_unknown_job_is_not_a_success(self):
        status, body = self.get("/log?job=deadbeef")
        payload = json.loads(body)
        self.assertEqual(404, status)
        # done with a non-zero code, so the page never reads a missing job as
        # a job that finished cleanly.
        self.assertTrue(payload["done"])
        self.assertNotEqual(0, payload["exit_code"])

    # ---- AI game creator ----

    def test_game_plan_is_a_preview_and_locks_the_shared_character(self):
        target = self.repo / "GameSpecs" / "game02.json"
        target.unlink(missing_ok=True)
        status, body = self.post({
            "token": self.token,
            "idea": "사탕 왕국에서 코인을 연속으로 모으는 러너",
            "style": "auto",
            "difficulty": "Easy",
            "theme": "Candy",
        }, path="/plan-game")
        self.assertEqual(200, status)
        self.assertFalse(body["saved"])
        self.assertEqual("Dori_Default", body["plan"]["character"])
        self.assertEqual("game02", body["plan"]["game_id"])
        self.assertFalse(target.exists())

    def test_game_create_can_save_a_spec_without_starting_a_process(self):
        target = self.repo / "GameSpecs" / "game02.json"
        target.unlink(missing_ok=True)
        try:
            status, body = self.post({
                "token": self.token,
                "idea": "중력이 뒤집히는 하늘 러너",
                "pipeline": "spec",
            }, path="/create-game")
            self.assertEqual(200, status)
            self.assertTrue(body["saved"])
            self.assertTrue(body["done"])
            self.assertTrue(target.is_file())
            spec = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual("Dori_Default", spec["theme"]["character"])
        finally:
            target.unlink(missing_ok=True)

    def test_game_creator_rejects_unlisted_pipeline(self):
        status, body = self.post({
            "token": self.token,
            "idea": "안전한 게임",
            "pipeline": "powershell",
        }, path="/create-game")
        self.assertEqual(409, status)
        self.assertIn("지원하지 않는", body["error"])


class JobProgressTests(unittest.TestCase):
    """A polled job carries the Korean summary, not just a wall of English.

    Derived on this side rather than in the browser so the rule that every
    phrase is anchored to a printed line can be tested at all.
    """

    def job(self, action="team-run", arg="C-1"):
        return srv.Job(id="j1", steps=[self.step(action, arg)])

    def step(self, action="team-run", arg="C-1"):
        return srv.Step(action=action, arg=arg, argv=["true"], timeout=60)

    def test_the_snapshot_carries_a_phase_and_the_argument(self):
        job = self.job()
        job.append("=== HANDING C-1 TO CODEX ===\n")
        payload = job.snapshot()

        self.assertEqual("C-1", payload["arg"])
        self.assertIn("progress", payload)
        self.assertEqual("작업 명세를 읽고 Codex에게 넘기는 중",
                         payload["progress"]["phase"])
        self.assertEqual("Codex 작업 실행", payload["progress"]["action_label"])

    def test_a_job_that_has_printed_nothing_is_starting(self):
        self.assertEqual("시작하는 중", self.job().snapshot()["progress"]["phase"])

    def test_elapsed_time_stops_when_the_job_does(self):
        # Otherwise a finished run keeps counting up for as long as the page
        # is left open, which reads as still running.
        job = self.job()
        job.finished = job.started + 42
        first = job.snapshot()["progress"]["elapsed"]
        self.assertEqual("42초", first)
        self.assertEqual(first, job.snapshot()["progress"]["elapsed"])

    def test_a_finished_job_reports_its_exit_code(self):
        job = self.job()
        job.done, job.exit_code, job.finished = True, 4, job.started + 3
        progress = job.snapshot()["progress"]
        self.assertTrue(progress["done"])
        self.assertIn("4", progress["phase"])

    def test_the_running_job_is_exposed_read_only(self):
        # LIVE state reaches the renderer, but nothing a request can send
        # creates or clears it - only start() makes a Job.
        self.assertFalse(hasattr(srv.Handler, "set_job"))
        self.assertNotIn("job", srv.ACTIONS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
