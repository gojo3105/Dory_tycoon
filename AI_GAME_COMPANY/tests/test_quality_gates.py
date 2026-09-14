import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.quality_gates import (  # noqa: E402
    Finding, GateValidationError, ReleaseEvidence, qa_gate, quality_gate, release_gate,
)


class QualityGateTests(unittest.TestCase):
    def test_blocker_and_critical_failures_prevent_release(self):
        result = qa_gate([Finding("FAIL", "BLOCKER", "scene does not load")])
        self.assertFalse(result.passed)
        self.assertEqual("BLOCKED", result.status)

    def test_not_verified_is_not_promoted_to_pass(self):
        result = qa_gate([Finding("NOT_VERIFIED", "MAJOR", "device test missing")])
        self.assertEqual("NOT_VERIFIED", result.status)

    def test_visual_score_requires_visual_evidence(self):
        scores = {"gameplay": 20, "graphics": 18, "ui_ux": 14,
                  "animation_vfx": 14, "audio": 9, "stability": 10, "mobile": 10}
        with self.assertRaises(GateValidationError):
            quality_gate(scores, visually_inspected=False)
        scores.update({"graphics": None, "ui_ux": None, "animation_vfx": None})
        self.assertEqual("NOT_VERIFIED",
                         quality_gate(scores, visually_inspected=False).status)

    def test_quality_target_is_85(self):
        scores = {"gameplay": 18, "graphics": 18, "ui_ux": 13,
                  "animation_vfx": 13, "audio": 8, "stability": 9, "mobile": 8}
        self.assertEqual("APPROVED", quality_gate(scores, visually_inspected=True).status)

    def test_release_needs_process_report_and_current_apk(self):
        with tempfile.TemporaryDirectory() as tmp:
            apk = Path(tmp) / "game.apk"
            self.assertEqual("ARTIFACT_MISSING", release_gate(
                ReleaseEvidence(True, True, apk)).status)
            apk.write_bytes(b"apk")
            self.assertEqual("RELEASE_CANDIDATE", release_gate(
                ReleaseEvidence(True, True, apk, current_run=True)).status)
            self.assertEqual("UNITY_BUILD_FAILURE", release_gate(
                ReleaseEvidence(True, False, apk)).status)


if __name__ == "__main__":
    unittest.main()
