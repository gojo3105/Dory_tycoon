"""Tests for the AI status dashboard.

Run:  python3 AI_GAME_COMPANY/tests/test_dashboard.py

The rule these exist to protect is the one the page is built around: absence
of evidence renders as "확인 불가", never as OK. A dashboard that upgrades
"not checked" to "fine" is worse than no dashboard, so most of what follows
is about what happens when a file is missing, empty or says UNKNOWN.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import dashboard as dash  # noqa: E402
from company.orchestrator import progress as prog  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
COMPANY_CONFIG = REPO / "AI_GAME_COMPANY" / "config"

BUILD_REPORT = """\
# Build status report

Generated: 2026-08-27 21:36:25
Findings-Hash: 4DFA10A6F3F05F57

## game01

- [CiWorkspacePath] Builds\\game01\\APK\\Game01_FactoryRunner_v1.0.apk  (17.24 MB, sha256:6F0E78431668FD15, built 2026-08-27 21:15:14)
"""

ERROR_REPORT = """\
# Unity error report

Generated: 2026-08-29 22:00:28

## Compile errors (3)
## Obsolete API warnings (CS0618) (0)
## Runtime exceptions (1)
"""


def profile(**overrides):
    base = {
        "machineName": "TEST-PC",
        "hardware": {"ramTotalGb": 15.71},
        "tools": {
            "claude": {"installed": True, "version": "2.1.247"},
            "codex": {"installed": True, "version": "codex-cli 0.151.0"},
            "ollama": {"installed": True, "version": "0.33.2"},
            "blender": {"installed": True, "version": "Blender 5.2.1 LTS"},
        },
        "ollamaApi": {"reachable": True, "models": []},
        "paidApiKeysPresent": {},
        "unity": {"requiredByProject": "6000.5.9f1", "status": "OK"},
    }
    base.update(overrides)
    return base


def policy(**overrides):
    base = {
        "use_claude_code": True,
        "use_codex_subscription": True,
        "allow_codex_write": True,
        "allow_gemini_design": True,
        "gemini_api_key_env": "TEST_GEMINI_API_KEY",
        "allow_local_image_generation": True,
        "human_gates": ["initial_codex_login", "initial_gemini_login"],
    }
    base.update(overrides)
    return base


def find(agents, prefix):
    for agent in agents:
        if agent.name.startswith(prefix):
            return agent
    raise AssertionError(f"no agent named {prefix!r} in {[a.name for a in agents]}")


class ReportParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_the_real_build_line(self):
        path = self.dir / "build.txt"
        path.write_text(BUILD_REPORT, encoding="utf-8")
        builds, when = dash.read_builds(path)

        self.assertEqual(1, len(builds))
        self.assertEqual("game01", builds[0]["game"])
        self.assertEqual("17.24 MB", builds[0]["size"])
        self.assertEqual("6F0E78431668FD15", builds[0]["sha"])
        self.assertEqual("2026-08-27 21:36:25", when)

    def test_a_missing_build_report_is_no_builds_not_a_crash(self):
        builds, when = dash.read_builds(self.dir / "nope.txt")
        self.assertEqual([], builds)
        self.assertEqual("", when)

    def test_reads_error_counts_including_zero(self):
        path = self.dir / "errors.txt"
        path.write_text(ERROR_REPORT, encoding="utf-8")
        counts, when = dash.read_errors(path)

        self.assertEqual({"compile": 3, "obsolete": 0, "runtime": 1}, counts)
        self.assertEqual("2026-08-29 22:00:28", when)

    def test_a_missing_error_report_yields_no_counts(self):
        # Not zeros: zero compile errors is a finding, and a missing report is
        # not that finding.
        counts, _ = dash.read_errors(self.dir / "nope.txt")
        self.assertEqual({}, counts)


class AgentStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.company = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def agents(self, prof=None, pol=None, licences=None):
        return dash.build_agents(
            profile() if prof is None else prof,
            policy() if pol is None else pol,
            licences or {}, self.company)

    def test_no_hardware_report_means_unknown_not_ready(self):
        agents = self.agents(prof={})
        self.assertEqual(dash.UNKNOWN, find(agents, "Claude Code").state)
        self.assertEqual([], find(agents, "Claude Code").evidence,
                         "a row with no evidence must not cite any")

    def test_claude_is_ready_when_installed_and_allowed(self):
        agents = self.agents()
        claude = find(agents, "Claude Code")
        self.assertEqual(dash.READY, claude.state)
        self.assertIn("config/HARDWARE_PROFILE.json", claude.evidence)

    def test_codex_is_gated_on_login_even_when_everything_else_passes(self):
        # Login cannot be inferred here: ~/.codex/auth.json is on the policy's
        # secrets_never_touched list and initial_codex_login is a HUMAN_GATE.
        # "Installed and allowed" is therefore never "ready".
        codex = find(self.agents(), "Codex CLI")
        self.assertEqual(dash.GATED, codex.state)
        self.assertIn("doctor", codex.detail)

    def test_codex_without_write_policy_says_review_only(self):
        codex = find(self.agents(pol=policy(allow_codex_write=False)), "Codex CLI")
        self.assertIn("리뷰", codex.role)
        self.assertIn("allow_codex_write", codex.detail)

    def test_gemini_is_blocked_when_design_policy_is_not_true(self):
        with mock.patch.dict("os.environ", {"TEST_GEMINI_API_KEY": "secret"}, clear=True):
            gemini = find(self.agents(pol=policy(allow_gemini_design=False)), "Gemini")
        self.assertEqual(dash.BLOCKED, gemini.state)
        self.assertIn("allow_gemini_design", gemini.detail)
        self.assertNotIn("secret", gemini.detail)

    def test_gemini_is_gated_by_initial_login_without_a_key(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            gemini = find(self.agents(), "Gemini")
        self.assertEqual(dash.GATED, gemini.state)
        self.assertIn("initial_gemini_login", gemini.detail)

    def test_gemini_is_ready_when_the_policy_named_key_is_present(self):
        with mock.patch.dict("os.environ", {"TEST_GEMINI_API_KEY": "do-not-print"}, clear=True):
            gemini = find(self.agents(), "Gemini")
        self.assertEqual(dash.READY, gemini.state)
        self.assertIn("TEST_GEMINI_API_KEY", gemini.detail)
        self.assertNotIn("do-not-print", gemini.detail)
        self.assertEqual(["config/company_policy.json"], gemini.evidence)

    def test_a_model_that_fails_licence_and_ram_reports_both(self):
        # Naming only the first failure hides the second, and someone would
        # then get the licence approved and still be unable to load it.
        prof = profile(ollamaApi={"reachable": True, "models": [
            {"name": "gemma4:26b", "sizeGb": 17.33,
             "parameterSize": "25.2B", "quantization": "Q4_K_M"}]})
        agent = find(self.agents(prof=prof), "Ollama · gemma4")
        self.assertEqual(dash.BLOCKED, agent.state)
        self.assertIn("라이선스 UNKNOWN", agent.detail)
        self.assertIn("적재 불가", agent.detail)

    def test_an_approved_model_that_fits_is_ready(self):
        prof = profile(ollamaApi={"reachable": True, "models": [
            {"name": "llama3.2:3b", "sizeGb": 2.0,
             "parameterSize": "3.2B", "quantization": "Q4_K_M"}]})
        agent = find(self.agents(prof=prof, licences={"llama3.2:3b": "APPROVED"}),
                     "Ollama · llama3.2")
        self.assertEqual(dash.READY, agent.state)

    def test_no_installed_model_is_gated_not_ready(self):
        agent = find(self.agents(), "Ollama (로컬 LLM)")
        self.assertEqual(dash.GATED, agent.state)

    def test_image_generation_is_gated_until_something_was_produced(self):
        agent = find(self.agents(licences={"stable-diffusion-v1-5": "APPROVED"}),
                     "Stable Diffusion")
        self.assertEqual(dash.GATED, agent.state)

        # An approved licence and a permissive policy are not output. A PNG is.
        (self.company / "generated" / "dori").mkdir(parents=True)
        (self.company / "generated" / "dori" / "a.png").write_bytes(b"x")
        agent = find(self.agents(licences={"stable-diffusion-v1-5": "APPROVED"}),
                     "Stable Diffusion")
        self.assertEqual(dash.READY, agent.state)

    def test_image_generation_blocked_by_policy_beats_everything(self):
        (self.company / "generated").mkdir(parents=True)
        (self.company / "generated" / "a.png").write_bytes(b"x")
        agent = find(self.agents(pol=policy(allow_local_image_generation=False)),
                     "Stable Diffusion")
        self.assertEqual(dash.BLOCKED, agent.state)

    def test_an_approved_but_unusable_model_is_blocked(self):
        agent = find(self.agents(licences={"qwen-image": "APPROVED_BUT_UNUSABLE_HERE"}),
                     "Qwen-Image")
        self.assertEqual(dash.BLOCKED, agent.state)
        self.assertIn("15.7", agent.detail)

    def test_blender_installed_without_an_adapter_is_unknown(self):
        agent = find(self.agents(), "Blender")
        self.assertEqual(dash.UNKNOWN, agent.state)
        self.assertIn("blender_runner.py", agent.detail)

    def test_blender_with_an_adapter_is_ready(self):
        (self.company / "company" / "orchestrator").mkdir(parents=True)
        (self.company / "company" / "orchestrator" / "blender_runner.py").write_text("x")
        self.assertEqual(dash.READY, find(self.agents(), "Blender").state)

    def test_paid_apis_are_always_blocked_and_keys_are_named_not_printed(self):
        prof = profile(paidApiKeysPresent={"OPENAI_API_KEY": True,
                                           "GOOGLE_API_KEY": False})
        agent = find(self.agents(prof=prof), "유료 API")
        self.assertEqual(dash.BLOCKED, agent.state)
        self.assertIn("OPENAI_API_KEY", agent.detail)
        self.assertNotIn("GOOGLE_API_KEY", agent.detail)


class GameProgressTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        (self.repo / "GameSpecs").mkdir()
        (self.repo / "Reports").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_ten_cells_regardless_of_what_exists(self):
        self.assertEqual(10, len(dash.read_games(self.repo, [])))

    def test_a_spec_alone_is_active_not_done(self):
        (self.repo / "GameSpecs" / "game01.json").write_text("{}")
        games = dash.read_games(self.repo, [])
        self.assertEqual("active", games[0]["state"])

    def test_a_report_without_an_apk_is_not_done(self):
        # Section 38: the report is a claim. The APK is the fact.
        (self.repo / "GameSpecs" / "game01.json").write_text("{}")
        (self.repo / "Reports" / "Game01_Report.txt").write_text("done!")
        self.assertEqual("active", dash.read_games(self.repo, [])[0]["state"])

    def test_a_report_and_a_real_apk_is_done(self):
        (self.repo / "GameSpecs" / "game01.json").write_text("{}")
        (self.repo / "Reports" / "Game01_Report.txt").write_text("done!")
        games = dash.read_games(self.repo, [{"game": "game01"}])
        self.assertEqual("done", games[0]["state"])
        self.assertEqual("todo", games[1]["state"])


class RenderTests(unittest.TestCase):
    def task_page(self, tasks, served=True, live_job=None):
        snapshot = dash.collect(REPO)
        snapshot.tasks = tasks
        return dash.render(snapshot,
                           control_token="test-token" if served else None,
                           live_job=live_job)

    @staticmethod
    def section(page, heading):
        """One <section> of the page, by its heading.

        The board and the queue both render task buttons, so a count over the
        whole page stopped answering "how many buttons does the BOARD have".
        """
        after = page.split(f"<h2>{heading}</h2>", 1)[1]
        return after.split("</section>", 1)[0]

    def test_served_board_is_a_read_only_audit_view(self):
        page = self.task_page([
            {"id": "C-TODO", "title": "todo", "owner": "codex", "status": "todo"},
            {"id": "C-WIP", "title": "wip", "owner": "codex", "status": "in_progress"},
            {"id": "C-BLOCKED", "title": "blocked", "owner": "codex", "status": "blocked"},
            {"id": "C-REVIEW", "title": "review", "owner": "codex", "status": "review"},
            {"id": "C-DONE", "title": "done", "owner": "codex", "status": "done"},
            {"id": "CLAUDE-TODO", "title": "claude", "owner": "claude", "status": "todo"},
        ])

        board = self.section(page, "공유 작업판")
        for task_id in ("C-TODO", "C-WIP", "C-BLOCKED", "C-REVIEW",
                        "C-DONE", "CLAUDE-TODO"):
            self.assertIn(task_id, board)
        self.assertNotIn('class="btn task-run"', board)
        self.assertNotIn('data-act="team-run"', board)

    def test_the_queue_is_the_only_task_execution_surface(self):
        page = self.task_page([
            {"id": "C-TODO", "title": "todo", "owner": "codex", "status": "todo"},
        ])
        queue = self.section(page, "작업 대기열")
        board = self.section(page, "공유 작업판")
        self.assertIn('data-arg-value="C-TODO"', queue)
        self.assertNotIn('data-arg-value="C-TODO"', board)

    def test_static_board_has_no_task_start_buttons(self):
        page = self.task_page([
            {"id": "C-TODO", "title": "todo", "owner": "codex", "status": "todo"},
        ], served=False)

        self.assertNotIn('class="btn task-run"', page)
        self.assertNotIn('data-act="team-run"', page)

    def test_unmet_dependency_disables_button_and_names_blocker(self):
        page = self.task_page([
            {"id": "FIRST", "title": "first", "owner": "claude", "status": "todo"},
            {"id": "SECOND", "title": "second", "owner": "codex", "status": "todo",
             "depends_on": ["FIRST"]},
        ])

        button = page.split('data-arg-value="SECOND"', 1)[1].split("</button>", 1)[0]
        self.assertIn("disabled", button)
        self.assertIn('data-blocked="true"', button)
        self.assertIn("선행 작업: <code>FIRST</code>", page)

    def test_every_agent_state_has_a_distinct_office_visual_class(self):
        declarations = []
        for state in dash.STATE_LABEL:
            match = __import__("re").search(
                rf"\.office-agent--{state}\{{([^}}]+)\}}", dash.CSS)
            self.assertIsNotNone(match, f"office CSS has no visual class for {state}")
            declarations.append(match.group(1).strip())
        self.assertEqual(len(declarations), len(set(declarations)),
                         "two agent states render identically in the office")

    def test_an_unmapped_agent_is_rendered_in_the_etc_department(self):
        snapshot = dash.collect(REPO)
        snapshot.company_roles = []
        snapshot.agents.append(dash.Agent(
            "Future Assistant", "새 역할", dash.UNKNOWN, "아직 매핑되지 않음"))
        html_out = dash.render(snapshot)
        self.assertIn("Future Assistant", html_out)
        self.assertIn("기타", html_out)
        self.assertIn("office-agent--etc", html_out)

    def test_virtual_office_agents_walk_when_idle(self):
        agents = [dash.Agent("Claude Code", "개발", dash.READY, "사용 가능")]
        html_out = dash._virtual_office_html(agents, "data:image/jpeg;base64,test")
        self.assertIn("office-avatar--walking", html_out)
        self.assertIn('data-agent="Claude Code"', html_out)
        self.assertIn("office-avatar-person", html_out)
        self.assertNotIn("<circle", html_out)

    def test_virtual_office_agent_sits_at_a_desk_while_working(self):
        agents = [dash.Agent("Codex CLI", "개발", dash.GATED, "로그인 필요")]
        html_out = dash._virtual_office_html(
            agents, "data:image/jpeg;base64,test", working_prefix="Codex")
        self.assertIn("office-avatar--working", html_out)
        self.assertIn("office-avatar-workstation", html_out)
        self.assertNotIn("office-avatar--walking", html_out)

    def test_collect_loads_the_twelve_company_roles(self):
        snapshot = dash.collect(REPO)
        self.assertEqual(12, len(snapshot.company_roles))
        self.assertEqual("ceo", snapshot.company_roles[0].id)
        self.assertEqual("빌드출시부", snapshot.company_roles[-1].department)

    def test_company_runtime_uses_taskboard_status_and_dependencies(self):
        roles = dash.collect(REPO).company_roles
        tasks = [
            {"id": "DESIGN", "agent_role": "game_director", "status": "todo",
             "title_ko": "게임 설계", "depends_on": ["CEO"]},
            {"id": "CEO", "agent_role": "ceo", "status": "done",
             "title_ko": "목표 확정", "depends_on": []},
            {"id": "QA", "agent_role": "qa_engineer", "status": "todo",
             "title_ko": "검증", "depends_on": ["CODE"]},
        ]
        runtime = {item["role"].id: item for item in
                   dash._company_role_runtime(roles, tasks)}
        self.assertEqual("DONE", runtime["ceo"]["state"])
        self.assertEqual("PLANNING", runtime["game_director"]["state"])
        self.assertEqual("WAITING", runtime["qa_engineer"]["state"])
        self.assertEqual("IDLE", runtime["release_engineer"]["state"])

    def test_company_office_has_twelve_real_role_avatars(self):
        roles = dash.collect(REPO).company_roles
        html_out = dash._company_office_html(
            roles, [], "data:image/jpeg;base64,test")
        self.assertEqual(12, html_out.count('class="office-avatar '))
        self.assertIn("CEO", html_out)
        self.assertIn("Build / Release Engineer", html_out)
        self.assertIn("배정된 작업 없음", html_out)
        self.assertIn('class="company-detail-fold"', html_out)

    def test_company_avatars_are_anchored_to_the_three_room_floors(self):
        roles = dash.collect(REPO).company_roles
        html_out = dash._company_office_html(
            roles, [], "data:image/jpeg;base64,test")
        self.assertEqual(4, html_out.count("--from-y:34.0%"))
        self.assertEqual(4, html_out.count("--from-y:58.4%"))
        self.assertEqual(4, html_out.count("--from-y:85.4%"))
        for start, end in ((17.5, 26.5), (36.5, 45.5),
                           (52.5, 61.5), (71.5, 80.5)):
            self.assertEqual(3, html_out.count(f"--from-x:{start:.1f}%"))
            self.assertEqual(3, html_out.count(f"--to-x:{end:.1f}%"))
        self.assertIn(".virtual-office-stage{position:relative; overflow:hidden;}",
                      dash.CSS)
        self.assertIn(".office-avatar{width:32px; height:40px", dash.CSS)
        self.assertNotIn("@keyframes office-avatar-step", dash.CSS)

    def test_shortens_a_path_to_its_filename(self):
        self.assertEqual("SceneGenerator.cs",
                         dash._short_path("Assets/GameFactory/Editor/SceneGenerator.cs"))
        self.assertEqual("UI/", dash._short_path("Assets/GameFactory/UI/"))
        self.assertEqual("SharedArtImporter.cs",
                         dash._short_path("Assets\\GameFactory\\Editor\\SharedArtImporter.cs"))

    def test_long_filenames_keep_their_distinguishing_tail(self):
        # Fifteen reference photos share a 24-character prefix; clipped from
        # the right they all render as the same caption.
        a = dash._short_name("KakaoTalk_20260826_014200658_09.png")
        b = dash._short_name("KakaoTalk_20260826_014200658_14.png")
        self.assertNotEqual(a, b)
        self.assertIn("09", a)
        self.assertIn("14", b)
        self.assertEqual("player.png", dash._short_name("player.png"),
                         "a short name should be left alone")

    def test_gallery_groups_are_present_even_when_empty(self):
        # The AI-generated group being empty is the useful fact, so it has to
        # render its reason rather than vanish.
        groups = dash.read_gallery(REPO)
        titles = [g["title"] for g in groups]
        self.assertEqual(3, len(groups))
        html_out = dash._gallery_html(groups)
        for group in groups:
            self.assertIn(group["title"], html_out)
            if not group["items"]:
                self.assertIn(group["empty"][:20], html_out)
        self.assertTrue(any("AI" in t for t in titles))

    def test_sprites_are_embedded_and_photos_are_thumbnailed(self):
        groups = {g["title"]: g for g in dash.read_gallery(REPO)}
        art = groups["게임에 들어간 아트"]["items"]
        if not art:
            self.skipTest("no art committed")
        # Inlined, so one HTML file works offline and as an Artifact.
        self.assertTrue(all(i["src"].startswith("data:") or i["note"] for i in art))
        for item in art:
            if item["src"]:
                self.assertTrue(item["src"].startswith(("data:image/png", "data:image/jpeg")),
                                f"{item['name']} is not an embedded image")

class ArtPlanTests(unittest.TestCase):
    """Why the gallery is shorter than the plan - three distinct reasons."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp.name)
        self.config = self.repo / "AI_GAME_COMPANY" / "config"
        self.config.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, mapping: dict) -> None:
        (self.config / "art-mapping.json").write_text(
            json.dumps(mapping), encoding="utf-8")

    def test_splits_targets_by_whether_the_file_is_actually_there(self):
        self.write({"targets": [
            {"target": "Assets/here.png", "packId": "p", "sourceFile": "a.png"},
            {"target": "Assets/gone.png", "packId": "p", "sourceFile": "b.png"},
        ]})
        (self.repo / "Assets").mkdir()
        (self.repo / "Assets" / "here.png").write_bytes(b"x")

        plan = dash.read_art_plan(self.repo)
        self.assertEqual(["Assets/here.png"], [x["path"] for x in plan["present"]])
        self.assertEqual(["Assets/gone.png"], [x["path"] for x in plan["missing"]])

    def test_a_missing_file_names_the_pack_it_should_come_from(self):
        # "It is missing" is not actionable; "it comes from this pack via this
        # script" is.
        self.write({"targets": [
            {"target": "Assets/x.png", "packId": "kenney-ui-pack",
             "sourceFile": "PNG/Blue/Default/button.png"}]})
        html_out = dash._art_plan_html(dash.read_art_plan(self.repo))
        self.assertIn("kenney-ui-pack", html_out)
        self.assertIn("apply-art-mapping.ps1", html_out)
        self.assertIn("-Commit", html_out)

    def test_queued_and_declined_carry_their_reason(self):
        self.write({
            "queued": [{"what": "parallax", "packId": "p", "why": "needs a component"}],
            "deliberatelyNotMapped": [{"target": "Assets/z.png", "why": "a tint is better"}],
        })
        plan = dash.read_art_plan(self.repo)
        html_out = dash._art_plan_html(plan)
        self.assertIn("needs a component", html_out)
        self.assertIn("a tint is better", html_out)
        # The three reasons must stay visually distinct - the fix for each is
        # different, so one shared badge would flatten them.
        self.assertIn("대기", html_out)
        self.assertIn("제외", html_out)

    def test_no_mapping_file_says_so_instead_of_showing_nothing(self):
        plan = dash.read_art_plan(self.repo)
        self.assertEqual("", plan["source"])
        self.assertIn("읽지 못했습니다", dash._art_plan_html(plan))

    def test_the_committed_mapping_has_no_target_without_a_reason(self):
        path = COMPANY_CONFIG / "art-mapping.json"
        if not path.is_file():
            self.skipTest("no mapping committed")
        mapping = json.loads(path.read_text(encoding="utf-8-sig"))
        for target in mapping.get("targets", []):
            self.assertTrue(target.get("why"), target.get("target"))
            self.assertTrue(target.get("packId"), target.get("target"))
        for item in mapping.get("queued", []) + mapping.get("deliberatelyNotMapped", []):
            self.assertTrue(item.get("why"), item)

    def test_the_real_repository_reports_the_background_as_queued(self):
        # The question this section exists to answer. If the background ever
        # stops being reported, either it shipped or the plan lost it - and
        # both should be a deliberate edit, not a silent one.
        plan = dash.read_art_plan(REPO)
        if not plan["source"]:
            self.skipTest("no mapping committed")
        queued = " ".join(x["what"] + x["why"] for x in plan["queued"])
        present = " ".join(x["path"] for x in plan["present"])
        self.assertTrue("background" in queued.lower() or "background" in present.lower(),
                        "background art is neither shipped nor queued")


    def test_task_titles_are_escaped(self):
        # Board content is hand-edited JSON and Codex can be asked to touch
        # it; it reaches the page as text, never as markup.
        row = dash._task_row({"id": "X", "title": "<script>alert(1)</script>",
                              "status": "todo", "files": []})
        self.assertNotIn("<script>", row)
        self.assertIn("&lt;script&gt;", row)

    def test_renders_the_real_repository(self):
        snapshot = dash.collect(REPO)
        page = dash.render(snapshot)

        self.assertIn("<title>", page)
        self.assertIn("Claude Code", page)
        self.assertIn("Codex CLI", page)
        # Every state token used by the roster needs its CSS class defined,
        # or a row renders with no severity colour at all.
        for agent in snapshot.agents:
            self.assertIn(f".s-{agent.state}{{", dash.CSS.replace(" ", ""))

    def test_every_theme_token_is_defined_in_bare_root(self):
        # The classic unreadable-artifact bug: a colour whose only definition
        # sits inside a media query never applies in the un-stamped state.
        bare = dash.CSS.split("@media", 1)[0]
        used = set(dash.__dict__ and __import__("re").findall(r"var\((--[\w-]+)\)", dash.CSS))
        for token in used:
            self.assertIn(f"{token}:", bare, f"{token} is never defined on bare :root")

    def test_missing_files_are_listed_not_silently_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = dash.collect(Path(tmp))
            self.assertIn("config/HARDWARE_PROFILE.json", snapshot.missing)
            self.assertIn("근거 없음", dash.render(snapshot))

    def test_the_written_file_declares_utf8(self):
        # Every label is Korean. A browser handed this file with no declared
        # encoding falls back to Latin-1 and renders the page as mojibake -
        # which is exactly what happened when it was served over plain HTTP.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "dashboard.html"
            dash.write(REPO, out)
            head = out.read_text(encoding="utf-8")[:400]
            self.assertIn("<!doctype html>", head.lower())
            self.assertIn('<meta charset="utf-8">', head)
            self.assertIn("viewport", head)

    def test_render_alone_has_no_doctype(self):
        # The Artifact publisher supplies the document skeleton, so render()
        # must not emit a second one.
        page = dash.render(dash.collect(REPO))
        self.assertNotIn("<!doctype", page.lower())
        self.assertNotIn("<html", page.lower())

    def test_write_produces_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "sub" / "dashboard.html"
            self.assertEqual(out, dash.write(REPO, out))
            self.assertGreater(out.stat().st_size, 4000)


class RunLogReaderTests(unittest.TestCase):
    """Reports/runs/latest.txt in both formats that have existed on the PC."""

    def write(self, text):
        self.tmp = tempfile.TemporaryDirectory()
        path = Path(self.tmp.name) / "latest.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def tearDown(self):
        if hasattr(self, "tmp"):
            self.tmp.cleanup()

    def test_reads_the_current_format(self):
        fields = dash.read_header_fields(self.write(
            "# Orchestrator run report\n\nGenerated: 2026-09-05 10:00:00\n"
            "Command: team run --task X\nOutcome: FAILED\nExit: 4\nDuration: 3분 2초\n"
            "\n## Output (tail)\n\nExit: 0 (body, must be ignored)\n"))
        self.assertEqual("FAILED", fields["Outcome"])
        self.assertEqual("4", fields["Exit"])

    def test_reads_the_first_writers_format_from_the_pc(self):
        # The shape Codex's runlog.py produced on 2026-09-03: fields under a
        # '## Result' heading, Status/Exit code instead of Outcome/Exit.
        fields = dash.read_header_fields(self.write(
            "# Orchestrator run report\n\nGenerated by runlog.py\n\n"
            "Generated: 2026-09-03 00:47:24 UTC\n\n## Result\n\nStatus: FAILURE\n"
            "Command: orchestrator team run --task CODEX-ANIM1\nExit code: 1\n"
            "Duration: 215.547 seconds\nException: UnicodeEncodeError: cp949\n\n"
            "## Standard output tail\n\n    Status: SUCCESS (body, must be ignored)\n"))
        self.assertEqual("FAILED", fields["Outcome"])
        self.assertEqual("1", fields["Exit"])
        self.assertIn("CODEX-ANIM1", fields["Command"])

    def test_a_failed_run_renders_red_with_its_command(self):
        snapshot = dash.collect(REPO)
        snapshot.last_run = {"Outcome": "FAILED", "Command": "team run --task X",
                             "Generated": "2026-09-05 10:00:00", "Exit": "1"}
        page = dash.render(snapshot)
        self.assertIn("실패", page)
        self.assertIn("team run --task X", page)
        self.assertIn("붙여넣을 필요가 없습니다", page)


class QueueTests(unittest.TestCase):
    """The 작업 대기열 section: who is working, and what is next in line.

    Its job is different from the board's. The board lists everything that
    exists; this answers "what happens next", so anything finished or waiting
    on a human has no business in it.
    """

    TASKS = [
        {"id": "C-RUN", "title": "running one", "owner": "codex", "status": "in_progress"},
        {"id": "C-NEXT", "title": "next one", "owner": "codex", "status": "todo"},
        {"id": "C-HELD", "title": "held one", "owner": "codex", "status": "todo",
         "depends_on": ["C-NEXT"]},
        {"id": "C-REVIEW", "title": "reviewed one", "owner": "codex", "status": "review"},
        {"id": "C-DONE", "title": "finished one", "owner": "codex", "status": "done"},
        {"id": "CL-TODO", "title": "claude one", "owner": "claude", "status": "todo"},
    ]

    def page(self, served=True, live_job=None, tasks=None):
        snapshot = dash.collect(REPO)
        snapshot.tasks = list(self.TASKS if tasks is None else tasks)
        return dash.render(snapshot,
                           control_token="tok" if served else None,
                           live_job=live_job)

    def queue(self, **kwargs):
        return RenderTests.section(self.page(**kwargs), "작업 대기열")

    def test_finished_and_review_work_is_not_in_the_queue(self):
        queue = self.queue()
        self.assertIn("C-NEXT", queue)
        self.assertNotIn("C-DONE", queue)
        self.assertNotIn("C-REVIEW", queue)

    def test_each_owner_gets_its_own_lane(self):
        queue = self.queue()
        self.assertIn(">Codex<", queue)
        self.assertIn(">Claude<", queue)
        self.assertIn("CL-TODO", queue)

    def test_a_dependency_blocked_task_is_separated_and_names_its_blocker(self):
        queue = self.queue()
        held = queue.split('data-arg-value="C-HELD"', 1)[1].split("</button>", 1)[0]
        self.assertIn("disabled", held)
        self.assertIn("선행 작업: <code>C-NEXT</code>", queue)
        self.assertIn("선행 대기 1", queue)
        # And it sorts below the runnable one rather than sitting in its place.
        self.assertLess(queue.index("C-NEXT"), queue.index("C-HELD"))

    def test_the_static_queue_lists_work_but_offers_no_buttons(self):
        queue = self.queue(served=False)
        self.assertIn("C-NEXT", queue)
        self.assertNotIn("data-act=", queue)

    def test_an_owner_with_no_queued_work_gets_no_lane(self):
        queue = self.queue(tasks=[
            {"id": "C-DONE", "title": "done", "owner": "codex", "status": "done"}])
        self.assertIn("대기 중인 작업이 없습니다", queue)

    def test_an_unknown_owner_still_appears(self):
        queue = self.queue(tasks=[
            {"id": "X-1", "title": "stray", "owner": "gemini", "status": "todo"}])
        self.assertIn("X-1", queue)

    # ---- the live job ----

    @staticmethod
    def running(action="team-run", arg="C-RUN", output="running codex exec",
                seconds=125.0, done=False, exit_code=None):
        summary = prog.summarise(action, output, seconds, done=done,
                                 exit_code=exit_code)
        return {"job": "j1", "action": action, "arg": arg, "output": output,
                "done": done, "exit_code": exit_code,
                "progress": summary.as_dict()}

    def test_a_running_task_shows_its_korean_phase_and_elapsed_time(self):
        queue = self.queue(live_job=self.running())
        self.assertIn("Codex가 코드를 작성하는 중", queue)
        self.assertIn("2분 5초 경과", queue)
        self.assertIn("진행 중 1", queue)

    def test_the_phase_is_shown_with_the_line_it_was_read_from(self):
        queue = self.queue(live_job=self.running(
            output="=== HANDING C-RUN TO CODEX ===\n"))
        self.assertIn("작업 명세를 읽고 Codex에게 넘기는 중", queue)
        # The evidence line, so the summary can be checked rather than trusted.
        self.assertIn("=== HANDING C-RUN TO CODEX ===", queue)

    def test_a_running_task_offers_no_start_button(self):
        queue = self.queue(live_job=self.running())
        self.assertNotIn('data-arg-value="C-RUN"', queue)
        self.assertIn('data-arg-value="C-NEXT"', queue)

    def test_the_static_page_never_shows_a_live_job(self):
        # It has no server behind it, so it has no live state to read. Faking
        # one there would be the same lie as a button that cannot post.
        page = self.page(served=False, live_job=self.running())
        self.assertNotIn('<div class="live', page)

    def test_a_board_entry_left_in_progress_by_a_dead_run_says_so(self):
        # in_progress on the board with nothing running is a stale record, and
        # calling it "진행 중" would be the exact false claim that cost a day.
        queue = self.queue(live_job=None)
        self.assertIn("기록만 진행 중", queue)


class LiveAgentTests(unittest.TestCase):
    """The office view must not contradict the panel above it."""

    def page(self, live_job=None, served=True):
        snapshot = dash.collect(REPO)
        return dash.render(snapshot, control_token="tok" if served else None,
                           live_job=live_job)

    def running_codex(self, done=False, exit_code=None):
        summary = prog.summarise("team-run", "running codex exec", 30.0,
                                 done=done, exit_code=exit_code)
        return {"job": "j", "action": "team-run", "arg": "C-RUN",
                "output": "running codex exec", "done": done,
                "exit_code": exit_code, "progress": summary.as_dict()}

    def test_an_unassigned_legacy_codex_run_does_not_fake_a_company_role(self):
        page = self.page(live_job=self.running_codex())
        self.assertNotIn('office-agent--working"', page)
        self.assertIn("Codex CLI", page)

    def test_a_finished_job_stops_claiming_the_agent_is_working(self):
        page = self.page(live_job=self.running_codex(done=True, exit_code=0))
        self.assertNotIn('office-agent--working"', page)

    def test_without_a_job_the_company_roles_report_their_real_idle_state(self):
        page = self.page(live_job=None)
        self.assertNotIn('office-agent--working"', page)
        self.assertIn("IDLE · 대기", page)
        self.assertIn("배정된 작업 없음", page)

    def test_the_four_roster_labels_are_unchanged(self):
        # The caption is the office character's; the roster keeps the plain
        # four so the two are not quietly forked.
        self.assertEqual("대기 중", dash.STATE_LABEL[dash.GATED])


class PcLinkTests(unittest.TestCase):
    """The 'PC 연결' section: the two files by which the PC reaches Claude.

    Both readers follow the page's founding rule - a missing file is 확인 불가,
    never a clean bill of health - and a stale sync is called out, because a
    scheduled task that quietly stopped is exactly how a day got lost.
    """

    SYNC_BLOCKED = (
        "# Auto-sync status\n\nGenerated: 2026-09-03 12:00:00\nOutcome: BLOCKED\n"
        "Reason: Uncommitted changes to tracked files:  M Packages/packages-lock.json\n"
        "Last-Success: 2026-09-03 08:25:10\nLocal-Head: 9ec5c9d\nUpstream-Head: f198460\n"
        "Branch: claude/x\n\nWritten by scripts/desktop/sync-and-run.ps1 on every run.\n"
    )
    RUN_FAILED = (
        "# Orchestrator run report\n\nGenerated: 2026-09-03 12:34:56\n"
        "Command: team run --task CODEX-X\nOutcome: FAILED\nExit: 4\nDuration: 12분 3초\n"
        "\n## Output (tail)\n\nOutcome: OK (this is body text and must be ignored)\n"
    )

    def page(self, sync=None, run=None, served=False):
        snapshot = dash.collect(REPO)
        snapshot.sync_status = dash.read_header_fields(self.write(sync)) if sync else {}
        snapshot.last_run = dash.read_header_fields(self.write(run)) if run else {}
        return RenderTests.section(
            dash.render(snapshot, control_token="tok" if served else None), "PC 연결")

    def write(self, text):
        self._tmp = getattr(self, "_tmp", None) or tempfile.TemporaryDirectory()
        path = Path(self._tmp.name) / f"{abs(hash(text))}.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def tearDown(self):
        tmp = getattr(self, "_tmp", None)
        if tmp:
            tmp.cleanup()

    def test_missing_files_render_as_unknown_not_ok(self):
        section = self.page()
        sync_panel = section.split("PC 자동 동기화", 1)[1].split("마지막 오케스트레이터 실행", 1)[0]
        run_panel = section.split("마지막 오케스트레이터 실행", 1)[1]
        self.assertIn("확인 불가", sync_panel)
        self.assertIn("확인 불가", run_panel)
        self.assertNotIn("동기화됨", section)
        self.assertNotIn(">성공<", section)

    def test_a_blocked_sync_shows_the_reason(self):
        section = self.page(sync=self.SYNC_BLOCKED)
        self.assertIn("머지 보류", section)
        self.assertIn("packages-lock.json", section)
        self.assertIn("2026-09-03 08:25:10", section)   # last success
        self.assertIn("· 다름", section)                 # heads differ

    def test_a_failed_run_shows_command_and_exit_code(self):
        section = self.page(run=self.RUN_FAILED)
        self.assertIn(">실패<", section)
        self.assertIn("team run --task CODEX-X", section)
        self.assertIn("종료 코드 4", section)
        self.assertIn("Reports/runs/latest.txt", section)

    def test_the_header_parser_stops_at_the_body(self):
        fields = dash.read_header_fields(self.write(self.RUN_FAILED))
        self.assertEqual("FAILED", fields["Outcome"])
        self.assertEqual("4", fields["Exit"])

    def test_a_stale_sync_is_called_out(self):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(hours=20)).strftime("%Y-%m-%d %H:%M:%S")
        text = self.SYNC_BLOCKED.replace("2026-09-03 12:00:00", old).replace("BLOCKED", "OK")
        section = self.page(sync=text)
        self.assertIn("예약 작업", section)
        self.assertIn("시간 전", section)

    def test_a_fresh_sync_is_not_called_stale(self):
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        text = self.SYNC_BLOCKED.replace("2026-09-03 12:00:00", now).replace("BLOCKED", "OK")
        section = self.page(sync=text)
        self.assertNotIn("예약 작업", section)
        self.assertIn("동기화됨", section)

    def test_hours_since_handles_garbage(self):
        self.assertIsNone(dash.hours_since("yesterday-ish"))
        self.assertIsNone(dash.hours_since(""))
        from datetime import datetime
        self.assertAlmostEqual(
            2.0, dash.hours_since("2026-09-03 10:00:00", now=datetime(2026, 9, 3, 12, 0, 0)), places=3)


class KoreanTitleTests(unittest.TestCase):
    """The page is Korean; a task with a title_ko shows it, one without falls back."""

    TASKS = [
        {"id": "K-1", "title": "English title", "title_ko": "한국어 제목", "owner": "codex",
         "status": "todo"},
        {"id": "K-2", "title": "Only English", "owner": "codex", "status": "todo"},
    ]

    def page(self, served=True):
        snapshot = dash.collect(REPO)
        snapshot.tasks = list(self.TASKS)
        return dash.render(snapshot, control_token="tok" if served else None)

    def test_the_queue_and_board_prefer_the_korean_title(self):
        page = self.page()
        queue = RenderTests.section(page, "작업 대기열")
        board = RenderTests.section(page, "공유 작업판")
        self.assertIn("한국어 제목", queue)
        self.assertIn("한국어 제목", board)
        # The English title survives as a hover so Codex's wording is one
        # mouse-over away, not lost.
        self.assertIn('title="English title"', board)

    def test_a_task_without_a_translation_still_shows_its_title(self):
        page = self.page()
        self.assertIn("Only English", RenderTests.section(page, "작업 대기열"))

    def test_there_is_no_duplicate_codex_task_dropdown(self):
        page = self.page()
        self.assertNotIn('id="task"', page)
        self.assertIn('data-arg-value="K-1"',
                      RenderTests.section(page, "작업 대기열"))

    def test_every_committed_task_has_a_korean_title(self):
        # The board is what the page shows; an untranslated task is the one
        # English line on a Korean page, which is how this started.
        board = json.loads((COMPANY_CONFIG / "TASKBOARD.json").read_text(encoding="utf-8"))
        missing = [t["id"] for t in board["tasks"] if not t.get("title_ko")]
        self.assertEqual([], missing)


if __name__ == "__main__":
    unittest.main(verbosity=2)
