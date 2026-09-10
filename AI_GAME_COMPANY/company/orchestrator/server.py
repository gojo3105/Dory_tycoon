"""A localhost control panel for the orchestrator.

WHY A SERVER AND NOT JUST THE HTML FILE. The dashboard can only ever describe
things; a page that runs Codex or starts an Android build has to be able to
execute something, and a published Artifact never can. So the read-only page
stays a file, and this serves the same page plus buttons - on the machine
where the AI tooling actually lives.

The buttons run commands, so this is the most dangerous file in the project.
Four rules hold, and all four are enforced here rather than documented:

  LOOPBACK ONLY
      Bound to 127.0.0.1. Never 0.0.0.0 - that would put a
      run-arbitrary-builds endpoint on whatever network the PC is joined to.

  NO COMMAND COMES FROM THE CLIENT
      The browser sends an action NAME. This file owns the argv. There is no
      code path where text from a request reaches a shell, and shell=False
      everywhere, so a crafted argument cannot become a second command.

      This holds for the order box too, which is the one place a person types
      a sentence. The sentence is written onto TASKBOARD.json as a task's
      goal - data - and what runs is the same fixed team-run action with an
      id orders.py generated. See orders.py for why that is the whole trick.

  A TOKEN, BECAUSE LOCALHOST IS NOT A BOUNDARY
      Any page in the user's browser can POST to 127.0.0.1. A random
      per-run token, printed in the terminal and embedded only in the page
      this server itself renders, is what stops a hostile site firing a
      build. Requests carrying a foreign Origin are refused outright.

  ONE JOB AT A TIME
      These jobs are Unity builds and Codex runs against one working tree.
      Two at once would corrupt each other's results.

It commits nothing and pushes nothing: same rule as everywhere else, a person
reviews the diff.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from company.orchestrator import dashboard as dash
from company.orchestrator import game_creator as creation
from company.orchestrator import orders as ordering
from company.orchestrator import progress as prog
from company.orchestrator.teamwork import TaskBoard

LOOPBACK = "127.0.0.1"
MAX_BODY = 16384
# Kept small and read-only-ish per entry; the log a browser polls does not
# need to hold a whole Gradle run.
MAX_LOG_CHARS = 200_000

# Ids we are willing to put on a command line. Anything else is rejected
# before argv is built, so the allowlist below never has to trust its input.
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


@dataclass
class Action:
    """One thing a button may do. argv is built here, never received."""
    label: str
    build: Any                      # (repo_root, arg) -> list[str]
    needs: str = ""                 # "" | "task" | "game"
    timeout: int = 900


def _python() -> str:
    """This interpreter, so a venv is not silently swapped for the system one."""
    return sys.executable or "python"


ACTIONS: dict[str, Action] = {
    "dashboard": Action(
        "대시보드 새로고침",
        lambda root, arg: [_python(), "-m", "company.orchestrator.main", "dashboard"],
        timeout=300),
    "codex-doctor": Action(
        "Codex 진단",
        lambda root, arg: [_python(), "-m", "company.orchestrator.main", "codex", "--doctor"],
        timeout=180),
    "team-run": Action(
        "Codex 작업 실행",
        lambda root, arg: [_python(), "-m", "company.orchestrator.main",
                           "team", "run", "--task", arg],
        needs="task", timeout=2400),
    "build": Action(
        "빌드",
        lambda root, arg: [_python(), "-m", "company.orchestrator.main",
                           "build", "--game", arg],
        needs="game", timeout=5400),
    # The second half of an order. Codex cannot compile - its own house rules
    # say so - so an order that stopped at "Codex finished" would hand back
    # unverified C# and call it done, which is the one thing section 8 forbids
    # above all others. Running the tests is what makes "AI did the work" true.
    "test": Action(
        "Unity 테스트",
        lambda root, arg: [_python(), "-m", "company.orchestrator.main",
                           "test", "--game", arg, "--platform", "playmode"],
        needs="game", timeout=3600),
    # Read-only: lists what is installed and whether each model passes the
    # licence and RAM checks. Installing is deliberately NOT here - policy
    # never_auto_install covers ollama_models, and which model is acceptable
    # is a licence judgement a person makes, not a button.
    "ollama-list": Action(
        "설치된 모델 확인",
        lambda root, arg: [_python(), "-m", "company.orchestrator.main",
                           "ollama", "--list"],
        timeout=120),
    "git-status": Action(
        "변경된 파일",
        lambda root, arg: ["git", "status", "--short", "--untracked-files=all"],
        timeout=60),
}


@dataclass
class Step:
    """One action in a job. argv is built from ACTIONS, never received."""
    action: str
    arg: str
    argv: list[str]
    timeout: int


@dataclass
class Job:
    """One or more steps, run strictly in sequence.

    A sequence rather than a single command because an order is two things -
    Codex writes the code, then Unity checks it compiles - and section 10's
    one-job-at-a-time rule still holds: the steps share one working tree and
    run one after another, never together.
    """
    id: str
    steps: list[Step]
    # What the job as a whole is, for the page: an order names its department,
    # a button press is just its action.
    title: str = ""
    order_id: str = ""
    step_index: int = 0
    output: str = ""
    done: bool = False
    exit_code: int | None = None
    # monotonic, so a clock adjustment mid-build cannot produce a negative
    # elapsed time or a sudden jump in the page.
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def step(self) -> Step:
        """The step running now, or the last one once the job is done."""
        return self.steps[min(self.step_index, len(self.steps) - 1)]

    @property
    def action(self) -> str:
        """The current step's action - what the phase text is derived from."""
        return self.step.action

    @property
    def arg(self) -> str:
        return self.step.arg

    def append(self, text: str) -> None:
        with self.lock:
            self.output += text
            if len(self.output) > MAX_LOG_CHARS:
                # Keep the tail: the failure is at the end of a build log.
                self.output = "...(앞부분 생략)...\n" + self.output[-MAX_LOG_CHARS:]

    def elapsed(self) -> float:
        end = self.finished if self.finished is not None else time.monotonic()
        return end - self.started

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            # Derived here rather than in the browser so the rule that every
            # phrase is anchored to a printed line lives with the code that
            # can be tested, not in a script tag.
            summary = prog.summarise(self.action, self.output, self.elapsed(),
                                     done=self.done, exit_code=self.exit_code)
            return {"job": self.id, "action": self.action, "arg": self.arg,
                    "title": self.title, "order": self.order_id,
                    "step": self.step_index + 1, "steps": len(self.steps),
                    "step_labels": [ACTIONS[s.action].label for s in self.steps],
                    "output": self.output, "done": self.done,
                    "exit_code": self.exit_code, "progress": summary.as_dict()}


class Runner:
    """Runs one job at a time and keeps only the current one's output."""

    def __init__(self, repo_root: Path, company_root: Path):
        self.repo_root = repo_root
        self.company_root = company_root
        self.lock = threading.Lock()
        self.current: Job | None = None

    def busy(self) -> bool:
        with self.lock:
            return self.current is not None and not self.current.done

    def board(self) -> TaskBoard:
        """The task board, read fresh.

        Never cached: Codex, the scheduled sync and a person with an editor all
        write this file, so a copy held in memory would be stale by the time an
        order was appended to it - and saving that copy would drop their work.
        """
        return TaskBoard.load(ordering.board_path(self.company_root))

    def valid_arg(self, action: Action, arg: str) -> tuple[bool, str]:
        """Is this id real, and is it safe to put on a command line?"""
        if not action.needs:
            return True, ""
        if not SAFE_ID.match(arg or ""):
            return False, "허용되지 않는 형식의 id 입니다."

        if action.needs == "task":
            ids = {t.id for t in self.board().tasks if t.owner == "codex"}
            if arg not in ids:
                return False, f"작업판에 Codex 소유의 '{arg}' 작업이 없습니다."
        elif action.needs == "game":
            if not (self.repo_root / "GameSpecs" / f"{arg}.json").is_file():
                return False, f"GameSpecs/{arg}.json 이 없습니다."
        return True, ""

    def plan(self, pairs: list[tuple[str, str]]) -> tuple[list[Step], str]:
        """Turn (action name, arg) pairs into steps, or say why not.

        Every argv in the returned list was built by ACTIONS from an arg that
        passed valid_arg. Nothing else builds a step, which is what keeps the
        no-command-from-the-client rule true for sequences as well as buttons.
        """
        steps: list[Step] = []
        for name, arg in pairs:
            action = ACTIONS.get(name)
            if action is None:
                return [], "알 수 없는 동작입니다."
            ok, why = self.valid_arg(action, arg)
            if not ok:
                return [], why
            steps.append(Step(action=name, arg=arg,
                              argv=action.build(self.repo_root, arg),
                              timeout=action.timeout))
        if not steps:
            return [], "실행할 동작이 없습니다."
        return steps, ""

    def start(self, name: str, arg: str) -> tuple[Job | None, str]:
        return self.start_steps([(name, arg)])

    def start_steps(self, pairs: list[tuple[str, str]], *, title: str = "",
                    order_id: str = "") -> tuple[Job | None, str]:
        steps, why = self.plan(pairs)
        if not steps:
            return None, why

        with self.lock:
            if self.current is not None and not self.current.done:
                return None, (f"이미 '{ACTIONS[self.current.action].label}' 이 "
                              "실행 중입니다. 끝나면 다시 누르세요.")

            job = Job(id=secrets.token_hex(8), steps=steps,
                      title=title or ACTIONS[steps[0].action].label,
                      order_id=order_id)
            self.current = job

        thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        thread.start()
        return job, ""

    def _run(self, job: Job) -> None:
        for index, step in enumerate(job.steps):
            with job.lock:
                job.step_index = index
            if len(job.steps) > 1:
                job.append(
                    f"\n=== {index + 1}/{len(job.steps)} "
                    f"{ACTIONS[step.action].label} ===\n")

            code = self._run_step(job, step)
            if code != 0:
                # Stop the sequence. A Unity test run after a failed Codex run
                # would report on code that was never written, and a green
                # result there would be the most misleading thing this panel
                # could print.
                if index + 1 < len(job.steps):
                    job.append(
                        f"\n앞 단계가 실패했으므로 남은 단계"
                        f"({len(job.steps) - index - 1}개)는 실행하지 않았습니다.\n")
                self._finish(job, code)
                return

        self._finish(job, 0)

    def _finish(self, job: Job, code: int) -> None:
        with job.lock:
            job.done, job.exit_code = True, code
            # Frozen here so a finished run keeps reporting how long it took
            # instead of counting up forever while the page is still open.
            job.finished = time.monotonic()

    def _run_step(self, job: Job, step: Step) -> int:
        # git runs at the repo root; the orchestrator module runs from
        # AI_GAME_COMPANY, which is where its package lives.
        cwd = self.repo_root if step.argv[0] == "git" else self.company_root
        job.append(f"$ {' '.join(step.argv)}\n\n")

        try:
            # shell=False: argv is a list this file built, so nothing in a
            # request can turn into a second command.
            process = subprocess.Popen(
                step.argv, cwd=str(cwd), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                encoding="utf-8", errors="replace",
            )
        except OSError as exc:
            job.append(f"실행할 수 없습니다: {exc}\n")
            return -1

        def reap() -> None:
            try:
                process.wait(timeout=step.timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                job.append(f"\n{step.timeout}초를 넘겨 중단했습니다.\n")

        timer = threading.Thread(target=reap, daemon=True)
        timer.start()

        if process.stdout is not None:
            # closing() rather than a bare loop: a build can be killed by the
            # timeout above while this is mid-read, and the pipe would then be
            # left open for the life of the server.
            with process.stdout as stream:
                for line in stream:
                    job.append(line)
        return process.wait()


class Handler(BaseHTTPRequestHandler):
    server_version = "GameFactoryControl/1"
    runner: Runner
    token: str

    # ---- plumbing ----

    def log_message(self, fmt: str, *args: Any) -> None:
        """Quiet by default: the terminal is where job output belongs."""

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # This page inlines its own script and its images as data URIs, and
        # must never be framed by another site.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _local_host(self) -> bool:
        """Host header must name this loopback server, not a public name."""
        host = (self.headers.get("Host") or "").split(":")[0].strip("[]")
        return host in (LOOPBACK, "localhost", "::1")

    def _origin_ok(self) -> bool:
        """A cross-site POST is refused. Same-origin requests send no Origin
        or one matching us; a hostile page always sends its own."""
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return origin.split("://")[-1].split(":")[0].strip("[]") in (
            LOOPBACK, "localhost", "::1")

    # ---- routes ----

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's name
        if not self._local_host():
            self._json(400, {"error": "bad host"})
            return

        if self.path.startswith("/log"):
            self._log()
            return
        if self.path.startswith("/artifact"):
            self._artifact()
            return
        if self.path in ("/", "/index.html"):
            snapshot = dash.collect(self.runner.repo_root, live_ollama=True)
            # Read-only: the renderer is handed the current job so the page
            # agrees with itself about who is working. Nothing in a request
            # can set or clear it - only start() creates one.
            live = self.runner.current
            page = dash.render(snapshot, control_token=self.token,
                               live_job=live.snapshot() if live else None)
            self._send(200, dash.standalone(page).encode("utf-8"),
                       "text/html; charset=utf-8")
            return

        self._json(404, {"error": "not found"})

    def _log(self) -> None:
        job = self.runner.current
        wanted = ""
        if "?" in self.path:
            for pair in self.path.split("?", 1)[1].split("&"):
                key, _, value = pair.partition("=")
                if key == "job":
                    wanted = value
        if job is None or (wanted and wanted != job.id):
            self._json(404, {"error": "그 작업의 로그가 없습니다.", "done": True,
                             "exit_code": -1, "output": ""})
            return
        self._json(200, job.snapshot())

    def do_POST(self) -> None:  # noqa: N802
        if not self._local_host():
            self._discard_body()
            self._json(400, {"error": "bad host"})
            return
        if not self._origin_ok():
            self._discard_body()
            self._json(403, {"error": "cross-site 요청은 거부됩니다."})
            return
        if self.path not in ("/run", "/order", "/plan-game", "/create-game"):
            self._discard_body()
            self._json(404, {"error": "not found"})
            return

        payload = self._authorised_body()
        if payload is None:
            return

        if self.path == "/order":
            self._order(payload)
        elif self.path == "/plan-game":
            self._plan_game(payload)
        elif self.path == "/create-game":
            self._create_game(payload)
        else:
            self._run_action(payload)

    def _discard_body(self) -> None:
        """Drain a small rejected request so Windows sends the response instead of a TCP reset."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < length <= MAX_BODY + 1024:
            self.rfile.read(length)

    def _authorised_body(self) -> dict[str, Any] | None:
        """Parse and authorise a POST body, or answer the error and return None."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json(400, {"error": "bad length"})
            return None
        if length <= 0 or length > MAX_BODY:
            if length > 0:
                self.rfile.read(min(length, MAX_BODY + 1024))
            self._json(400, {"error": "bad body size"})
            return None

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"error": "bad json"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"error": "bad json"})
            return None

        # compare_digest, so a wrong token cannot be found one character at a
        # time by timing the responses.
        if not secrets.compare_digest(str(payload.get("token", "")), self.token):
            self._json(403, {"error": "토큰이 맞지 않습니다. 터미널에 찍힌 주소로 다시 여세요."})
            return None
        return payload

    def _run_action(self, payload: dict[str, Any]) -> None:
        job, why = self.runner.start(str(payload.get("action", "")),
                                     str(payload.get("arg", "")))
        if job is None:
            self._json(409, {"error": why})
            return

        print(f"  [실행] {' '.join(job.step.argv)}")
        self._json(200, {"job": job.id})

    def _plan_game(self, payload: dict[str, Any]) -> None:
        """Return a complete preview without writing a file or starting Unity."""
        try:
            plan = creation.plan_game(self.runner.repo_root, payload)
        except creation.GameCreationError as exc:
            self._json(409, {"error": str(exc)})
            return
        self._json(200, {"plan": plan.as_dict(), "saved": False})

    def _create_game(self, payload: dict[str, Any]) -> None:
        """Save a generated GameSpec, then run an allowlisted Unity pipeline."""
        if self.runner.busy():
            self._json(409, {"error": "지금 다른 작업이 실행 중입니다. 끝나면 다시 시도하세요."})
            return

        pipeline = payload.get("pipeline", "full")
        if not isinstance(pipeline, str) or pipeline not in ("spec", "test", "build", "full"):
            self._json(409, {"error": "지원하지 않는 자동화 단계입니다."})
            return

        try:
            plan = creation.create_game(self.runner.repo_root, payload)
        except creation.GameCreationError as exc:
            self._json(409, {"error": str(exc)})
            return

        pairs = {
            "spec": [],
            "test": [("test", plan.game_id)],
            "build": [("build", plan.game_id)],
            "full": [("test", plan.game_id), ("build", plan.game_id)],
        }[pipeline]
        response: dict[str, Any] = {
            "plan": plan.as_dict(),
            "saved": True,
            "spec_path": f"GameSpecs/{plan.game_id}.json",
            "steps": [ACTIONS[action].label for action, _ in pairs],
        }
        if not pairs:
            response.update(done=True, exit_code=0)
            self._json(200, response)
            return

        job, why = self.runner.start_steps(
            pairs,
            title=f"{plan.title} 자동 생성",
        )
        if job is None:
            response.update(error=why, note="GameSpec은 저장되었습니다.")
            self._json(409, response)
            return

        job.append(
            f"GameSpec created: GameSpecs/{plan.game_id}.json\n"
            f"Character locked: {creation.SHARED_CHARACTER}\n"
        )
        response.update(job.snapshot())
        print(f"  [게임 생성] {plan.game_id} · {plan.title} · {pipeline}")
        self._json(200, response)

    def _artifact(self) -> None:
        """Download the newest verified Android artifact for one safe game id."""
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        game = (query.get("game") or [""])[0]
        if not SAFE_ID.match(game):
            self._json(400, {"error": "올바르지 않은 게임 id입니다."})
            return
        build_dir = self.runner.repo_root / "Builds" / game
        if not build_dir.is_dir():
            self._json(404, {"error": "빌드 파일이 없습니다."})
            return
        files = [
            path for pattern in ("**/*.apk", "**/*.aab")
            for path in build_dir.glob(pattern)
            if path.is_file() and path.stat().st_size > 0
        ]
        if not files:
            self._json(404, {"error": "빌드 파일이 없습니다."})
            return
        artifact = max(files, key=lambda path: path.stat().st_mtime)
        body = artifact.read_bytes()
        content_type = (
            "application/vnd.android.package-archive"
            if artifact.suffix.lower() == ".apk" else "application/octet-stream"
        )
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'attachment; filename="{artifact.name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _order(self, payload: dict[str, Any]) -> None:
        """Accept one typed instruction and put the AI to work on it.

        The order's TEXT becomes task data on the board. The order's DEPARTMENT
        selects an allowlist from a table in orders.py. Neither reaches argv:
        what runs is team-run with an id orders.py minted, then the Unity test.
        """
        # Rejected before anything is written, so a run that cannot start
        # leaves no half-placed order on the board.
        if self.runner.busy():
            self._json(409, {"error": "지금 다른 작업이 실행 중입니다. 끝나면 다시 보내세요."})
            return

        game = str(payload.get("game", "")).strip()
        verify = payload.get("verify", True) is not False

        try:
            placed = ordering.place_order(
                self.runner.board(),
                str(payload.get("department", "")),
                str(payload.get("text", "")),
            )
        except ordering.OrderRejected as exc:
            self._json(409, {"error": str(exc)})
            return

        pairs: list[tuple[str, str]] = [("team-run", placed.task.id)]
        if verify and game:
            pairs.append(("test", game))

        job, why = self.runner.start_steps(
            pairs,
            title=f"{placed.dept.label} · {placed.task.title_ko}",
            order_id=placed.task.id,
        )
        if job is None:
            # The task stays on the board as todo. It is a real, valid order
            # that simply could not start now, and deleting it would throw
            # away what the user typed.
            self._json(409, {
                "error": why,
                "order": placed.task.id,
                "note": f"지시는 작업판에 {placed.task.id} 로 남겨뒀습니다.",
            })
            return

        print(f"  [주문] {placed.task.id} → {placed.dept.label}: {placed.task.title_ko}")
        self._json(200, {
            "job": job.id,
            "order": placed.task.id,
            "department": placed.dept.id,
            "department_label": placed.dept.label,
            "steps": [ACTIONS[a].label for a, _ in pairs],
            "duplicate_of": placed.duplicate_of,
        })


def make_handler(runner: Runner, token: str) -> type[Handler]:
    """Bind a runner and token to a handler class.

    BaseHTTPRequestHandler is instantiated per request by the server, so
    per-server state has to live on the class. Split out from serve() so a
    test can stand up a real server without serve()'s printing and blocking.
    """
    return type("BoundHandler", (Handler,), {"runner": runner, "token": token})


def serve(repo_root: Path, company_root: Path, port: int = 8765) -> int:
    """Run the control panel until interrupted. Returns a process exit code."""
    token = secrets.token_urlsafe(24)
    handler = make_handler(Runner(repo_root, company_root), token)

    try:
        # ThreadingHTTPServer: a job polls /log while it runs, so a
        # single-threaded server would block on its own long request.
        httpd = ThreadingHTTPServer((LOOPBACK, port), handler)
    except OSError as exc:
        print(f"ERROR: {LOOPBACK}:{port} 를 열 수 없습니다 - {exc}")
        print("  이미 실행 중이거나 다른 프로그램이 쓰고 있습니다. --port 로 바꿔보세요.")
        return 2

    url = f"http://{LOOPBACK}:{port}/"
    print("=== GAME FACTORY 제어판 ===")
    print(f"  {url}")
    print(f"  이 주소는 이 PC에서만 열립니다 ({LOOPBACK} 전용).")
    print("  실행 가능한 동작: " + ", ".join(ACTIONS))
    print("  커밋과 푸시는 하지 않습니다. Ctrl+C 로 종료합니다.\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        httpd.server_close()
    return 0
