"""Tests for the character rig asset step; no network is used.

The behaviour that matters here is what happens when Gemini says no. A quota
stop must not delete the parts already on disk, and a missing key must name
the human gate rather than producing a half-written rig that Unity will
happily build a broken character out of.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator import character_assets as ca  # noqa: E402
from company.orchestrator.gemini_client import (  # noqa: E402
    GeminiKeyMissing, GeminiLimited,
)

PNG = b"\x89PNG\r\n\x1a\ntest"


class FakeClient:
    """Stands in for GeminiClient. Counts calls and can fail on the Nth."""

    def __init__(self, fail_on: int | None = None, error: Exception | None = None):
        self.calls: list[tuple[str, str, int]] = []
        self.fail_on = fail_on
        self.error = error

    def generate_image(self, model, prompt, images=None):
        self.calls.append((model, prompt, len(images or [])))
        if self.fail_on is not None and len(self.calls) == self.fail_on:
            raise self.error
        return PNG


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.reference = self.root / "player.png"
        self.reference.write_bytes(PNG)
        self.out = self.root / "rig"

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_part_is_requested_with_the_reference_attached(self):
        client = FakeClient()
        written = ca.generate(client, self.root, self.reference, self.out)

        self.assertEqual([part.name for part in ca.PARTS], written)
        self.assertEqual(len(ca.PARTS), len(client.calls))
        for model, _prompt, image_count in client.calls:
            self.assertEqual(ca.IMAGE_MODEL, model)
            # Without the reference this is text-to-image, and the result is a
            # different hedgehog rather than this one taken apart.
            self.assertEqual(1, image_count)

    def test_the_written_manifest_describes_what_unity_needs(self):
        ca.generate(FakeClient(), self.root, self.reference, self.out)
        manifest = json.loads((self.out / ca.MANIFEST_NAME).read_text(encoding="utf-8"))

        self.assertEqual("gemini", manifest["source"])
        names = [entry["name"] for entry in manifest["parts"]]
        self.assertEqual([part.name for part in ca.PARTS], names)
        for entry in manifest["parts"]:
            self.assertIn("x", entry["joint"])
            self.assertIn("y", entry["anchor"])

    def test_each_part_lands_in_its_own_file(self):
        ca.generate(FakeClient(), self.root, self.reference, self.out)
        for part in ca.PARTS:
            self.assertEqual(PNG, (self.out / f"{part.name}.png").read_bytes())

    def test_a_quota_stop_keeps_the_parts_already_written(self):
        client = FakeClient(fail_on=3, error=GeminiLimited("free tier spent"))
        with self.assertRaises(ca.CharacterAssetError) as caught:
            ca.generate(client, self.root, self.reference, self.out)

        self.assertIn("free tier spent", str(caught.exception))
        # Two finished before the limit; deleting them would waste the quota
        # that produced them.
        self.assertEqual(2, len(list(self.out.glob("*.png"))))
        # ...but no manifest, so Unity does not build a character out of a
        # body with one arm.
        self.assertFalse((self.out / ca.MANIFEST_NAME).exists())

    def test_a_quota_stop_never_tries_another_model(self):
        client = FakeClient(fail_on=1, error=GeminiLimited("429"))
        with self.assertRaises(ca.CharacterAssetError):
            ca.generate(client, self.root, self.reference, self.out)
        self.assertEqual(1, len(client.calls))

    def test_a_missing_key_names_the_human_gate(self):
        client = FakeClient(fail_on=1, error=GeminiKeyMissing(
            "Gemini key is ABSENT in GEMINI_API_KEY; initial_gemini_login is a HUMAN_GATE."))
        with self.assertRaises(ca.CharacterAssetError) as caught:
            ca.generate(client, self.root, self.reference, self.out)
        self.assertIn("initial_gemini_login", str(caught.exception))

    def test_a_missing_reference_fails_before_any_request(self):
        client = FakeClient()
        with self.assertRaises(ca.CharacterAssetError):
            ca.generate(client, self.root, self.root / "nothing.png", self.out)
        self.assertEqual([], client.calls)


class PartTableTests(unittest.TestCase):
    def test_joints_and_anchors_are_fractions(self):
        for part in ca.PARTS:
            for value in part.joint:
                self.assertTrue(0.0 <= value <= 1.0, f"{part.name} joint {value}")
            # An anchor may sit outside its own image - a hip is above the
            # foot that swings from it - so only x is bounded here.
            self.assertTrue(0.0 <= part.anchor[0] <= 1.0, part.name)

    def test_a_body_part_exists_and_is_first(self):
        # Unity sizes the collider and every joint offset from the body, so a
        # rig without one is not a rig.
        self.assertEqual("body", ca.PARTS[0].name)

    def test_every_prompt_asks_for_a_transparent_background(self):
        for part in ca.PARTS:
            self.assertIn("transparent background", part.prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
