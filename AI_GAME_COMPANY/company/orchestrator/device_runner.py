"""Runs a built APK on a real Android device, and brings back evidence.

WHY THIS EXISTS. Until now nothing in this repository had eyes. Unity runs
-batchmode -nographics, which renders nothing by definition, so no screenshot
was ever possible and quality_gate's visually_inspected could only ever be
false. The review step's honest answer was NOT_VERIFIED forever. A chain could
build an APK and no one - person or model - had seen the game run.

adb closes that, and it closes it with the real thing rather than a simulation:
the actual APK, installed on the actual device, launched, photographed. The
screenshot lands at Reports/quality/<game>-screen.png, which is exactly where
ReviewExecutor already looks, so the loop closes with no change to the gate.

WHAT ONE RUN CAN AND CANNOT SAY. It can say the app installed, that it started,
how long the first frame took, whether it crashed or hung, and what it looked
like. It CANNOT say "crash rate 0.8%" - a rate needs thousands of sessions, and
Google Play measures it from real installs. play_store_growth.TECHNICAL_GATES
holds those thresholds; this module reports one session against them and says
so. Reporting a single launch as a rate would be the same lie as reporting an
exit code as a passing test.

NOTHING HERE IS INVENTED. Every command below is a documented adb subcommand,
and each one is named where it is used. The subprocess call is injected so the
whole thing is testable on a machine with no phone attached - which is most of
them, including this one today.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

#: adb inside the editor's own Android SDK. Unity installs it with the Android
#: build support module, so a machine that can build an APK already has it -
#: which is why this is preferred over asking the user to install one.
ADB_IN_EDITOR = Path("Data") / "PlaybackEngines" / "AndroidPlayer" / "SDK" / \
    "platform-tools" / "adb.exe"

#: `am start -W` prints this. TotalTime is the one to read: WaitTime includes
#: the time the previous activity took to go away, and ThisTime covers only the
#: last activity in a chain, so neither is "how long until the user saw it".
TOTAL_TIME = re.compile(r"^TotalTime:\s*(\d+)\s*$", re.MULTILINE)

#: What a crash and a hang look like in logcat.
FATAL = re.compile(r"^.*(FATAL EXCEPTION|beginning of crash).*$", re.MULTILINE)
ANR = re.compile(r"^.*ANR in ([^\s]+).*$", re.MULTILINE)

#: How long to let the game settle before photographing it. A Unity first
#: frame on a cold start is not the game - it is a splash screen, and a
#: screenshot of a splash screen tells the review step nothing about the game.
SETTLE_SECONDS = 6.0


class DeviceUnavailable(RuntimeError):
    """No adb, or no device. Not a failure of the build - a missing machine."""


@dataclass
class DeviceEvidence:
    """What one session on one device actually showed.

    Deliberately not called a "result": every field is an observation, and the
    gates decide. `sessions` is 1 by construction, and it is here so that a
    caller cannot mistake this for a rate.
    """
    device: str = ""
    sessions: int = 1
    installed: bool = False
    launched: bool = False
    cold_start_ms: int | None = None
    screenshot: Path | None = None
    crashes: tuple[str, ...] = field(default_factory=tuple)
    anrs: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""

    @property
    def ok(self) -> bool:
        return (self.installed and self.launched and not self.crashes
                and not self.anrs and self.screenshot is not None)


@dataclass
class DeviceRunner:
    repo_root: Path
    adb: str
    #: Seconds any single adb call may take. An install of a 30 MB APK over a
    #: slow cable is the long one.
    timeout_seconds: float = 300.0
    #: Injected for tests: (args, binary) -> (returncode, stdout, stderr).
    runner: object = field(default=None, repr=False)

    # ---- locating adb ----------------------------------------------------

    @staticmethod
    def adb_from_editor(unity_path: str | None) -> str | None:
        """The adb that came with the editor that builds these APKs."""
        if not unity_path:
            return None
        editor = Path(unity_path).parent          # .../Editor/Unity.exe -> Editor
        candidate = editor / ADB_IN_EDITOR
        return str(candidate) if candidate.is_file() else None

    # ---- talking to the device -------------------------------------------

    def _run(self, *args: str, binary: bool = False):
        """One adb call. shell=False and a list, never a command string."""
        if self.runner is not None:                      # test seam
            return self.runner(list(args), binary)
        completed = subprocess.run(
            [self.adb, *args], capture_output=True,
            timeout=self.timeout_seconds,
            # binary for screencap, whose stdout IS the PNG. Decoding that as
            # text corrupts it silently - the file is written and is not an
            # image, which is worse than an error.
            **({} if binary else {"text": True, "encoding": "utf-8",
                                  "errors": "replace"}),
        )
        return completed.returncode, completed.stdout, completed.stderr

    def devices(self) -> list[str]:
        """Serial numbers of devices in the `device` state, nothing else.

        `unauthorized` and `offline` are listed by adb too, and installing to
        one fails in a way that reads like a broken APK. They are excluded so
        the error says what is actually wrong.
        """
        code, out, err = self._run("devices")
        if code != 0:
            raise DeviceUnavailable(f"adb devices 실패: {(err or '').strip()}")
        serials = []
        for line in (out or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def install(self, apk: Path) -> tuple[bool, str]:
        """-r reinstall, -d allow downgrade: a dev build's version code goes
        backwards all the time and a refused downgrade is not a real failure."""
        code, out, err = self._run("install", "-r", "-d", str(apk))
        text = f"{out or ''}{err or ''}".strip()
        # adb install exits 0 even when it prints Failure, so the text decides.
        return code == 0 and "Failure" not in text, text

    def force_stop(self, package: str) -> None:
        self._run("shell", "am", "force-stop", package)

    def clear_log(self) -> None:
        self._run("logcat", "-c")

    def launch(self, package: str) -> tuple[bool, int | None, str]:
        """Cold-start the app and read TotalTime out of `am start -W`."""
        code, out, err = self._run(
            "shell", "am", "start", "-W", "-S",
            "-a", "android.intent.action.MAIN",
            "-c", "android.intent.category.LAUNCHER",
            "-n", f"{package}/com.unity3d.player.UnityPlayerActivity")
        text = f"{out or ''}{err or ''}"
        match = TOTAL_TIME.search(text)
        started = code == 0 and "Error" not in text
        return started, int(match.group(1)) if match else None, text.strip()

    def screenshot(self, destination: Path) -> Path | None:
        """`adb exec-out screencap -p` - the framebuffer, as a PNG, on stdout.

        exec-out rather than `shell screencap > file`: adb shell mangles \\n
        into \\r\\n on some devices, which corrupts every PNG it touches. That
        bug is old, well documented, and produces a file that exists and is
        not an image - exactly the kind of "success" this project keeps
        refusing to report.
        """
        code, out, err = self._run("exec-out", "screencap", "-p", binary=True)
        if code != 0 or not out or not out.startswith(b"\x89PNG\r\n\x1a\n"):
            return None
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(out)
        return destination

    def crashes(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """(crashes, ANRs) seen since clear_log."""
        _, crash_log, _ = self._run("logcat", "-d", "-b", "crash")
        _, main_log, _ = self._run("logcat", "-d", "-b", "main")
        found = tuple(line.strip() for line in FATAL.findall(crash_log or ""))
        hangs = tuple(f"ANR in {name}" for name in ANR.findall(main_log or ""))
        return found, hangs

    # ---- one session -----------------------------------------------------

    def verify(self, game: str, apk: Path, package: str,
               settle_seconds: float = SETTLE_SECONDS) -> DeviceEvidence:
        """Install, launch, wait, photograph, and read the log. One session."""
        serials = self.devices()
        if not serials:
            raise DeviceUnavailable(
                "연결된 안드로이드 기기가 없습니다. USB 디버깅을 켠 기기를 "
                "연결하거나 에뮬레이터를 실행하세요.")
        evidence = DeviceEvidence(device=serials[0])
        if len(serials) > 1:
            evidence.detail = (f"기기 {len(serials)}대 중 {serials[0]} 사용: "
                               + ", ".join(serials))

        installed, text = self.install(apk)
        evidence.installed = installed
        if not installed:
            evidence.detail = (evidence.detail + " " + text).strip()
            return evidence

        # Stopped and the log cleared FIRST, so what comes back belongs to this
        # launch rather than to whatever was running before.
        self.force_stop(package)
        self.clear_log()

        launched, cold_ms, text = self.launch(package)
        evidence.launched = launched
        evidence.cold_start_ms = cold_ms
        if not launched:
            evidence.detail = (evidence.detail + " " + text).strip()
            return evidence

        time.sleep(settle_seconds)
        evidence.screenshot = self.screenshot(
            self.repo_root / "Reports" / "quality" / f"{game}-screen.png")
        evidence.crashes, evidence.anrs = self.crashes()
        self.force_stop(package)
        return evidence
