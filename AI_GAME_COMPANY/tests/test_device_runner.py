"""Tests for the device step - the first thing here that can see the game.

Run:  python3 AI_GAME_COMPANY/tests/test_device_runner.py

No phone is attached to the machine this was written on, and probably none is
attached to yours, so the subprocess call is injected exactly as UnityRunner's
is. What that leaves untested is adb itself; what it covers is every place
this code could turn a non-answer into a yes.

The ones that matter most:

  * adb install EXITS 0 AND PRINTS "Failure". Trusting the exit code reports a
    successful install of an APK that is not on the device.
  * `adb shell screencap` corrupts PNGs on devices that translate newlines.
    The file exists, has a sensible size, and is not an image. exec-out is
    used instead, and anything that is not a PNG is refused rather than saved.
  * a device listed as `unauthorized` is a device adb will happily list and
    refuse to install to.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.device_runner import (  # noqa: E402
    DeviceEvidence, DeviceRunner, DeviceUnavailable,
)
from company.orchestrator.executors import find_screen  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"pretend pixels"

LAUNCHED = """Starting: Intent { act=android.intent.action.MAIN }
Status: ok
LaunchState: COLD
Activity: com.gamefactory.game01/com.unity3d.player.UnityPlayerActivity
TotalTime: 2418
WaitTime: 2465
Complete
"""

CRASH_LOG = """--------- beginning of crash
01-01 00:00:01.000  E AndroidRuntime: FATAL EXCEPTION: main
01-01 00:00:01.000  E AndroidRuntime: java.lang.NullPointerException
"""

ANR_LOG = """01-01 00:00:09.000  E ActivityManager: ANR in com.gamefactory.game01
01-01 00:00:09.000  E ActivityManager: Reason: Input dispatching timed out
"""


class FakeAdb:
    """Answers adb calls from a table, and records what was asked, in order."""

    def __init__(self, **replies):
        self.replies = {
            "devices": (0, "List of devices attached\nR5CT30ABCDE\tdevice\n", ""),
            "install": (0, "Success\n", ""),
            "start": (0, LAUNCHED, ""),
            "screencap": (0, PNG, b""),
            "crash": (0, "", ""),
            "main": (0, "", ""),
            "force-stop": (0, "", ""),
            "logcat-c": (0, "", ""),
        }
        self.replies.update(replies)
        self.calls: list[list[str]] = []

    def __call__(self, args, binary):
        self.calls.append(list(args))
        key = self.key_of(args)
        return self.replies.get(key, (0, b"" if binary else "", b"" if binary else ""))

    @staticmethod
    def key_of(args: list[str]) -> str:
        if args[0] == "devices":
            return "devices"
        if args[0] == "install":
            return "install"
        if args[0] == "exec-out":
            return "screencap"
        if args[0] == "logcat" and "-c" in args:
            return "logcat-c"
        if args[0] == "logcat":
            return "crash" if "crash" in args else "main"
        if "force-stop" in args:
            return "force-stop"
        if "start" in args:
            return "start"
        return " ".join(args)

    @property
    def order(self) -> list[str]:
        return [self.key_of(call) for call in self.calls]


class DeviceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.apk = self.root / "Builds" / "game01" / "APK" / "game01.apk"
        self.apk.parent.mkdir(parents=True, exist_ok=True)
        self.apk.write_bytes(b"PK\x03\x04 not really an apk")

    def runner(self, **replies) -> tuple[DeviceRunner, FakeAdb]:
        fake = FakeAdb(**replies)
        return DeviceRunner(repo_root=self.root, adb="adb.exe", runner=fake), fake

    def verify(self, **replies) -> tuple[DeviceEvidence, FakeAdb]:
        runner, fake = self.runner(**replies)
        evidence = runner.verify("game01", self.apk, "com.gamefactory.game01",
                                 settle_seconds=0.0)
        return evidence, fake


class ListingTests(DeviceTestCase):
    def test_a_device_that_has_not_authorised_this_pc_is_not_a_device(self):
        """adb lists it, and installing to it fails in a misleading way."""
        runner, _ = self.runner(devices=(
            0, "List of devices attached\nR5CT30ABCDE\tunauthorized\n"
               "emulator-5554\toffline\n", ""))
        self.assertEqual([], runner.devices())

    def test_only_ready_devices_are_returned(self):
        runner, _ = self.runner(devices=(
            0, "List of devices attached\nA\tdevice\nB\tunauthorized\nC\tdevice\n", ""))
        self.assertEqual(["A", "C"], runner.devices())

    def test_no_device_is_a_missing_machine_not_a_failed_build(self):
        _, fake = self.runner(devices=(0, "List of devices attached\n", ""))
        runner = DeviceRunner(repo_root=self.root, adb="adb.exe", runner=fake)
        with self.assertRaises(DeviceUnavailable) as caught:
            runner.verify("game01", self.apk, "com.gamefactory.game01")
        self.assertIn("기기가 없습니다", str(caught.exception))


class InstallTests(DeviceTestCase):
    def test_adb_exiting_zero_while_printing_failure_is_not_an_install(self):
        """The exit code says yes and the device does not have the app."""
        evidence, fake = self.verify(install=(
            0, "", "adb: failed to install game01.apk: Failure "
                   "[INSTALL_FAILED_INSUFFICIENT_STORAGE]\n"))
        self.assertFalse(evidence.installed)
        self.assertIn("INSTALL_FAILED_INSUFFICIENT_STORAGE", evidence.detail)

    def test_a_failed_install_does_not_go_on_to_launch_anything(self):
        _, fake = self.verify(install=(0, "Failure [INSTALL_FAILED_OLDER_SDK]\n", ""))
        self.assertNotIn("start", fake.order)
        self.assertNotIn("screencap", fake.order)


class SessionTests(DeviceTestCase):
    def test_a_clean_session_brings_back_a_screenshot_and_a_cold_start(self):
        evidence, _ = self.verify()
        self.assertTrue(evidence.ok, evidence.detail)
        self.assertEqual("R5CT30ABCDE", evidence.device)
        self.assertEqual(2418, evidence.cold_start_ms)
        self.assertEqual(PNG, evidence.screenshot.read_bytes())
        self.assertEqual(1, evidence.sessions)

    def test_the_screenshot_lands_where_the_review_step_looks_for_it(self):
        """The loop closing. If these two ever disagree, the gate goes blind."""
        evidence, _ = self.verify()
        self.assertEqual(evidence.screenshot, find_screen(self.root, "game01"))

    def test_the_log_is_cleared_before_the_launch_not_after(self):
        """Otherwise the crash reported belongs to whatever ran previously."""
        _, fake = self.verify()
        order = fake.order
        self.assertLess(order.index("logcat-c"), order.index("start"))
        self.assertLess(order.index("start"), order.index("crash"))

    def test_a_crash_is_reported_even_though_the_app_started(self):
        evidence, _ = self.verify(crash=(0, CRASH_LOG, ""))
        self.assertTrue(evidence.launched)
        self.assertFalse(evidence.ok)
        self.assertTrue(any("FATAL EXCEPTION" in line for line in evidence.crashes))

    def test_a_hang_is_reported(self):
        evidence, _ = self.verify(main=(0, ANR_LOG, ""))
        self.assertEqual(("ANR in com.gamefactory.game01",), evidence.anrs)
        self.assertFalse(evidence.ok)

    def test_an_app_that_will_not_start_is_not_photographed(self):
        evidence, fake = self.verify(start=(
            0, "Starting: Intent { }\nError: Activity not started\n", ""))
        self.assertFalse(evidence.launched)
        self.assertIsNone(evidence.screenshot)
        self.assertNotIn("screencap", fake.order)


class ScreenshotTests(DeviceTestCase):
    def test_bytes_that_are_not_a_png_are_refused_rather_than_saved(self):
        """The `adb shell screencap` newline bug, which this code avoids.

        A saved non-image is worse than no image: the file exists, the size
        looks plausible, and the review step would report that something looked
        at the screen.
        """
        evidence, _ = self.verify(screencap=(0, b"\x89PNG\r\r\n\x1a\n junk", b""))
        self.assertIsNone(evidence.screenshot)
        self.assertFalse(evidence.ok)

    def test_exec_out_is_used_because_adb_shell_corrupts_pngs(self):
        _, fake = self.verify()
        shot = next(call for call in fake.calls if "screencap" in call)
        self.assertEqual("exec-out", shot[0])

    def test_an_empty_framebuffer_is_not_a_screenshot(self):
        evidence, _ = self.verify(screencap=(0, b"", b""))
        self.assertIsNone(evidence.screenshot)


class AdbLocationTests(unittest.TestCase):
    def test_adb_is_taken_from_the_editor_that_builds_the_apks(self):
        with tempfile.TemporaryDirectory() as tmp:
            editor = Path(tmp) / "Editor"
            adb = (editor / "Data" / "PlaybackEngines" / "AndroidPlayer" / "SDK"
                   / "platform-tools" / "adb.exe")
            adb.parent.mkdir(parents=True, exist_ok=True)
            adb.write_text("", encoding="utf-8")
            found = DeviceRunner.adb_from_editor(str(editor / "Unity.exe"))
            self.assertEqual(str(adb), found)

    def test_no_editor_path_means_no_adb_rather_than_a_guess(self):
        self.assertIsNone(DeviceRunner.adb_from_editor(None))
        self.assertIsNone(DeviceRunner.adb_from_editor("C:/nowhere/Unity.exe"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
