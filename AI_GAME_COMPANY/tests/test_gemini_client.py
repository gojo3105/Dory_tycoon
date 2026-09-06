"""Tests for the policy-gated Gemini design adapter; no network is used."""

from __future__ import annotations

import base64
import json
import io
import sys
import unittest
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from company.orchestrator.gemini_client import (  # noqa: E402
    MAX_INPUT_IMAGE_BYTES,
    GeminiClient,
    GeminiInputRejected,
    GeminiKeyMissing,
    GeminiLimited,
    GeminiModelNotAllowed,
    GeminiResponseError,
    GeminiUnavailable,
)
from company.orchestrator.policy import Policy, PolicyViolation  # noqa: E402


SECRET = "gemini-secret-that-must-never-leak"
TEXT_MODEL = "gemini-2.5-flash"
IMAGE_MODEL = "gemini-2.5-flash-image"
PNG = b"\x89PNG\r\n\x1a\nsmall-test-png"


def policy_with(**overrides) -> Policy:
    raw = {
        "allow_gemini_design": True,
        "gemini_free_tier_models": [TEXT_MODEL, IMAGE_MODEL],
        "gemini_api_key_env": "GEMINI_API_KEY",
        "blocked_env_keys": ["GOOGLE_API_KEY"],
        "human_gates": ["initial_gemini_login"],
    }
    raw.update(overrides)
    return Policy(raw=raw)


class FakeRunner:
    def __init__(self, body: dict | bytes, *, status: int = 200,
                 content_type: str = "application/json"):
        self.body = json.dumps(body).encode("utf-8") if isinstance(body, dict) else body
        self.status = status
        self.content_type = content_type
        self.calls = []

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        return self.status, self.content_type, self.body


def text_response(text: str = "design notes") -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def image_response(data: bytes = PNG, mime: str = "image/png") -> dict:
    return {"candidates": [{"content": {"parts": [{"inlineData": {
        "mimeType": mime,
        "data": base64.b64encode(data).decode("ascii"),
    }}]}}]}


class GeminiClientTests(unittest.TestCase):
    def make(self, fake: FakeRunner, policy: Policy | None = None,
             environ: dict[str, str] | None = None) -> GeminiClient:
        return GeminiClient(policy or policy_with(), runner=fake,
                            environ={} if environ is None else environ)

    def test_policy_gate_prevents_any_request(self):
        fake = FakeRunner(text_response())
        client = self.make(fake, policy_with(allow_gemini_design=False),
                           {"GEMINI_API_KEY": SECRET})
        with self.assertRaises(PolicyViolation):
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertEqual(fake.calls, [])

    def test_non_free_model_gate_prevents_any_request(self):
        fake = FakeRunner(text_response())
        client = self.make(fake, environ={"GEMINI_API_KEY": SECRET})
        with self.assertRaises(GeminiModelNotAllowed):
            client.generate_text("gemini-pro", "prompt")
        self.assertEqual(fake.calls, [])

    def test_missing_policy_named_key_prevents_any_request(self):
        fake = FakeRunner(text_response())
        client = self.make(fake, environ={"GOOGLE_API_KEY": SECRET})
        with self.assertRaises(GeminiKeyMissing) as ctx:
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertEqual(fake.calls, [])
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_status_reports_absence_and_human_gate_without_google_key_value(self):
        client = self.make(FakeRunner(text_response()),
                           environ={"GOOGLE_API_KEY": SECRET})
        status = client.status_summary()
        self.assertIn("ABSENT", status)
        self.assertIn("initial_gemini_login", status)
        self.assertNotIn(SECRET, status)

    def test_only_the_policy_named_environment_variable_is_read(self):
        fake = FakeRunner(text_response())
        policy = policy_with(gemini_api_key_env="CUSTOM_GEMINI_KEY")
        client = self.make(fake, policy, {
            "CUSTOM_GEMINI_KEY": "custom-key",
            "GEMINI_API_KEY": SECRET,
            "GOOGLE_API_KEY": SECRET,
        })
        self.assertEqual(client.generate_text(TEXT_MODEL, "prompt"), "design notes")
        request = fake.calls[0][0]
        self.assertEqual(request.get_header("X-goog-api-key"), "custom-key")
        self.assertNotIn(SECRET, str(request.header_items()))

    def test_google_api_key_is_never_used(self):
        fake = FakeRunner(text_response())
        client = self.make(fake, environ={
            "GEMINI_API_KEY": "allowed-key",
            "GOOGLE_API_KEY": SECRET,
        })
        self.assertEqual(client.generate_text(TEXT_MODEL, "prompt"), "design notes")
        request = fake.calls[0][0]
        self.assertEqual(request.get_header("X-goog-api-key"), "allowed-key")
        self.assertNotIn(SECRET, request.full_url)
        self.assertNotIn(SECRET, str(request.header_items()))

    def test_429_degrades_without_retrying(self):
        fake = FakeRunner({"error": {"message": "quota exceeded"}}, status=429)
        client = self.make(fake, environ={"GEMINI_API_KEY": SECRET})
        with self.assertRaises(GeminiLimited) as ctx:
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertEqual(len(fake.calls), 1)
        self.assertIn("refusing to retry", str(ctx.exception))

    def test_urllib_http_429_degrades_without_leaking_error_body(self):
        class Http429Runner:
            def __init__(self):
                self.calls = 0

            def __call__(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(
                    request.full_url, 429, "rate limited", {},
                    io.BytesIO(f"quota: {SECRET}".encode("utf-8")),
                )

        fake = Http429Runner()
        client = GeminiClient(policy_with(), runner=fake,
                              environ={"GEMINI_API_KEY": SECRET})
        with self.assertRaises(GeminiLimited) as ctx:
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertEqual(fake.calls, 1)
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_quota_error_body_degrades_even_without_429(self):
        fake = FakeRunner({"error": {"status": "RESOURCE_EXHAUSTED"}}, status=403)
        client = self.make(fake, environ={"GEMINI_API_KEY": SECRET})
        with self.assertRaises(GeminiLimited):
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertEqual(len(fake.calls), 1)

    def test_http_error_raises(self):
        fake = FakeRunner({"error": "denied"}, status=403)
        with self.assertRaises(GeminiUnavailable):
            self.make(fake, environ={"GEMINI_API_KEY": SECRET}).generate_text(
                TEXT_MODEL, "prompt")

    def test_empty_and_malformed_responses_raise(self):
        for body in (b"", b"not json", json.dumps({"candidates": []}).encode()):
            with self.subTest(body=body):
                client = self.make(FakeRunner(body), environ={"GEMINI_API_KEY": SECRET})
                with self.assertRaises(GeminiResponseError):
                    client.generate_text(TEXT_MODEL, "prompt")

    def test_unexpected_http_content_type_raises(self):
        fake = FakeRunner(text_response(), content_type="text/plain")
        with self.assertRaises(GeminiResponseError):
            self.make(fake, environ={"GEMINI_API_KEY": SECRET}).generate_text(
                TEXT_MODEL, "prompt")

    def test_text_and_png_generation_return_expected_types(self):
        text_client = self.make(FakeRunner(text_response("hello")),
                                environ={"GEMINI_API_KEY": SECRET})
        self.assertEqual(text_client.generate_text(TEXT_MODEL, "prompt"), "hello")
        image_client = self.make(FakeRunner(image_response()),
                                 environ={"GEMINI_API_KEY": SECRET})
        self.assertEqual(image_client.generate_image(IMAGE_MODEL, "draw"), PNG)

    def test_quota_word_in_generated_text_is_not_a_limit_response(self):
        client = self.make(FakeRunner(text_response("quota is a noun")),
                           environ={"GEMINI_API_KEY": SECRET})
        self.assertEqual(client.generate_text(TEXT_MODEL, "define quota"),
                         "quota is a noun")

    def test_non_png_image_content_raises(self):
        client = self.make(FakeRunner(image_response(b"webp", "image/webp")),
                           environ={"GEMINI_API_KEY": SECRET})
        with self.assertRaises(GeminiResponseError):
            client.generate_image(IMAGE_MODEL, "draw")

    def test_secret_never_appears_in_status_or_exception_text(self):
        fake = FakeRunner({"error": {"message": SECRET}}, status=500)
        client = self.make(fake, environ={"GEMINI_API_KEY": SECRET})
        status = client.status_summary()
        self.assertIn("PRESENT", status)
        self.assertNotIn(SECRET, status)
        with self.assertRaises(GeminiUnavailable) as ctx:
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_runner_exception_cannot_leak_key(self):
        class LeakyRunner:
            def __call__(self, request, timeout):
                raise urllib.error.URLError(SECRET)

        client = GeminiClient(policy_with(), runner=LeakyRunner(),
                              environ={"GEMINI_API_KEY": SECRET})
        with self.assertRaises(GeminiUnavailable) as ctx:
            client.generate_text(TEXT_MODEL, "prompt")
        self.assertNotIn(SECRET, str(ctx.exception))


class ReferenceImageTests(unittest.TestCase):
    """Image-to-image: the character drawing goes IN, so the pieces that come
    back are the same character taken apart rather than a new one that merely
    resembles it."""

    JPEG = b"\xff\xd8\xff\xe0-not-really-a-jpeg"

    def make(self, fake: FakeRunner) -> GeminiClient:
        return GeminiClient(policy_with(), runner=fake,
                            environ={"GEMINI_API_KEY": SECRET})

    def body_of(self, fake: FakeRunner) -> dict:
        request, _ = fake.calls[0]
        return json.loads(request.data.decode("utf-8"))

    def test_a_reference_image_is_inlined_before_the_instruction(self):
        fake = FakeRunner(image_response())
        self.make(fake).generate_image(IMAGE_MODEL, "cut this up", images=[PNG])

        parts = self.body_of(fake)["contents"][0]["parts"]
        self.assertEqual(2, len(parts))
        self.assertEqual("image/png", parts[0]["inlineData"]["mimeType"])
        self.assertEqual(PNG, base64.b64decode(parts[0]["inlineData"]["data"]))
        # The instruction is last: it is about the image, so it follows it.
        self.assertEqual("cut this up", parts[1]["text"])

    def test_the_mime_type_comes_from_the_bytes_not_the_caller(self):
        fake = FakeRunner(image_response())
        self.make(fake).generate_image(IMAGE_MODEL, "p", images=[self.JPEG])
        parts = self.body_of(fake)["contents"][0]["parts"]
        self.assertEqual("image/jpeg", parts[0]["inlineData"]["mimeType"])

    def test_a_file_that_is_not_an_image_is_refused_before_the_request(self):
        fake = FakeRunner(image_response())
        with self.assertRaises(GeminiInputRejected):
            self.make(fake).generate_image(IMAGE_MODEL, "p", images=[b"GIF89a..."])
        self.assertEqual([], fake.calls)

    def test_an_oversized_reference_is_refused_before_the_request(self):
        # The SourceImage/ photos are 2-3 MB each and inlining one spends
        # quota that cannot be refunded, so the limit is checked locally.
        fake = FakeRunner(image_response())
        huge = PNG + b"\x00" * MAX_INPUT_IMAGE_BYTES
        with self.assertRaises(GeminiInputRejected):
            self.make(fake).generate_image(IMAGE_MODEL, "p", images=[huge])
        self.assertEqual([], fake.calls)

    def test_no_images_still_sends_exactly_one_text_part(self):
        fake = FakeRunner(image_response())
        self.make(fake).generate_image(IMAGE_MODEL, "just a prompt")
        parts = self.body_of(fake)["contents"][0]["parts"]
        self.assertEqual([{"text": "just a prompt"}], parts)

    def test_the_key_still_never_appears_in_the_body(self):
        fake = FakeRunner(image_response())
        self.make(fake).generate_image(IMAGE_MODEL, "p", images=[PNG])
        request, _ = fake.calls[0]
        self.assertNotIn(SECRET, request.data.decode("utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
