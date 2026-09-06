"""Policy-gated Gemini design generation using the standard library only.

The policy, free-model allowlist, and key gate are checked before transport is
invoked. Quota exhaustion is terminal for the request: this adapter never
selects a fallback Gemini model or a billed tier.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping

from company.orchestrator.policy import Policy, PolicyViolation


DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SIGNATURE = b"\xff\xd8\xff"

# A reference image is inlined as base64 in the request body, so its size is
# spent on every retry. The character sheets this is for are a few KB; the
# phone photos in SourceImage/ are 2-3 MB each. Refusing early beats sending
# one and finding out through a quota that cannot be refunded.
MAX_INPUT_IMAGE_BYTES = 4 * 1024 * 1024
LIMIT_PATTERNS = (
    r"\b429\b",
    r"quota",
    r"rate.?limit",
    r"resource_exhausted",
    r"too many requests",
)


class GeminiUnavailable(RuntimeError):
    """The Gemini endpoint could not complete the request."""


class GeminiKeyMissing(RuntimeError):
    """The human-provided key is absent from the policy-named variable."""


class GeminiModelNotAllowed(RuntimeError):
    """The requested model is outside the policy's free-tier allowlist."""


class GeminiLimited(RuntimeError):
    """Free-tier quota is exhausted; callers must degrade, never spend."""


class GeminiResponseError(RuntimeError):
    """Gemini answered, but did not provide a valid requested generation."""


class GeminiInputRejected(ValueError):
    """A reference image was not something this adapter will upload."""


def _image_mime(data: bytes) -> str:
    """The mime type for a reference image, from its own bytes.

    Deliberately not from the file extension: a .png that is really a 3 MB
    JPEG would be declared wrongly to the API and rejected after the request
    was already spent.
    """
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if data.startswith(JPEG_SIGNATURE):
        return "image/jpeg"
    raise GeminiInputRejected("Reference images must be PNG or JPEG.")


def _looks_limited(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(pattern, lowered) for pattern in LIMIT_PATTERNS)


@dataclass
class GeminiClient:
    policy: Policy
    base_url: str = DEFAULT_BASE_URL
    timeout_seconds: float = 120.0
    environ: Mapping[str, str] | None = field(default=None, repr=False)
    # Injectable for tests, like CodexRunner.runner. The callable receives a
    # urllib Request and timeout and may return (status, content_type, bytes).
    runner: object = field(default=None, repr=False)

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if self.runner is None:
            self.runner = self._run_request

    # ---- policy and secret gates ---------------------------------------

    @property
    def key_env_name(self) -> str:
        name = self.policy.raw.get("gemini_api_key_env")
        if not isinstance(name, str) or not name:
            raise PolicyViolation(
                "Gemini is blocked: policy 'gemini_api_key_env' is not configured."
            )
        # A blocked key is never read, even if a policy typo points Gemini at it.
        if name in self.policy.blocked_env_keys:
            raise PolicyViolation(
                f"Gemini is blocked: policy key variable {name} is blocked."
            )
        return name

    def _source_env(self, environ: Mapping[str, str] | None = None) -> Mapping[str, str]:
        if environ is not None:
            return environ
        return os.environ if self.environ is None else self.environ

    def key_status(self, environ: Mapping[str, str] | None = None) -> str:
        """Reports only presence, never the credential value."""
        name = self.key_env_name
        state = "PRESENT" if self._source_env(environ).get(name) else "ABSENT"
        return f"{name}: {state}"

    def _api_key(self) -> str:
        name = self.key_env_name
        key = self._source_env().get(name)
        if not key:
            raise GeminiKeyMissing(
                f"Gemini key is ABSENT in {name}; initial_gemini_login is a HUMAN_GATE."
            )
        return key

    def _require_request_allowed(self, model: str) -> str:
        self.policy.require("allow_gemini_design", "Gemini design generation")
        allowed = self.policy.raw.get("gemini_free_tier_models")
        free_models = (
            [item for item in allowed if isinstance(item, str)]
            if isinstance(allowed, list) else []
        )
        if model not in free_models:
            raise GeminiModelNotAllowed(
                f"Gemini model '{model}' is not in policy gemini_free_tier_models: "
                f"{free_models}"
            )
        return self._api_key()

    def status_summary(self, environ: Mapping[str, str] | None = None) -> str:
        if not self.policy.allows("allow_gemini_design"):
            return "Gemini BLOCKED: policy allow_gemini_design is not true"
        status = self.key_status(environ)
        if status.endswith("ABSENT"):
            return f"Gemini GATED: {status}; HUMAN_GATE initial_gemini_login"
        return f"Gemini READY: {status}"

    # ---- transport ------------------------------------------------------

    @staticmethod
    def _run_request(request: urllib.request.Request, timeout: float) -> tuple[int, str, bytes]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.getcode() or 200)
            content_type = response.headers.get_content_type()
            return status, content_type, response.read()

    @staticmethod
    def _normalise_result(result: Any) -> tuple[int, str, bytes]:
        if isinstance(result, tuple) and len(result) == 3:
            status, content_type, body = result
            return int(status), str(content_type), bytes(body)
        if hasattr(result, "read"):
            status = int(result.getcode() or 200)
            headers = getattr(result, "headers", {})
            content_type = headers.get_content_type() if hasattr(headers, "get_content_type") else headers.get("Content-Type", "")
            return status, str(content_type), bytes(result.read())
        # Byte-only fakes match the older Ollama seam; the real transport
        # always supplies a content type, so tests can opt into stricter tuples.
        if isinstance(result, bytes):
            return 200, "application/json", result
        raise TypeError("unsupported Gemini runner result")

    @staticmethod
    def _inline_parts(images: "list[bytes] | None") -> list[dict[str, Any]]:
        parts: list[dict[str, Any]] = []
        for data in images or []:
            if not isinstance(data, (bytes, bytearray)) or not data:
                raise GeminiInputRejected("Reference image data is empty.")
            if len(data) > MAX_INPUT_IMAGE_BYTES:
                raise GeminiInputRejected(
                    f"Reference image is {len(data)} bytes; the limit is "
                    f"{MAX_INPUT_IMAGE_BYTES}. Downscale it before sending."
                )
            parts.append({
                "inlineData": {
                    "mimeType": _image_mime(bytes(data)),
                    "data": base64.b64encode(bytes(data)).decode("ascii"),
                }
            })
        return parts

    def _post(self, model: str, prompt: str, *, image: bool,
              images: "list[bytes] | None" = None) -> dict[str, Any]:
        key = self._require_request_allowed(model)
        # Reference images first, instruction last: the model is being asked
        # what to do WITH them, and the ordering is what says so.
        parts = self._inline_parts(images) + [{"text": prompt}]
        payload: dict[str, Any] = {"contents": [{"parts": parts}]}
        if image:
            payload["generationConfig"] = {"responseModalities": ["IMAGE"]}

        encoded_model = urllib.parse.quote(model, safe="")
        request = urllib.request.Request(
            f"{self.base_url}/models/{encoded_model}:generateContent",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-goog-api-key": key,
            },
        )

        try:
            status, content_type, body = self._normalise_result(
                self.runner(request, self.timeout_seconds)  # type: ignore[operator]
            )
        except urllib.error.HTTPError as exc:
            try:
                error_body = exc.read().decode("utf-8", errors="replace")
            except Exception:
                error_body = ""
            if exc.code == 429 or _looks_limited(error_body):
                raise GeminiLimited(
                    "Gemini free-tier quota or rate limit reached; refusing to retry "
                    "another model, tier, or project."
                ) from None
            raise GeminiUnavailable(f"Gemini HTTP request failed with status {exc.code}.") from None
        except Exception:
            # Transport exception text is deliberately omitted because a custom
            # runner could include its Request (and therefore its secret header).
            raise GeminiUnavailable("Gemini request failed before a valid response.") from None

        decoded_body = body.decode("utf-8", errors="replace")
        if status == 429 or (status >= 400 and _looks_limited(decoded_body)):
            raise GeminiLimited(
                "Gemini free-tier quota or rate limit reached; refusing to retry "
                "another model, tier, or project."
            )
        if status < 200 or status >= 300:
            raise GeminiUnavailable(f"Gemini HTTP request failed with status {status}.")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            raise GeminiResponseError("Gemini returned an unexpected response content type.")
        if not body:
            raise GeminiResponseError("Gemini returned an empty response.")
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise GeminiResponseError("Gemini returned malformed JSON.") from None
        if not isinstance(data, dict):
            raise GeminiResponseError("Gemini returned an unexpected JSON response.")
        error = data.get("error")
        if error and _looks_limited(json.dumps(error)):
            raise GeminiLimited(
                "Gemini free-tier quota or rate limit reached; refusing to retry "
                "another model, tier, or project."
            )
        if error:
            raise GeminiResponseError("Gemini returned an error response.")
        return data

    # ---- generation -----------------------------------------------------

    @staticmethod
    def _parts(data: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError):
            raise GeminiResponseError("Gemini response contained no generation.") from None
        if not isinstance(parts, list):
            raise GeminiResponseError("Gemini response contained malformed generation parts.")
        return [part for part in parts if isinstance(part, dict)]

    def generate_text(self, model: str, prompt: str,
                      images: "list[bytes] | None" = None) -> str:
        data = self._post(model, prompt, image=False, images=images)
        text = "".join(
            part.get("text", "") for part in self._parts(data)
            if isinstance(part.get("text"), str)
        ).strip()
        if not text:
            raise GeminiResponseError("Gemini response contained no generated text.")
        return text

    def generate_image(self, model: str, prompt: str,
                       images: "list[bytes] | None" = None) -> bytes:
        """A PNG from Gemini. With `images`, this is image-to-image: the
        references go in the same request and the prompt says what to make
        of them - which is how one character drawing becomes the separate
        pieces a rig needs, rather than a new character that merely resembles it."""
        data = self._post(model, prompt, image=True, images=images)
        for part in self._parts(data):
            inline = part.get("inlineData")
            if not isinstance(inline, dict):
                continue
            if inline.get("mimeType") != "image/png":
                raise GeminiResponseError("Gemini returned an unexpected image content type.")
            encoded = inline.get("data")
            if not isinstance(encoded, str) or not encoded:
                raise GeminiResponseError("Gemini returned empty image data.")
            try:
                png = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise GeminiResponseError("Gemini returned malformed image data.") from None
            if not png.startswith(PNG_SIGNATURE):
                raise GeminiResponseError("Gemini image data was not a PNG.")
            return png
        raise GeminiResponseError("Gemini response contained no generated PNG image.")
