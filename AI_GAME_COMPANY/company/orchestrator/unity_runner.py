"""Drives Unity in batch mode. Master prompt section 18 and STEP 14.

DESIGN NOTE - why this does not call Unity.exe directly.

On Windows Unity can relaunch itself as a separate process, so the exit code
of the process we start is not the exit code of the work. Trusting it made
every CI step "succeed" in seconds while the real Unity run was later killed
as an orphan. scripts/ci/wait-for-unity.ps1 already solves this by waiting on
a Logs/<name>.exitcode sentinel that the Editor writes through CommandLineExit,
and that script is the one currently producing real APKs.

So this module builds the same invocations that workflow uses and delegates to
that script. Re-implementing the sentinel handling in Python would risk
reintroducing a bug that cost real debugging time, for no benefit.

The argument list is written into a generated .ps1 rather than passed on a
command line: Unity paths contain spaces ("C:\\Program Files\\..."), and
quoting a string[] through subprocess -> powershell.exe -File is a well known
source of silent breakage. A generated script with a proper PowerShell array
literal is deterministic, and it stays on disk so a failed run can be
inspected instead of guessed at.
"""

from __future__ import annotations

import subprocess
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from company.orchestrator.policy import Policy, PolicyViolation

# Verified against .github/workflows/game-factory.yml, which has produced a
# real APK on the self-hosted runner. Section 38: not invented.
ENTRY_GENERATE = "GameFactory.Editor.GameFactoryGenerator.GenerateFromCommandLine"
ENTRY_VALIDATE = "GameFactory.Editor.GameValidator.ValidateFromCommandLine"
ENTRY_BUILD = "GameFactory.Editor.BuildAndroid.BuildFromCommandLine"

WAIT_SCRIPT = Path("scripts") / "ci" / "wait-for-unity.ps1"

#: The Build Report the Editor leaves behind, relative to the repo root.
#: Named once because two places now check it - verify_build here, and the
#: chain's release gate - and a build report that only one of them can find
#: would be the quietest possible disagreement.
BUILD_REPORT = Path("Logs") / "unity-build.log"


def ps_quote(value: str) -> str:
    """Single-quoted PowerShell literal; ' is escaped by doubling it."""
    return "'" + str(value).replace("'", "''") + "'"


@dataclass
class UnityResult:
    step: str
    exit_code: int | None
    ok: bool
    log_path: Path | None = None
    script_path: Path | None = None
    stdout: str = ""
    stderr: str = ""
    detail: str = ""
    test_results_path: Path | None = None
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    failures: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class UnityRunner:
    repo_root: Path
    policy: Policy
    timeout_minutes: int = 30
    # wait-for-unity.ps1 launches $env:UNITY_PATH. On the self-hosted runner
    # that is a machine environment variable, which is why the workflow never
    # sets it - and why a local run from an ordinary shell would launch nothing
    # at all. Passing it explicitly makes a local build self-sufficient instead
    # of depending on how the machine happens to be configured.
    unity_path: str | None = None
    # Injectable so the command construction can be tested without Windows.
    runner: object = field(default=None, repr=False)

    @staticmethod
    def unity_path_from_profile(profile_path: Path) -> str | None:
        """The editor path detect-environment.ps1 already verified."""
        if not profile_path.is_file():
            return None
        import json
        profile = json.loads(profile_path.read_text(encoding="utf-8-sig"))
        unity = profile.get("unity") or {}
        if unity.get("status") != "OK":
            return None
        return unity.get("matchingEditorPath") or None

    # ---- invocation building ---------------------------------------------

    def unity_args(self, entry_method: str | None, log_name: str,
                   extra: list[str] | None = None) -> list[str]:
        """The -batchmode argument list, matching the working workflow exactly."""
        args = [
            "-batchmode", "-nographics",
            "-projectPath", str(self.repo_root),
        ]
        if entry_method is not None:
            args += ["-executeMethod", entry_method]
        args += extra or []
        args += ["-logFile", str(self.repo_root / "Logs" / f"{log_name}.log")]
        return args

    def build_wrapper_script(self, sentinel_name: str | None, unity_args: list[str],
                             test_results_path: Path | None = None,
                             timeout_minutes: int | None = None) -> str:
        """The generated .ps1 that calls wait-for-unity.ps1."""
        quoted = ", ".join(ps_quote(a) for a in unity_args)
        timeout = timeout_minutes or self.timeout_minutes
        unity_line = ""
        if self.unity_path:
            unity_line = f"$env:UNITY_PATH = {ps_quote(self.unity_path)}\r\n"
        selector = (
            f"-TestResultsPath {ps_quote(test_results_path)} "
            if test_results_path is not None
            else f"-SentinelName {ps_quote(sentinel_name or '')} "
        )
        return (
            "$ErrorActionPreference = 'Stop'\r\n"
            f"{unity_line}"
            f"Set-Location {ps_quote(self.repo_root)}\r\n"
            f"& {ps_quote(self.repo_root / WAIT_SCRIPT)} "
            f"{selector}"
            f"-TimeoutMinutes {int(timeout)} "
            f"-UnityArgs @({quoted})\r\n"
            "exit $LASTEXITCODE\r\n"
        )

    # ---- execution -------------------------------------------------------

    def _run_powershell(self, script_text: str,
                        timeout_minutes: int | None = None) -> tuple[int, str, str, Path]:
        scripts_dir = self.repo_root / "AI_GAME_COMPANY" / "logs" / "unity-invocations"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        script_path = scripts_dir / f"invoke-{stamp}-{uuid.uuid4().hex[:6]}.ps1"
        # ascii on purpose: PowerShell 5.1 reads a BOM-less UTF-8 .ps1 using
        # the local codepage, so a stray non-ASCII byte can break parsing.
        script_path.write_text(script_text, encoding="ascii")

        if self.runner is not None:  # test seam
            code, out, err = self.runner(script_path, script_text)
            return code, out, err, script_path

        # Must match what the generated script gave wait-for-unity.ps1, plus
        # slack. Using self.timeout_minutes here regardless meant the build step
        # allowed the inner script 60 minutes while killing the outer process at
        # 32 - so a cold Gradle cache produced an uncaught TimeoutExpired and
        # left Unity running detached.
        budget = timeout_minutes or self.timeout_minutes
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(script_path)],
            capture_output=True, text=True,
            cwd=str(self.repo_root),
            timeout=budget * 60 + 120,
            # Explicit: text=True decodes with the machine's locale encoding,
            # which is cp949 on the Korean build PC. Unity and Gradle both emit
            # UTF-8, and a single non-ASCII byte in a compiler error - a Korean
            # path, a quoted string from the source - would raise
            # UnicodeDecodeError and lose the entire build log, exactly when
            # that log is the thing being asked for.
            encoding="utf-8", errors="replace",
        )
        return completed.returncode, completed.stdout, completed.stderr, script_path

    def _step(self, step: str, sentinel: str | None, entry: str | None, log_name: str,
              extra: list[str] | None = None,
              test_results_path: Path | None = None,
              timeout_minutes: int | None = None) -> UnityResult:
        args = self.unity_args(entry, log_name, extra)
        script = self.build_wrapper_script(
            sentinel, args, test_results_path, timeout_minutes
        )

        try:
            code, out, err, script_path = self._run_powershell(script, timeout_minutes)
        except subprocess.TimeoutExpired as exc:
            # A timeout is a failed step, not a crash. Say plainly that Unity
            # was launched detached and may still be running, because killing
            # powershell.exe does not kill it - that is the whole reason
            # wait-for-unity.ps1 waits on a sentinel instead of a process.
            minutes = timeout_minutes or self.timeout_minutes
            return UnityResult(
                step=step, exit_code=None, ok=False,
                log_path=self.repo_root / "Logs" / f"{log_name}.log",
                detail=(
                    f"{step} timed out after {minutes} min. Unity was started "
                    "detached, so it may still be running - check Task Manager "
                    f"and Logs/{log_name}.log before re-running."
                ),
                stderr=str(exc),
            )

        return UnityResult(
            step=step,
            exit_code=code,
            ok=(code == 0),
            log_path=self.repo_root / "Logs" / f"{log_name}.log",
            script_path=script_path,
            stdout=out,
            stderr=err,
            detail="" if code == 0 else f"{step} exited {code}",
        )

    def generate(self, game_id: str) -> UnityResult:
        return self._step(
            "generate", "generate", ENTRY_GENERATE, "unity-generate",
            extra=["-gameSpec", f"GameSpecs/{game_id}.json"],
        )

    def validate(self) -> UnityResult:
        return self._step("validate", "validate", ENTRY_VALIDATE, "unity-validate")

    def build_android(self, game_id: str) -> UnityResult:
        # -gameId and -buildType, NOT -gameSpec: BuildFromCommandLine reads
        # -gameId and exits 1 on the spot when it is missing, before
        # BuildPipeline.BuildPlayer runs - so the failure leaves no
        # unity-build-report.log at all, which is how this was found.
        # These flags are copied from the workflow's Build step, which has
        # produced a real APK.
        #
        # 60 minutes because that is what the working workflow allows for the
        # build step; Gradle on a cold cache genuinely takes that long.
        return self._step(
            "build", "build", ENTRY_BUILD, "unity-build",
            extra=["-gameId", game_id, "-buildType", "apk"],
            timeout_minutes=60,
        )

    def test(self, platform: str) -> UnityResult:
        """Run and verify one Unity test platform using the CI invocation."""
        platforms = {"editmode": "EditMode", "playmode": "PlayMode"}
        key = platform.lower()
        if key not in platforms:
            raise ValueError(f"unknown Unity test platform: {platform}")

        xml_relative = Path("Logs") / f"unity-test-{key}.xml"
        xml_path = self.repo_root / xml_relative
        result = self._step(
            f"test-{key}", None, None, f"unity-test-{key}",
            extra=[
                "-runTests", "-testPlatform", platforms[key],
                "-testResults", str(xml_path),
            ],
            test_results_path=xml_relative,
        )
        result.test_results_path = xml_path
        self._apply_test_results(result)
        return result

    def _apply_test_results(self, result: UnityResult) -> None:
        """Make the NUnit XML, rather than Unity's exit alone, authoritative."""
        path = result.test_results_path
        if path is None or not path.is_file():
            result.ok = False
            result.detail = f"{result.step} failed: NUnit XML is missing: {path}"
            return

        try:
            root = ET.parse(path).getroot()
            if root.tag != "test-run":
                raise ValueError(f"expected test-run root, found {root.tag}")
            result.passed = int(root.attrib["passed"])
            result.failed = int(root.attrib["failed"])
            result.skipped = int(root.attrib["skipped"])
        except (ET.ParseError, OSError, KeyError, TypeError, ValueError) as exc:
            result.ok = False
            result.detail = f"{result.step} failed: NUnit XML is unparsable: {exc}"
            return

        result.failures = []
        for case in root.iter("test-case"):
            if case.attrib.get("result", "").lower() != "failed":
                continue
            name = case.attrib.get("fullname") or case.attrib.get("name") or "(unnamed test)"
            message_node = case.find("./failure/message")
            message = "" if message_node is None else "".join(message_node.itertext()).strip()
            result.failures.append((name, message or "(no failure message)"))

        process_ok = result.exit_code == 0
        result.ok = process_ok and result.failed == 0
        if result.failed:
            failure_details = "; ".join(
                f"{name}: {message}" for name, message in result.failures
            )
            result.detail = f"{result.step} reported {result.failed} failed test(s)"
            if failure_details:
                result.detail += f": {failure_details}"
        elif not process_ok:
            result.detail = f"{result.step} exited {result.exit_code} despite passing NUnit XML"

    def run_tests(self, game_id: str, platform: str = "both") -> list[UnityResult]:
        """Generate the test scene, then run the requested Unity test suites."""
        if platform not in ("editmode", "playmode", "both"):
            raise ValueError(f"unknown Unity test selection: {platform}")

        results = [self.generate(game_id)]
        if not results[0].ok:
            return results

        selected = ("editmode", "playmode") if platform == "both" else (platform,)
        results.extend(self.test(item) for item in selected)
        return results

    # ---- verification ----------------------------------------------------

    def find_apk(self, game_id: str) -> Path | None:
        """A non-empty .apk/.aab under Builds/<game_id>/, newest first."""
        build_dir = self.repo_root / "Builds" / game_id
        if not build_dir.is_dir():
            return None
        found = [
            path for pattern in ("**/*.apk", "**/*.aab")
            for path in build_dir.glob(pattern)
            if path.is_file() and path.stat().st_size > 0
        ]
        if not found:
            return None
        return max(found, key=lambda p: p.stat().st_mtime)

    def verify_build(self, game_id: str, result: UnityResult) -> Path:
        """Sections 18 and 32: exit code AND build report AND APK on disk.

        Returns the APK path, or raises PolicyViolation. Deliberately raises
        rather than returning a bool - section 38 forbids reporting a build as
        successful without an APK, and an ignored return value is how that
        rule gets broken by accident.
        """
        apk = self.find_apk(game_id)
        report = self.repo_root / BUILD_REPORT

        self.policy.assert_build_verification(
            exit_code_ok=result.ok,
            report_ok=report.is_file() and report.stat().st_size > 0,
            apk_exists=apk is not None,
        )
        assert apk is not None  # assert_build_verification raises otherwise
        return apk

    def run_pipeline(self, game_id: str) -> tuple[list[UnityResult], Path | None]:
        """generate -> validate -> build, stopping at the first failure.

        Mirrors the workflow's ordering. Tests are left to the workflow: it
        already runs EditMode and PlayMode through the same wrapper, and
        duplicating that here would mean two places to keep in sync.
        """
        results: list[UnityResult] = []

        # Spelled out rather than looped over a tuple. `for step in
        # (self.generate(game_id), self.validate())` reads like it stops at the
        # first failure and does not: Python builds the tuple first, so BOTH
        # calls happen before the loop body ever checks `ok`. A run against
        # game02, which has no GameSpec, launched the editor twice - once to
        # fail generating, once to validate a project that had not been
        # generated. The results list looked right, so the existing test passed;
        # only the invocation scripts on disk showed it.
        generated = self.generate(game_id)
        results.append(generated)
        if not generated.ok:
            return results, None

        validated = self.validate()
        results.append(validated)
        if not validated.ok:
            return results, None

        build = self.build_android(game_id)
        results.append(build)
        if not build.ok:
            return results, None

        try:
            return results, self.verify_build(game_id, build)
        except PolicyViolation as exc:
            build.ok = False
            build.detail = str(exc)
            return results, None
