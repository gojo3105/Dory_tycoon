"""Renders Reports/dashboard.html - which AI can work, and on what.

WHY THIS EXISTS. Eight AI-ish tools are installed on the build PC, and asking
"what is connected?" gets you a list of eight. That list is misleading: the
only local LLM installed does not fit in this machine's RAM, the image model
has no weights on disk yet, Blender has no adapter, and every paid API is
switched off by policy. The useful question is not what is installed but what
can actually do work right now, and what is holding back the rest.

So every row on this page carries its EVIDENCE - the file the status was read
from. Where there is no file, the row says "근거 없음" rather than guessing.
That is section 38 made visible: a claim with nothing behind it is not a
status, and a dashboard that quietly upgrades "not checked" to "OK" is worse
than no dashboard.

Nothing here launches a tool. The served control panel asks the loopback-only
Ollama API for its current installed-model inventory; everything else reads
committed files. For Gemini it reports only whether the policy-named
environment variable contains a key; the key itself is never retained or
rendered.
"""

from __future__ import annotations

import base64
import html
import io
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from company.orchestrator.agent_registry import AgentRegistry, AgentRegistryError, RegisteredAgent
from company.orchestrator import orders
from company.orchestrator import progress as progress_mod
from company.orchestrator.hardware import HardwareProfile
from company.orchestrator.ollama_client import OllamaClient, OllamaUnavailable
from company.orchestrator.teamwork import Task, TaskBoard

# Optional: only used to shrink the multi-megabyte reference photos. Absent on
# a machine that never installed it, and the gallery degrades to "too large to
# embed" rather than failing - a dashboard is not worth a hard dependency.
try:  # pragma: no cover - availability is the thing being handled
    from PIL import Image
except ImportError:
    Image = None

# Files at or below this go in untouched, which keeps sprite art pixel-exact.
EMBED_AS_IS_BYTES = 90_000
# Long edge of a generated thumbnail. 240 is enough to recognise a character
# pose on a phone without pushing the page past a megabyte.
THUMB_EDGE = 240

# Status vocabulary. Deliberately four, not three: "not checked" is its own
# state and must never collapse into either OK or broken.
READY = "ready"        # can do work now
GATED = "gated"        # blocked on a person or a one-off setup step
BLOCKED = "blocked"    # cannot work on this machine / forbidden by policy
UNKNOWN = "unknown"    # no evidence in the repository

STATE_LABEL = {
    READY: "작업 가능",
    GATED: "대기 중",
    BLOCKED: "사용 불가",
    UNKNOWN: "확인 불가",
}

# Agent names can include a model id, so departments are assigned by prefix.
# Keep this as the single mapping table: new integrations that do not appear
# here remain visible in the fallback department instead of disappearing.
DEPARTMENT_BY_PREFIX = {
    "Claude Code": "dev",
    "Codex CLI": "dev",
    "Gemini": "design",
    "Stable Diffusion": "design",
    "Qwen-Image": "design",
    "Ollama": "lab",
    "Blender": "modeling",
    "유료 API": "outsource",
}

DEPARTMENT_LABEL = {
    "dev": "기획개발실",
    "design": "디자인실",
    "lab": "사내 연구소",
    "modeling": "3D 모델링실",
    "outsource": "외주",
    "etc": "기타",
}


@dataclass
class Agent:
    name: str
    role: str
    state: str
    detail: str
    version: str = ""
    evidence: list[str] = field(default_factory=list)


@dataclass
class Snapshot:
    """Everything the page shows, read once so rendering stays pure."""
    generated_at: str
    profile: dict[str, Any]
    policy: dict[str, Any]
    licences: dict[str, str]
    tasks: list[dict[str, Any]]
    agents: list[Agent]
    builds: list[dict[str, str]]
    build_report_at: str
    errors: dict[str, int]
    error_report_at: str
    games: list[dict[str, Any]]
    commits: list[dict[str, str]]
    gallery: list[dict[str, Any]]
    art_plan: dict[str, Any]
    missing: list[str]
    # Reports/sync-status/latest.txt and Reports/runs/latest.txt, parsed.
    # Empty dict = file absent, which renders as 확인 불가 - never as fine.
    sync_status: dict[str, str] = field(default_factory=dict)
    last_run: dict[str, str] = field(default_factory=dict)
    ollama_models: list[dict[str, Any]] = field(default_factory=list)
    ollama_error: str = ""
    gemini_adapter: bool = False
    blender_adapter: bool = False
    image_adapter: bool = False
    office_image: str = ""
    company_roles: list[RegisteredAgent] = field(default_factory=list)


# ---- reading -------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any]:
    """Missing or unparsable reads as empty. The caller reports the absence."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (json.JSONDecodeError, OSError):
        return {}


def _licence_status(registry: dict[str, Any]) -> dict[str, str]:
    return {
        entry.get("id", ""): entry.get("status", "UNKNOWN")
        for entry in registry.get("entries", [])
        if isinstance(entry, dict)
    }


def read_live_ollama_models(
    company_root: Path, profile: dict[str, Any], policy: dict[str, Any],
    licences: dict[str, str],
) -> tuple[list[dict[str, Any]], str]:
    """Read installed models from Ollama itself, never the stale scan inventory."""
    try:
        client = OllamaClient(
            local_only=policy.get("ollama_local_only") is True,
            registry_path=company_root / "config" / "LICENSE_REGISTRY.json",
            state_path=company_root / "company" / "state" / "company_state.json",
            timeout=2.0,
        )
        installed = client.list_models()
        if not installed:
            return [], ""
        active = client.active_model()
        approved = client.approved_models()
    except (OSError, ValueError, OllamaUnavailable) as exc:
        return [], str(exc)

    hardware = HardwareProfile(profile)
    rows = []
    for model in installed:
        licence = "APPROVED" if model.name in approved else licences.get(
            model.name, "UNKNOWN")
        fit, fit_reason = hardware.model_fit(model.size_gb)
        reasons = []
        if licence != "APPROVED":
            reasons.append(f"라이선스 {licence}")
        if fit not in ("VIABLE", "LIMITED"):
            reasons.append(f"RAM {fit}: {fit_reason}")
        rows.append({
            "name": model.name,
            "size_gb": model.size_gb,
            "active": model.name == active,
            "enabled": not reasons,
            "reason": " · ".join(reasons),
        })
    return rows, ""


def _report_timestamp(text: str) -> str:
    match = re.search(r"^Generated:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def read_builds(path: Path) -> tuple[list[dict[str, str]], str]:
    """Real APK/AAB files, from the report that scans the PC's Builds folder.

    This is the only evidence of a build that reaches the repository - there
    is no GitHub Actions API access here - so a game with no line in this file
    has no verified APK, whatever any other document claims.
    """
    if not path.is_file():
        return [], ""

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    builds = []
    pattern = re.compile(
        r"^-\s+\[(?P<where>[^\]]+)\]\s+(?P<file>\S+)\s+"
        r"\((?P<size>[\d.]+\s*[KMG]B),\s*sha256:(?P<sha>[0-9A-Fa-f]+),\s*"
        r"built (?P<built>[^)]+)\)",
        re.MULTILINE,
    )
    for match in pattern.finditer(text):
        name = match.group("file").replace("\\", "/")
        game = ""
        parts = name.split("/")
        if "Builds" in parts:
            index = parts.index("Builds")
            if index + 1 < len(parts):
                game = parts[index + 1]
        builds.append({
            "game": game,
            "file": parts[-1],
            "path": name,
            "size": match.group("size"),
            "sha": match.group("sha"),
            "built": match.group("built").strip(),
            "where": match.group("where"),
        })
    return builds, _report_timestamp(text)


def read_errors(path: Path) -> tuple[dict[str, int], str]:
    """Compile errors, obsolete-API warnings and runtime exceptions."""
    if not path.is_file():
        return {}, ""

    text = path.read_text(encoding="utf-8-sig", errors="replace")
    counts: dict[str, int] = {}
    for label, key in (
        ("Compile errors", "compile"),
        ("Obsolete API warnings", "obsolete"),
        ("Runtime exceptions", "runtime"),
    ):
        # The count is the LAST bracket on the heading line, not the first:
        # "## Obsolete API warnings (CS0618) (0)" carries the rule id in its
        # own brackets first. A pattern that stopped at the first "(" silently
        # matched nothing here, and the renderer's default turned that into a
        # confident "0" - the exact false-clean this page exists to prevent.
        match = re.search(rf"^##\s+{re.escape(label)}.*\((\d+)\)\s*$", text, re.MULTILINE)
        if match:
            counts[key] = int(match.group(1))
    return counts, _report_timestamp(text)


def read_header_fields(path: Path) -> dict[str, str]:
    """'Key: value' header lines of a report, up to its first '## ' section.

    Shared by the sync-status and run-log readers because both files use the
    same shape as the two reports that already work - a third format would
    be a third parser.
    """
    if not path.is_file():
        return {}
    fields: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    for line in text.splitlines():
        # Stop where the captured program output begins - that body can
        # contain anything, including lines that look like headers. A
        # '## Result' section is still header material and is read through:
        # the first run-log writer on the PC (Codex's) put its fields there.
        if line.startswith("## ") and "result" not in line.lower():
            break
        match = re.match(r"^([A-Za-z][A-Za-z -]*?):\s*(.*)$", line)
        if match:
            fields[match.group(1).strip()] = match.group(2).strip()
    # Aliases from that earlier writer, so its records still read.
    if "Outcome" not in fields and "Status" in fields:
        fields["Outcome"] = {"SUCCESS": "OK", "FAILURE": "FAILED"}.get(
            fields["Status"].upper(), fields["Status"])
    if "Exit" not in fields and "Exit code" in fields:
        fields["Exit"] = fields["Exit code"]
    return fields


def hours_since(stamp: str, now: datetime | None = None) -> float | None:
    """Age of a 'YYYY-MM-DD HH:MM:SS' stamp in hours, or None if unreadable."""
    try:
        then = datetime.strptime(stamp.strip()[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    current = now or datetime.now()
    return max(0.0, (current - then).total_seconds() / 3600)


# The scheduled sync runs every 15 minutes but commits its status only when
# the outcome changes or after a 6-hour heartbeat, so a committed stamp up to
# ~6h old is normal. Past this, the scheduled task itself has probably stopped.
SYNC_STALE_HOURS = 7.0


def read_commits(repo_root: Path, limit: int = 8) -> list[dict[str, str]]:
    """Recent history. Best effort - a dashboard is not worth failing over."""
    try:
        completed = subprocess.run(
            ["git", "log", f"-{limit}", "--date=short",
             "--pretty=format:%h\x1f%ad\x1f%s"],
            cwd=str(repo_root), capture_output=True, text=True, timeout=20, check=True,
            # This project's commit subjects contain Korean, so decoding with
            # the machine's locale (cp949 on the build PC) raises and the whole
            # dashboard fails to render over a log line.
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return []

    commits = []
    for line in completed.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 3:
            commits.append({"sha": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


# ---- the roster ----------------------------------------------------------


def build_agents(profile: dict[str, Any], policy: dict[str, Any],
                 licences: dict[str, str], company_root: Path) -> list[Agent]:
    """Work out what each AI can actually do, and say what the evidence was.

    Every branch here answers one question: is there a file in this repository
    that says this thing works? "Installed" is never enough on its own - a
    model that is installed, licensed and too big for RAM is still unusable,
    and the row has to say which of the three failed.
    """
    tools = profile.get("tools", {})
    profile_evidence = "config/HARDWARE_PROFILE.json"
    policy_evidence = "config/company_policy.json"
    licence_evidence = "config/LICENSE_REGISTRY.json"
    has_profile = bool(profile)

    ram_total = float(profile.get("hardware", {}).get("ramTotalGb") or 0)
    agents: list[Agent] = []

    def tool(name: str) -> dict[str, Any]:
        return tools.get(name, {}) if isinstance(tools.get(name), dict) else {}

    # --- Claude Code ---
    claude = tool("claude")
    if not has_profile:
        agents.append(Agent("Claude Code", "설계 · 구현 · 리뷰", UNKNOWN,
                            "환경 리포트가 없습니다. detect-environment.ps1을 먼저 실행하세요.",
                            evidence=[]))
    elif claude.get("installed") and policy.get("use_claude_code") is True:
        agents.append(Agent(
            "Claude Code", "설계 · 구현 · 리뷰", READY,
            "코드를 직접 작성합니다. 커밋은 사람 승인 후에만 합니다.",
            str(claude.get("version", "")), [profile_evidence, policy_evidence]))
    else:
        agents.append(Agent(
            "Claude Code", "설계 · 구현 · 리뷰", BLOCKED,
            "설치되지 않았거나 정책 use_claude_code 가 켜져 있지 않습니다.",
            str(claude.get("version", "")), [profile_evidence, policy_evidence]))

    # --- Codex ---
    codex = tool("codex")
    if not codex.get("installed"):
        agents.append(Agent("Codex CLI", "공동 개발 · 독립 리뷰", UNKNOWN,
                            "환경 리포트에 codex 항목이 없습니다.", "", [profile_evidence]))
    elif policy.get("use_codex_subscription") is not True:
        agents.append(Agent(
            "Codex CLI", "공동 개발 · 독립 리뷰", BLOCKED,
            "정책 use_codex_subscription 이 false 입니다.",
            str(codex.get("version", "")), [policy_evidence]))
    else:
        writes = policy.get("allow_codex_write") is True
        # Login is deliberately not inferred: ~/.codex/auth.json is on the
        # policy's secrets_never_touched list, so nothing here may read it,
        # and initial_codex_login is a HUMAN_GATE.
        agents.append(Agent(
            "Codex CLI",
            "공동 개발 · 독립 리뷰" if writes else "독립 리뷰 전용",
            GATED,
            ("작업판에서 작업을 넘겨받아 코드를 직접 씁니다 (workspace-write). "
             if writes else "쓰기 권한 없음 - 정책 allow_codex_write 가 false 입니다. ")
            + "로그인 상태는 여기서 확인하지 않습니다. PC에서 "
              "'orchestrator codex --doctor' 로 확인하세요.",
            str(codex.get("version", "")), [profile_evidence, policy_evidence]))

    # --- Gemini design ---
    # Only the policy-selected environment variable is inspected. Its value is
    # never copied into the snapshot, logs, or rendered HTML.
    gemini_key_env = str(policy.get("gemini_api_key_env") or "")
    if policy.get("allow_gemini_design") is not True:
        agents.append(Agent(
            "Gemini", "디자인 · 이미지 생성", BLOCKED,
            "정책 allow_gemini_design 이 true 가 아닙니다.",
            "", [policy_evidence]))
    elif gemini_key_env and os.environ.get(gemini_key_env):
        agents.append(Agent(
            "Gemini", "디자인 · 이미지 생성", READY,
            f"환경 변수 {gemini_key_env} 에 키가 있습니다. 값은 표시하지 않습니다.",
            "", [policy_evidence]))
    else:
        agents.append(Agent(
            "Gemini", "디자인 · 이미지 생성", GATED,
            f"initial_gemini_login 게이트가 남아 있습니다. {gemini_key_env or '정책에 지정된 환경 변수'}에 키가 없습니다.",
            "", [policy_evidence]))

    # --- Ollama and whatever model is actually installed ---
    api = profile.get("ollamaApi", {}) if isinstance(profile.get("ollamaApi"), dict) else {}
    models = api.get("models") or []
    if not tool("ollama").get("installed"):
        agents.append(Agent("Ollama (로컬 LLM)", "로컬 추론", UNKNOWN,
                            "환경 리포트에 ollama 항목이 없습니다.", "", [profile_evidence]))
    elif not models:
        agents.append(Agent(
            "Ollama (로컬 LLM)", "로컬 추론", GATED,
            "설치된 모델이 없습니다. 라이선스 확인 후 3B급 모델을 받아야 합니다.",
            str(tool("ollama").get("version", "")), [profile_evidence]))
    else:
        for model in models:
            model_id = str(model.get("name", "?"))
            size = float(model.get("sizeGb") or 0)
            status = licences.get(model_id, "UNKNOWN")
            reasons = []
            # Both checks run: a model can pass the licence gate and still be
            # unloadable, and naming only the first failure hides the second.
            if status != "APPROVED":
                reasons.append(f"라이선스 {status}")
            if ram_total and size >= ram_total * 0.8:
                reasons.append(f"{size:.1f} GB 모델 / 전체 RAM {ram_total:.1f} GB - 적재 불가")
            agents.append(Agent(
                f"Ollama · {model_id}", "로컬 추론",
                BLOCKED if reasons else READY,
                " · ".join(reasons) if reasons
                else f"{model.get('parameterSize', '')} {model.get('quantization', '')}".strip(),
                str(tool("ollama").get("version", "")),
                [profile_evidence, licence_evidence]))

    # --- local image generation ---
    generated = company_root / "generated"
    produced = list(generated.rglob("*.png")) if generated.is_dir() else []
    sd_status = licences.get("stable-diffusion-v1-5", "UNKNOWN")
    if policy.get("allow_local_image_generation") is not True:
        agents.append(Agent(
            "Stable Diffusion 1.5 + IP-Adapter", "캐릭터 스프라이트 생성", BLOCKED,
            "정책 allow_local_image_generation 이 false 입니다.",
            "", [policy_evidence]))
    elif produced:
        agents.append(Agent(
            "Stable Diffusion 1.5 + IP-Adapter", "캐릭터 스프라이트 생성", READY,
            f"생성된 이미지 {len(produced)}장이 저장소에 있습니다.",
            "", [licence_evidence, "AI_GAME_COMPANY/generated/"]))
    else:
        agents.append(Agent(
            "Stable Diffusion 1.5 + IP-Adapter", "캐릭터 스프라이트 생성", GATED,
            f"라이선스 {sd_status}. 가중치가 아직 없습니다 - 이 컨테이너에서는 "
            "huggingface.co 가 프록시에서 403 이라 받을 수 없고, PC에서 "
            "setup-image-generation.ps1 을 실행해야 합니다.",
            "", [licence_evidence, policy_evidence]))

    # --- Qwen-Image: approved, and still unusable here ---
    if licences.get("qwen-image", "").startswith("APPROVED_BUT_UNUSABLE"):
        agents.append(Agent(
            "Qwen-Image", "고품질 이미지 생성", BLOCKED,
            f"라이선스는 통과했지만 약 18.5 GB 가 필요합니다. 이 PC 전체 RAM 은 "
            f"{ram_total:.1f} GB 입니다.",
            "", [licence_evidence]))

    # --- Blender: installed, but nothing drives it yet ---
    blender = tool("blender")
    if blender.get("installed"):
        has_adapter = (company_root / "company" / "orchestrator" / "blender_runner.py").is_file()
        agents.append(Agent(
            "Blender", "3D 캐릭터 · 렌더",
            READY if has_adapter else UNKNOWN,
            "어댑터 연결됨." if has_adapter
            else "설치는 확인됐지만 이걸 호출하는 코드가 아직 없습니다 (blender_runner.py 미구현).",
            str(blender.get("version", "")), [profile_evidence]))

    # --- paid APIs: off by policy, and that is the desired state ---
    present = [k for k, v in (profile.get("paidApiKeysPresent") or {}).items() if v]
    agents.append(Agent(
        "유료 API (OpenAI · Anthropic · Google 등)", "사용 안 함", BLOCKED,
        ("정책상 차단되어 있습니다. 환경에 있는 키: " + ", ".join(present) +
         " - 존재해도 사용하지 않으며, 자식 프로세스 환경에서 제거됩니다.")
        if present else
        "정책상 차단되어 있고, 환경에 키도 없습니다. 비용이 발생하는 경로가 없습니다.",
        "", [profile_evidence, policy_evidence]))

    return agents


def read_games(repo_root: Path, builds: list[dict[str, str]],
               total: int = 10) -> list[dict[str, Any]]:
    """The 10-game plan against what actually exists.

    A game counts as done only with BOTH a written report and a real APK in
    the build report. Either one alone is a claim, not a finished game.
    """
    spec_dir = repo_root / "GameSpecs"
    specs = sorted(p.stem for p in spec_dir.glob("*.json")) if spec_dir.is_dir() else []
    built = {b["game"] for b in builds if b.get("game")}

    games = []
    for index in range(1, total + 1):
        game_id = f"game{index:02d}"
        has_spec = game_id in specs
        has_report = (repo_root / "Reports" / f"Game{index:02d}_Report.txt").is_file()
        has_apk = game_id in built
        if has_report and has_apk:
            state = "done"
        elif has_spec:
            state = "active"
        else:
            state = "todo"
        games.append({"id": game_id, "state": state, "spec": has_spec,
                      "report": has_report, "apk": has_apk})
    return games


def _data_uri(path: Path) -> tuple[str, str]:
    """(data URI, note). Embeds so one HTML file works offline and as an Artifact.

    A published Artifact cannot load an image from the user's disk, and a local
    file opened from Reports/ would need a relative path that breaks the moment
    the file is copied anywhere. Inlining is what makes the same bytes work in
    both places.
    """
    raw = path.read_bytes()
    suffix = path.suffix.lower()

    if len(raw) <= EMBED_AS_IS_BYTES and suffix in (".png", ".gif", ".webp"):
        mime = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}[suffix[1:]]
        return f"data:{mime};base64,{base64.b64encode(raw).decode()}", "원본"

    if Image is None:
        return "", "Pillow 미설치 - 축소할 수 없어 생략"

    try:
        with Image.open(io.BytesIO(raw)) as image:
            # A cut-out with transparency must stay PNG. JPEG has no alpha, so
            # thumbnailing one flattens it onto black - and the biggest images
            # in this gallery are exactly the character drawings, which is how
            # player.png came to render as a hedgehog in a black box. Scaled
            # down to 240px a PNG is a few KB anyway, so nothing is saved by
            # the flatten.
            transparent = (image.mode in ("RGBA", "LA")
                           or (image.mode == "P" and "transparency" in image.info))
            image = image.convert("RGBA" if transparent else "RGB")
            image.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
            buffer = io.BytesIO()
            if transparent:
                image.save(buffer, format="PNG", optimize=True)
            else:
                image.save(buffer, format="JPEG", quality=74, optimize=True)
    except OSError as exc:
        return "", f"읽을 수 없음 ({exc})"

    encoded = base64.b64encode(buffer.getvalue()).decode()
    mime = "image/png" if transparent else "image/jpeg"
    return f"data:{mime};base64,{encoded}", "축소본"


def _wide_office_data_uri(path: Path) -> str:
    """Embed the office at hero resolution without shipping a 2.6 MB PNG."""
    if not path.is_file():
        return ""
    if Image is None:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    try:
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((1600, 900), Image.LANCZOS)
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=87, optimize=True)
    except (OSError, ValueError):
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _short_name(name: str, keep: int = 17) -> str:
    """Trim a filename from the LEFT, so what distinguishes it survives.

    The reference photos are all KakaoTalk_20260826_014200658_NN.png - clipped
    from the right they render as fifteen identical captions, and the two
    digits that say which one it is are the part that gets cut.
    """
    if len(name) <= keep + 3:
        return name
    return "…" + name[-keep:]


def _image_items(paths: list[Path], repo_root: Path, pixel_art: bool) -> list[dict[str, Any]]:
    items = []
    for path in sorted(paths):
        try:
            size = path.stat().st_size
        except OSError:
            continue

        dimensions = ""
        if Image is not None:
            try:
                with Image.open(path) as image:
                    dimensions = f"{image.width}x{image.height}"
            except OSError:
                dimensions = ""

        src, note = _data_uri(path)
        items.append({
            "name": path.name,
            "label": _short_name(path.name),
            "rel": str(path.relative_to(repo_root)).replace("\\", "/"),
            "src": src,
            "note": note,
            "dimensions": dimensions,
            "kb": size / 1024,
            "pixel_art": pixel_art,
        })
    return items


def read_gallery(repo_root: Path) -> list[dict[str, Any]]:
    """The project's images, grouped by where they come from.

    Three groups on purpose, because they answer different questions: what the
    game currently draws, what the character is supposed to look like, and
    what the image model has actually produced. The third being empty is
    itself the useful fact - it is the whole reason that AI shows as 대기 중.
    """
    company = repo_root / "AI_GAME_COMPANY"

    def png(directory: Path, recursive: bool = False) -> list[Path]:
        if not directory.is_dir():
            return []
        pattern = "**/*" if recursive else "*"
        return [p for p in directory.glob(pattern)
                if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]

    art_root = repo_root / "Assets" / "Common" / "Art"
    art = [
        path for path in png(art_root, recursive=True)
        if "dashboard" not in {
            part.lower() for part in path.relative_to(art_root).parts
        }
    ]
    generated_character = png(repo_root / "Assets" / "Common" / "Character" / "Generated")
    source = png(repo_root / "Assets" / "Common" / "Character" / "SourceImage")
    ai_made = png(company / "generated", recursive=True)

    return [
        {
            "title": "게임에 들어간 아트",
            "note": "Assets/Common/Art/ · 실제로 화면에 그려지는 스프라이트",
            "empty": "아직 없습니다. UI 스프라이트는 PC에서 파이프라인을 돌리면 생성됩니다.",
            "items": _image_items(art + generated_character, repo_root, pixel_art=True),
        },
        {
            "title": "AI가 생성한 이미지",
            "note": "AI_GAME_COMPANY/generated/ · Stable Diffusion + IP-Adapter 출력",
            "empty": ("아직 없습니다. 가중치가 없어서 한 장도 만들지 못했습니다 - "
                      "PC에서 setup-image-generation.ps1 을 실행해야 합니다."),
            "items": _image_items(ai_made, repo_root, pixel_art=False),
        },
        {
            "title": "캐릭터 원본 (사용자 제공)",
            "note": "Assets/Common/Character/SourceImage/ · 도리의 기준 이미지. 지우거나 바꾸지 않습니다.",
            "empty": "원본 이미지가 없습니다.",
            "items": _image_items(source, repo_root, pixel_art=False),
        },
    ]


def read_art_plan(repo_root: Path) -> dict[str, Any]:
    """The art that is planned but not in the tree, and why.

    WHY THIS IS ON THE PAGE. The gallery showing four sprites is correct -
    there are four - but "correct" and "obvious" are different things, and the
    first question anyone asks is where the background and the buttons are.
    The answer already exists in art-mapping.json; it just was not visible.
    Three different reasons live in that file and they are not
    interchangeable: a mapped target with no file is a step nobody has run, a
    queued one is blocked on code that does not exist yet, and a deliberately
    unmapped one is a decision.
    """
    mapping = _read_json(repo_root / "AI_GAME_COMPANY" / "config" / "art-mapping.json")
    if not mapping:
        return {"present": [], "missing": [], "queued": [], "declined": [],
                "source": ""}

    present, missing = [], []
    for target in mapping.get("targets", []):
        if not isinstance(target, dict):
            continue
        path = str(target.get("target", ""))
        row = {"path": path, "pack": str(target.get("packId", "")),
               "file": str(target.get("sourceFile", ""))}
        (present if (repo_root / path).is_file() else missing).append(row)

    queued = [{"what": str(q.get("what", "")), "pack": str(q.get("packId", "")),
               "why": str(q.get("why", ""))}
              for q in mapping.get("queued", []) if isinstance(q, dict)]

    declined = [{"path": str(d.get("target", "")), "why": str(d.get("why", ""))}
                for d in mapping.get("deliberatelyNotMapped", []) if isinstance(d, dict)]

    return {"present": present, "missing": missing, "queued": queued,
            "declined": declined, "source": "AI_GAME_COMPANY/config/art-mapping.json"}


def collect(repo_root: Path, *, live_ollama: bool = False) -> Snapshot:
    company_root = repo_root / "AI_GAME_COMPANY"
    config = company_root / "config"

    profile = _read_json(config / "HARDWARE_PROFILE.json")
    policy = _read_json(config / "company_policy.json")
    registry = _read_json(config / "LICENSE_REGISTRY.json")
    board = _read_json(config / "TASKBOARD.json")

    builds, build_at = read_builds(repo_root / "Reports" / "build-status" / "latest.txt")
    errors, error_at = read_errors(repo_root / "Reports" / "errors" / "latest.txt")

    missing = [name for name, data in (
        ("config/HARDWARE_PROFILE.json", profile),
        ("config/company_policy.json", policy),
        ("config/LICENSE_REGISTRY.json", registry),
        ("config/TASKBOARD.json", board),
    ) if not data]
    company_roles: list[RegisteredAgent] = []
    try:
        company_roles = list(AgentRegistry.load(config / "AGENTS.json").list_agents())
    except AgentRegistryError as exc:
        missing.append(f"config/AGENTS.json ({exc})")
    if not builds:
        missing.append("Reports/build-status/latest.txt")

    licences = _licence_status(registry)
    ollama_models, ollama_error = (read_live_ollama_models(
        company_root, profile, policy, licences) if live_ollama else ([], ""))
    reports = repo_root / "Reports"
    return Snapshot(
        sync_status=read_header_fields(reports / "sync-status" / "latest.txt"),
        last_run=read_header_fields(reports / "runs" / "latest.txt"),
        generated_at=datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"),
        profile=profile,
        policy=policy,
        licences=licences,
        tasks=board.get("tasks", []),
        agents=build_agents(profile, policy, licences, company_root),
        builds=builds,
        build_report_at=build_at,
        errors=errors,
        error_report_at=error_at,
        games=read_games(repo_root, builds),
        commits=read_commits(repo_root),
        gallery=read_gallery(repo_root),
        art_plan=read_art_plan(repo_root),
        missing=missing,
        ollama_models=ollama_models,
        ollama_error=ollama_error,
        gemini_adapter=(company_root / "company" / "orchestrator" /
                        "gemini_client.py").is_file(),
        blender_adapter=(company_root / "company" / "orchestrator" /
                         "blender_runner.py").is_file(),
        image_adapter=(company_root / "tools" / "generate-sprite.py").is_file(),
        office_image=_wide_office_data_uri(
            repo_root / "Assets" / "Common" / "Art" / "Dashboard" / "ai_office_block_pixel_v2.png"),
        company_roles=company_roles,
    )


# ---- rendering -----------------------------------------------------------

def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


CSS = """
:root{
  --ground:#EBEEF2; --surface:#FFFFFF; --surface-2:#F5F7FA; --sunk:#E3E8EE;
  --ink:#17222E; --ink-2:#39485A; --muted:#61707F; --line:#D6DEE6;
  --accent:#12657F; --accent-soft:#DCEDF3;
  --ok:#0C6F58; --ok-soft:#DCF0E9;
  --gate:#8E5205; --gate-soft:#FAEBD6;
  --blocked:#A32B22; --blocked-soft:#F8E2E0;
  --unknown:#5A6472; --unknown-soft:#E6E9ED;
  /* Severity slot, overridden per element by the .s-* / .g-* classes below.
     Defined here so an element that somehow renders without one of those
     classes gets the neutral colour rather than no colour at all. */
  --st:var(--unknown); --st-soft:var(--unknown-soft); --legend:var(--unknown);
  --avatar-skin:#F2C6A8; --avatar-hair:#26384A; --avatar-shirt:var(--accent);
  --from-x:50%; --from-y:50%; --to-x:55%; --to-y:50%;
  --desk-x:50%; --desk-y:50%; --speed:14s; --delay:0s;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --ground:#0E1319; --surface:#161D25; --surface-2:#1C242E; --sunk:#111820;
    --ink:#E7EEF4; --ink-2:#BCC8D4; --muted:#8E9CAB; --line:#2A3542;
    --accent:#5FBBDA; --accent-soft:#123340;
    --ok:#43CCA1; --ok-soft:#0F3830;
    --gate:#E5A94A; --gate-soft:#3A2B12;
    --blocked:#F5837B; --blocked-soft:#3D1E1C;
    --unknown:#9AA6B4; --unknown-soft:#232B34;
  }
}
:root[data-theme="dark"]{
  --ground:#0E1319; --surface:#161D25; --surface-2:#1C242E; --sunk:#111820;
  --ink:#E7EEF4; --ink-2:#BCC8D4; --muted:#8E9CAB; --line:#2A3542;
  --accent:#5FBBDA; --accent-soft:#123340;
  --ok:#43CCA1; --ok-soft:#0F3830;
  --gate:#E5A94A; --gate-soft:#3A2B12;
  --blocked:#F5837B; --blocked-soft:#3D1E1C;
  --unknown:#9AA6B4; --unknown-soft:#232B34;
}

*{box-sizing:border-box;}
body{
  margin:0; background:
    radial-gradient(circle at 12% 8%,rgba(22,126,177,.30),transparent 32%),
    radial-gradient(circle at 88% 18%,rgba(238,133,74,.20),transparent 30%),
    linear-gradient(160deg,#07131f 0%,#0b2132 52%,#101928 100%);
  background-attachment:fixed; color:var(--ink);
  font-family:'Noto Sans KR','Archivo',-apple-system,'Malgun Gothic',sans-serif;
  font-size:15px; line-height:1.6; -webkit-font-smoothing:antialiased;
}
.wrap{max-width:1460px; margin:0 auto; padding:32px 28px 72px;}

h1,h2,h3{font-family:'Archivo','Noto Sans KR',sans-serif; text-wrap:balance; margin:0;}
h1{font-size:30px; font-weight:700; letter-spacing:-0.015em;}
h2{font-size:15px; font-weight:700; letter-spacing:0.09em; text-transform:uppercase;
   color:var(--muted);}
.mono{font-family:'JetBrains Mono',ui-monospace,'Cascadia Mono',monospace;
      font-variant-numeric:tabular-nums;}

/* ---- masthead ---- */
.mast{display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between;
      gap:16px; padding-bottom:20px; border-bottom:1px solid rgba(255,255,255,.32); color:#fff;}
.mast .sub{color:rgba(255,255,255,.72); font-size:14px; margin-top:6px;}
.stamp{font-size:12.5px; color:rgba(255,255,255,.68); text-align:right; line-height:1.7;}
.head h2{color:#eef7ff; text-shadow:0 1px 10px rgba(0,0,0,.25);}
.head .note{color:rgba(235,246,255,.68);}

.verdict{
  margin:24px 0 0; padding:18px 22px; border-radius:3px;
  background:var(--surface); border:1px solid var(--line);
  border-left:4px solid var(--accent);
  display:flex; flex-wrap:wrap; gap:8px 28px; align-items:baseline;
}
.verdict b{font-family:'Archivo','Noto Sans KR',sans-serif; font-size:17px;}
.verdict span{color:var(--ink-2); font-size:14px;}

section{margin-top:44px;}
.head{display:flex; align-items:baseline; justify-content:space-between; gap:16px;
      margin-bottom:14px;}
.head .note{font-size:12.5px; color:var(--muted);}

/* ---- roster ---- */
.roster{display:flex; flex-direction:column; gap:2px;
        background:var(--line); border:1px solid var(--line); border-radius:3px;
        overflow:hidden;}
.row{background:var(--surface); padding:16px 20px 16px 17px; border-left:3px solid var(--st);
     display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px 20px; align-items:start;}
.row .who{font-family:'Archivo','Noto Sans KR',sans-serif; font-weight:600; font-size:16px;}
.row .role{color:var(--muted); font-size:12.5px; letter-spacing:0.04em;}
.row .detail{grid-column:1/-1; color:var(--ink-2); font-size:14px; margin-top:4px;}
.row .ev{grid-column:1/-1; margin-top:8px; display:flex; flex-wrap:wrap; gap:6px;
         align-items:center;}
.ev .k{font-size:11px; letter-spacing:0.1em; text-transform:uppercase; color:var(--muted);}
.ev code{font-size:11.5px; padding:2px 7px; border-radius:2px;
         background:var(--sunk); color:var(--ink-2);}
.ev .none{font-size:11.5px; color:var(--blocked);}

.pill{display:inline-flex; align-items:center; gap:7px; white-space:nowrap;
      font-size:12px; font-weight:600; letter-spacing:0.05em;
      padding:4px 11px; border-radius:2px; background:var(--st-soft); color:var(--st);}
.pill::before{content:""; width:6px; height:6px; border-radius:50%; background:currentColor;}
.ver{font-size:12px; color:var(--muted); text-align:right; margin-top:3px;}

.s-ready{--st:var(--ok); --st-soft:var(--ok-soft);}
.s-gated{--st:var(--gate); --st-soft:var(--gate-soft);}
.s-blocked{--st:var(--blocked); --st-soft:var(--blocked-soft);}
.s-unknown{--st:var(--unknown); --st-soft:var(--unknown-soft);}

/* ---- department office ---- */
.virtual-office-shell{position:relative; overflow:hidden; border-radius:18px;
  border:1px solid rgba(255,255,255,.32); background:#07111c;
  box-shadow:0 28px 70px rgba(0,0,0,.38),0 0 0 1px rgba(80,190,255,.12);}
.virtual-office-stage{position:relative; overflow:hidden;}
.virtual-office-image{display:block; width:100%; aspect-ratio:16/9; object-fit:cover;}
.virtual-office-vignette{position:absolute; inset:0; pointer-events:none;
  background:linear-gradient(180deg,rgba(3,10,18,.10),transparent 38%,rgba(3,10,18,.20));}
.office-zone-label{position:absolute; z-index:2; transform:translateX(-50%); padding:5px 10px;
  border:1px solid rgba(255,255,255,.34); border-radius:999px; color:#fff;
  background:rgba(4,15,25,.70); backdrop-filter:blur(8px); font-size:11px; font-weight:700;
  box-shadow:0 4px 15px rgba(0,0,0,.20); white-space:nowrap;}
.agent-hotspot{--st:var(--unknown); position:absolute; z-index:3; transform:translate(-50%,-50%);
  display:flex; align-items:center; gap:6px; padding:5px 8px 5px 6px; max-width:155px;
  color:#fff; text-decoration:none; border:1px solid color-mix(in srgb,var(--st) 80%,white);
  border-radius:999px; background:rgba(4,13,22,.76); backdrop-filter:blur(8px);
  box-shadow:0 5px 18px rgba(0,0,0,.34); transition:transform .18s ease,background .18s ease;}
.agent-hotspot:hover,.agent-hotspot:focus-visible{transform:translate(-50%,-55%) scale(1.05);
  background:rgba(8,26,40,.94); outline:2px solid rgba(255,255,255,.8); outline-offset:2px;}
.agent-photo-dot{width:24px; height:24px; flex:0 0 24px; border-radius:50%; border:2px solid #fff;
  background:radial-gradient(circle at 35% 30%,#fff 0 12%,var(--st) 15% 62%,#10202d 65%);
  box-shadow:0 0 0 3px color-mix(in srgb,var(--st) 32%,transparent);}
.agent-hotspot.office-agent--working .agent-photo-dot{animation:office-pulse 1.1s ease-in-out infinite;}
.agent-hotspot-text{display:grid; min-width:0; line-height:1.18;}
.agent-hotspot-text b{overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:10.5px;}
.agent-hotspot-text span{overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
  font-size:8.5px; color:rgba(255,255,255,.72);}
.office-avatar{--avatar-shirt:var(--skin,var(--accent)); position:absolute; z-index:3;
  width:52px; height:72px; left:var(--from-x); top:var(--from-y);
  transform:translate(-50%,-100%); pointer-events:none;
  filter:drop-shadow(0 7px 5px rgba(0,0,0,.38));
  transition:left .9s ease,top .9s ease,transform .9s ease;}
.office-avatar svg{display:block; width:100%; height:100%; overflow:visible;}
.office-avatar--walking{animation:office-avatar-patrol var(--speed) ease-in-out var(--delay) infinite;}
.office-avatar--walking .office-avatar-person{animation:none;}
.office-avatar--still{left:var(--from-x); top:var(--from-y); filter:grayscale(.65) opacity(.78);}
.office-avatar--working{left:var(--desk-x); top:var(--desk-y); transform:translate(-50%,-100%);}
.office-avatar--working .office-avatar-person{transform:translateY(5px) scale(.92); transform-origin:32px 58px;}
.office-avatar--working .office-avatar-leg{opacity:0;}
.office-avatar--working .office-avatar-workstation{display:block;}
.office-avatar-workstation{display:none;}
.office-avatar-shadow{fill:rgba(0,0,0,.28);}
.office-avatar-skin{fill:var(--avatar-skin); stroke:#162535; stroke-width:1.5;}
.office-avatar-hair{fill:var(--avatar-hair); stroke:#162535; stroke-width:1.5;}
.office-avatar-shirt{fill:var(--avatar-shirt); stroke:#162535; stroke-width:1.5;}
.office-avatar-limb{fill:none; stroke:#162535; stroke-width:5; stroke-linecap:round;}
.office-avatar-eye{fill:#10202d;}
.office-avatar-chair{fill:#1d3444; stroke:#8fb6ca; stroke-width:1.3;}
.office-avatar-desk{fill:#9f6942; stroke:#3d2417; stroke-width:1.5;}
.office-avatar-screen{fill:#12384b; stroke:#77d7ef; stroke-width:1.5;}
.office-avatar-screen-stand{fill:#8fb6ca;}
.office-avatar-pants{fill:#34495A;}
.office-avatar-shoe{fill:#172633;}
.office-avatar-mouth{fill:#9B5C55;}
.avatar-style-1{--avatar-skin:#E9B78F;--avatar-hair:#512E25;}
.avatar-style-2{--avatar-skin:#F4D1B6;--avatar-hair:#C47B35;}
.avatar-style-3{--avatar-skin:#B97855;--avatar-hair:#1C2934;}
@keyframes office-avatar-patrol{
  0%,12%,100%{left:var(--from-x);top:var(--from-y)}
  48%,62%{left:var(--to-x);top:var(--to-y)}
}
.virtual-office-legend{position:absolute; z-index:3; left:18px; bottom:17px; display:flex;
  flex-wrap:wrap; gap:6px; padding:7px; border-radius:9px; background:rgba(3,12,20,.72);
  backdrop-filter:blur(7px); color:#fff; font-size:9px;}
.virtual-office-legend span{display:flex; align-items:center; gap:4px;}
.virtual-office-legend i{width:7px;height:7px;border-radius:50%;background:var(--legend);}
@keyframes office-pulse{50%{box-shadow:0 0 0 9px color-mix(in srgb,var(--st) 8%,transparent)}}
@media(max-width:860px){.virtual-office-shell{overflow-x:auto}.virtual-office-stage{position:relative;min-width:980px}.agent-hotspot{padding:4px 7px}.virtual-office-legend{display:none}}

.office{max-width:100%; overflow:hidden;}
.office-building{display:grid; grid-template-columns:repeat(2,minmax(0,1fr));
  gap:2px; padding:2px; background:var(--line); border:2px solid var(--ink);
  border-radius:3px;}
.office-room{min-width:0; padding:14px; background:var(--surface);}
.office-room--etc{grid-column:1/-1;}
.office-room--outsource{margin-top:14px; border:2px dashed var(--unknown);
  border-radius:3px; background:var(--surface);}
.office-room-head{display:flex; flex-wrap:wrap; justify-content:space-between;
  align-items:baseline; gap:4px 12px; margin-bottom:12px;}
.office-room-head h3{font-size:13px; letter-spacing:.04em; color:var(--ink);}
.office-room-count{font-size:11.5px; color:var(--muted);}
.office-seats{display:flex; flex-wrap:wrap; gap:10px; align-items:flex-end;}
.office-empty{min-height:112px; display:grid; place-items:center; text-align:center;
  border:1px dashed var(--line); color:var(--muted); font-size:12px;}
.office-agent{flex:1 1 118px; min-width:0; max-width:160px; padding:8px;
  border:2px solid var(--st); border-radius:3px;
  background:var(--surface-2); color:var(--ink); text-decoration:none;
  text-align:center;}
.office-agent:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
.office-agent--ready{--st:var(--ok);}
.office-agent--gated{--st:var(--gate);}
.office-agent--blocked{--st:var(--blocked);}
.office-agent--unknown{--st:var(--unknown); border-style:dashed;}
/* A live job outranks the file evidence: solid accent border, so a Codex that
   is gated on paper but demonstrably running does not read as idle. */
.office-agent--working{--st:var(--accent); border-style:solid;}
.office-character{display:block; width:100%; max-width:112px; height:auto; margin:0 auto;
  overflow:visible;}
/* --st is the state colour the seat already sets; --skin is the department's,
   so a room reads as one team before any label is read. */
.office-desk{fill:var(--sunk); stroke:var(--line); stroke-width:1.5;}
.office-desk-leg{fill:none; stroke:var(--line); stroke-width:3; stroke-linecap:round;}
.office-person{fill:var(--skin,var(--ink-2)); stroke:var(--ink); stroke-width:1.5;}
.office-face{fill:var(--surface); stroke:var(--ink); stroke-width:1.5;}
.office-hair{fill:var(--skin,var(--ink-2)); stroke:var(--ink); stroke-width:1.5;
  stroke-linejoin:round;}
.office-eye{fill:var(--ink);}
.office-eye-line{fill:none; stroke:var(--ink); stroke-width:2.4; stroke-linecap:round;}
.office-mouth{fill:none; stroke:var(--ink); stroke-width:2; stroke-linecap:round;}
.office-cheek{fill:var(--skin,var(--ink-2)); opacity:.32;}
.office-limb{fill:none; stroke:var(--skin,var(--ink-2)); stroke-width:6;
  stroke-linecap:round;}
.office-fist{fill:var(--face-fill,var(--surface)); stroke:var(--ink); stroke-width:1.5;}
.office-agent--dev{--skin:var(--accent);}
.office-agent--design{--skin:var(--gate);}
.office-agent--lab{--skin:var(--ok);}
.office-agent--modeling{--skin:var(--blocked);}
.office-agent--outsource{--skin:var(--unknown);}
.office-agent--etc{--skin:var(--ink-2);}
.office-screen{fill:var(--surface); stroke:var(--ink-2); stroke-width:1.5;}
.office-screen--on{fill:var(--ok-soft); stroke:var(--ok);}
.office-screen--wait{fill:var(--gate-soft); stroke:var(--gate);}
.office-stand{fill:none; stroke:var(--ink-2); stroke-width:2.5; stroke-linecap:round;}
.office-code{fill:none; stroke:var(--ok); stroke-width:2; stroke-linecap:round;
  opacity:.55;}
.office-cursor{fill:var(--ok); animation:office-cursor 1s steps(1,end) infinite;}

/* Working. The amplitudes are deliberately large: at 112px wide a 2px arm
   swing is invisible, and an office that never appears to move is the same
   picture as an office that is idle. */
.office-agent--ready .office-body{transform-origin:42px 48px;
  animation:office-breathe 2.6s ease-in-out infinite;}
.office-agent--ready .office-eyes{transform-origin:42px 31px;
  animation:office-blink 4.4s ease-in-out infinite;}
.office-agent--ready .office-hand--l{transform-origin:17px 61px;
  animation:office-type .6s ease-in-out infinite alternate;}
.office-agent--ready .office-hand--r{transform-origin:67px 61px;
  animation:office-type .6s ease-in-out .3s infinite alternate;}
/* A live job is the one thing on this page that is certainly happening right
   now, so its character types visibly faster than the merely-available ones. */
.office-agent--working .office-hand--l,
.office-agent--working .office-hand--r{animation-duration:.3s;}
.office-agent--working .office-body{animation-duration:1.3s;}

.office-body--waiting{transform-origin:42px 48px;
  animation:office-waiting 3s ease-in-out infinite;}
.office-alert{transform-origin:66px 16px; animation:office-alert 1.8s ease-in-out infinite;}
.office-alert-bubble{fill:var(--gate-soft); stroke:var(--gate); stroke-width:1.5;}
.office-alert-mark{fill:var(--gate);}

/* Blocked: no motion at all, and the colour goes out of the character too -
   a greyed-out desk should not still be wearing the team's colour. */
.office-agent--blocked{--skin:var(--unknown);}
.office-agent--blocked .office-face,.office-agent--blocked .office-fist{
  fill:var(--surface-2);}
.office-agent--blocked .office-eye-line,.office-agent--blocked .office-mouth{
  stroke:var(--unknown);}
.office-body--slumped{transform:translate(3px,5px) rotate(4deg);
  transform-origin:42px 48px;}

.office-agent--unknown .office-silhouette{fill:none; stroke:var(--unknown);
  stroke-width:2.5; stroke-dasharray:4 4; opacity:.55;
  animation:office-unknown 4s ease-in-out infinite;}
.office-agent-name{display:block; overflow-wrap:anywhere; font-size:11.5px;
  font-weight:600; line-height:1.35;}
.office-agent-state{display:block; margin-top:3px; color:var(--st);
  font-size:11px; line-height:1.3;}
@keyframes office-breathe{50%{transform:translateY(-2px) scale(1.015);}}
@keyframes office-type{to{transform:translateY(-5px) rotate(-6deg);}}
@keyframes office-blink{0%,92%,100%{transform:scaleY(1);}96%{transform:scaleY(.1);}}
@keyframes office-cursor{50%{opacity:0;}}
@keyframes office-alert{50%{transform:scale(1.14) translateY(-2px);}}
@keyframes office-waiting{50%{transform:translateY(-3px);}}
@keyframes office-unknown{50%{opacity:.25;}}

/* ---- the fold ----
   Four sections carry the flow: office, studio, order box, progress. The
   other eight are evidence you go looking for, not things you act on, and
   left open they pushed the flow off the top of the screen. Collapsed by
   default; a <details> keeps them one click away and findable by Ctrl+F,
   which a tab strip would not. */
.more{margin-top:26px; border-top:1px solid var(--line); padding-top:6px;}
.more > summary{cursor:pointer; list-style:none; padding:12px 4px;
  font-size:12.5px; letter-spacing:.04em; color:var(--muted); font-weight:600;}
.more > summary::-webkit-details-marker{display:none;}
.more > summary::before{content:'B8'; display:inline-block; margin-right:8px;
  transition:transform .15s ease;}
.more[open] > summary::before{transform:rotate(90deg);}
.more > summary:hover{color:var(--ink);}
.more > summary:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}

/* ---- queue ----
   The board section shows everything including finished work. This shows only
   what is queued or running, in the order it will be taken, because "what is
   next" is a different question from "what exists". */
.queue{display:grid; grid-template-columns:repeat(auto-fit,minmax(340px,1fr)); gap:18px;}
.q-lane{background:var(--surface); border:1px solid var(--line); border-radius:3px;
        padding:18px 20px 20px;}
.q-lane > h3{font-size:13px; letter-spacing:0.1em; text-transform:uppercase;
             color:var(--muted); margin-bottom:6px;
             display:flex; justify-content:space-between; align-items:baseline; gap:10px;}
.q-tally{font-size:11.5px; color:var(--muted); letter-spacing:0; font-weight:400;}
.q-item{display:grid; grid-template-columns:auto minmax(0,1fr); gap:0 12px;
        padding:12px 0; border-top:1px solid var(--line);}
.q-item:first-of-type{border-top:0;}
.q-seat{width:26px; height:26px; border-radius:50%; display:grid; place-items:center;
        font-size:12px; font-weight:600; background:var(--sunk); color:var(--muted);}
.q-item--running .q-seat{background:var(--accent); color:#fff;}
.q-item--waiting-deps .q-seat{background:var(--blocked-soft); color:var(--blocked);}
.q-head{display:flex; gap:10px; align-items:baseline; justify-content:space-between;}
.q-title{font-weight:500; font-size:14.5px;}
.q-id{grid-column:2; font-size:11.5px; color:var(--muted); letter-spacing:0.05em;}
.q-deps{grid-column:2; margin-top:5px; font-size:11.5px; color:var(--blocked);}
.q-deps code{font-size:11px;}
.q-act{grid-column:2; margin-top:9px;}
.q-act .btn{padding:6px 11px; font-size:12px;}
.q-empty{font-size:13px; color:var(--muted); padding:6px 0 2px;}

/* ---- live progress ----
   Every string in here comes from progress.py, which anchors each phrase to a
   line the orchestrator actually printed. There is deliberately no bar and no
   percentage: this pipeline has no denominator, so one would be invented. */
.live{grid-column:2; margin-top:10px; padding:12px 14px; border-radius:3px;
      background:var(--accent-soft); border:1px solid var(--accent);}
.live--panel{grid-column:auto; margin:0 0 12px;}
.live-top{display:flex; flex-wrap:wrap; gap:8px 12px; align-items:baseline;
          justify-content:space-between;}
.live-phase{font-weight:600; font-size:14px; color:var(--accent); min-width:0;}
.live-elapsed{font-size:12px; color:var(--ink-2); font-variant-numeric:tabular-nums;}
.live-what{font-size:11.5px; color:var(--ink-2); margin-top:3px;}
.live-evidence{margin-top:7px; font-size:11px; line-height:1.6; color:var(--muted);
               word-break:break-word;}
.live-note{margin-top:7px; font-size:11.5px; color:var(--gate);}
.live-dots::after{content:''; animation:live-dots 1.4s steps(4,end) infinite;}
@keyframes live-dots{0%{content:'';}25%{content:'.';}50%{content:'..';}75%{content:'...';}}
.live--done{background:var(--ok-soft); border-color:var(--ok);}
.live--done .live-phase{color:var(--ok);}
.live--failed{background:var(--blocked-soft); border-color:var(--blocked);}
.live--failed .live-phase{color:var(--blocked);}

.kv-note{margin-top:10px; font-size:12px; line-height:1.6; color:var(--muted);}

/* ---- board ---- */
.board{display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:18px;}
.lane{background:var(--surface); border:1px solid var(--line); border-radius:3px;
      padding:18px 20px 20px;}
.lane > h3{font-size:13px; letter-spacing:0.1em; text-transform:uppercase;
           color:var(--muted); margin-bottom:14px;
           display:flex; justify-content:space-between; align-items:baseline;}
.lane .count{font-size:12px; color:var(--muted); letter-spacing:0;}
.task{padding:11px 0; border-top:1px solid var(--line);}
.task:first-of-type{border-top:0; padding-top:0;}
.task .t{display:flex; gap:10px; align-items:baseline; justify-content:space-between;}
.task .id{font-size:11.5px; color:var(--muted); letter-spacing:0.05em;}
.task .title{font-weight:500; font-size:14.5px;}
/* Wraps rather than scrolls: a clipped path with a hidden scrollbar reads as
   a broken layout, and only the filename identifies the file anyway. */
.task .files{margin-top:5px; font-size:11.5px; color:var(--muted); line-height:1.7;}
.task-action{display:flex; flex-wrap:wrap; align-items:center; gap:8px; margin-top:9px;}
.task-action .btn{padding:6px 11px; font-size:12px;}
.task-blockers{font-size:11.5px; color:var(--blocked);}
.task-blockers code{font-size:11px;}
.tag{font-size:11px; font-weight:600; letter-spacing:0.05em; padding:2px 8px;
     border-radius:2px; background:var(--st-soft); color:var(--st); white-space:nowrap;}
.s-todo{--st:var(--unknown); --st-soft:var(--unknown-soft);}
.s-in_progress{--st:var(--accent); --st-soft:var(--accent-soft);}
.s-review{--st:var(--gate); --st-soft:var(--gate-soft);}
.s-done{--st:var(--ok); --st-soft:var(--ok-soft);}
.s-blocked-tag{--st:var(--blocked); --st-soft:var(--blocked-soft);}

/* ---- pipeline + facts ---- */
.grid2{display:grid; grid-template-columns:repeat(auto-fit,minmax(300px,1fr)); gap:18px;}
.panel{background:var(--surface); border:1px solid var(--line); border-radius:3px;
       padding:18px 20px 20px;}
.panel h3{font-size:13px; letter-spacing:0.1em; text-transform:uppercase;
          color:var(--muted); margin-bottom:14px;}
.kv{display:flex; justify-content:space-between; gap:16px; padding:8px 0;
    border-top:1px solid var(--line); font-size:14px;}
.kv:first-of-type{border-top:0;}
.kv .k{color:var(--muted);}
.kv .v{text-align:right; word-break:break-all;}
.big{font-family:'Archivo',sans-serif; font-size:15px; font-weight:600;}

/* ---- games ---- */
.games{display:flex; gap:5px; flex-wrap:wrap;}
.g{flex:1 1 84px; min-width:84px; border:1px solid var(--line); border-radius:2px;
   background:var(--surface); padding:11px 12px 12px; border-top:3px solid var(--st);}
.g .n{font-family:'Archivo',sans-serif; font-weight:700; font-size:14px;}
.g .s{font-size:11px; color:var(--st); margin-top:2px; font-weight:600;}
.g-done{--st:var(--ok);}
.g-active{--st:var(--accent);}
.g-todo{--st:var(--line);}
.g-todo .s{color:var(--muted); font-weight:400;}

/* ---- log ---- */
.log{background:var(--surface); border:1px solid var(--line); border-radius:3px;
     overflow:hidden;}
.log div{display:grid; grid-template-columns:auto auto minmax(0,1fr); gap:16px;
         padding:9px 20px; border-top:1px solid var(--line); font-size:13.5px;
         align-items:baseline;}
.log div:first-child{border-top:0;}
.log .sha{color:var(--accent);}
.log .when{color:var(--muted); font-size:12.5px;}
.log .what{overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}

/* ---- gallery ---- */
.gal{display:grid; grid-template-columns:repeat(auto-fill,minmax(128px,1fr)); gap:10px;}
.shot{background:var(--surface); border:1px solid var(--line); border-radius:3px;
      overflow:hidden; display:flex; flex-direction:column;}
.shot .frame{aspect-ratio:1; display:grid; place-items:center; padding:9px;
  /* Checkerboard, so a sprite's transparent edge is visible instead of
     blending into whichever theme the viewer is in. */
  background-color:var(--sunk);
  background-image:linear-gradient(45deg,var(--line) 25%,transparent 25%,transparent 75%,var(--line) 75%),
                   linear-gradient(45deg,var(--line) 25%,transparent 25%,transparent 75%,var(--line) 75%);
  background-size:14px 14px; background-position:0 0,7px 7px;}
.shot img{max-width:100%; max-height:100%; display:block;}
.shot img.px{image-rendering:pixelated;}
.shot .cap{padding:7px 9px 9px; font-size:11px; line-height:1.55;
           border-top:1px solid var(--line);}
/* Both lines clamped to one line each, so every card is exactly the same
   height and the grid rows line up instead of stair-stepping. */
.shot .cap b, .shot .cap span{display:block; overflow:hidden;
  text-overflow:ellipsis; white-space:nowrap;}
.shot .cap b{font-weight:500;}
.shot .cap span{color:var(--muted);}
.shot .miss{grid-column:1/-1; color:var(--muted); font-size:11px; text-align:center;
            padding:0 6px;}
.gal-empty{background:var(--surface); border:1px dashed var(--line); border-radius:3px;
           padding:20px; color:var(--muted); font-size:13.5px;}
.plan{display:flex; flex-direction:column;}
.plan .r{display:grid; grid-template-columns:auto minmax(0,1fr); gap:5px 12px;
         padding:11px 0; border-top:1px solid var(--line);}
.plan .r:first-child{border-top:0; padding-top:0;}
.plan .t{font-size:11px; font-weight:600; letter-spacing:0.05em; padding:2px 9px;
         border-radius:2px; background:var(--st-soft); color:var(--st);
         white-space:nowrap; align-self:start;}
.plan .n{font-family:'JetBrains Mono',monospace; font-size:12.5px; word-break:break-all;
         align-self:center;}
.plan .w{grid-column:2; color:var(--ink-2); font-size:12.5px; line-height:1.6;}
.gal-wrap + .gal-wrap{margin-top:22px;}
.gal-wrap > .h{display:flex; flex-wrap:wrap; align-items:baseline; gap:6px 14px;
               margin-bottom:10px;}
.gal-wrap > .h b{font-family:'Archivo','Noto Sans KR',sans-serif; font-size:14.5px;}
.gal-wrap > .h span{font-size:12px; color:var(--muted);}

/* ---- AI game studio (served locally only) ---- */
.studio{position:relative; overflow:hidden; display:grid; grid-template-columns:minmax(210px,.48fr) minmax(0,1.52fr);
  gap:22px; padding:24px; border:1px solid #d9ccba; border-radius:16px;
  background:linear-gradient(145deg,#fffaf0 0%,#fff 52%,#eef8ff 100%);
  box-shadow:0 16px 45px rgba(69,44,22,.09);}
.studio::after{content:""; position:absolute; width:240px; height:240px; border-radius:50%;
  right:-90px; top:-120px; background:rgba(70,185,255,.10); pointer-events:none;}
.studio-character{display:flex; flex-direction:column; align-items:center; justify-content:center;
  min-height:300px; padding:18px; border-radius:13px; color:#fff;
  background:linear-gradient(165deg,#1677d2,#25a6dd 58%,#56c982); text-align:center;}
.studio-character img{width:min(168px,80%); height:205px; object-fit:contain;
  filter:drop-shadow(0 13px 10px rgba(0,0,0,.22));}
.studio-character .lock{display:inline-flex; align-items:center; gap:6px; padding:6px 10px;
  margin-top:7px; border-radius:999px; background:rgba(0,0,0,.18); font-size:12px; font-weight:700;}
.studio-character h3{margin:10px 0 2px; font-size:20px;}
.studio-character p{margin:0; font-size:12px; opacity:.84; line-height:1.6;}
.studio-main{min-width:0; position:relative; z-index:1;}
.studio-steps{display:flex; flex-wrap:wrap; gap:7px; margin-bottom:16px;}
.studio-step{padding:6px 10px; border-radius:999px; background:#eef2f5; color:var(--ink-2);
  font-size:11.5px; font-weight:700;}
.studio-step b{color:var(--accent); margin-right:3px;}
.creator-form{display:grid; gap:12px;}
.creator-form label{display:grid; gap:6px; font-size:12px; font-weight:700; color:var(--ink-2);}
.creator-form input,.creator-form textarea,.creator-form select{box-sizing:border-box; width:100%;
  padding:11px 12px; border:1px solid var(--line); border-radius:7px; color:var(--ink);
  background:#fff; font:13.5px 'Noto Sans KR',sans-serif;}
.creator-form textarea{min-height:88px; resize:vertical; line-height:1.65;}
.creator-form input:focus-visible,.creator-form textarea:focus-visible,.creator-form select:focus-visible{
  outline:2px solid var(--accent); outline-offset:1px; border-color:transparent;}
.creator-options{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:9px;}
.creator-actions{display:flex; flex-wrap:wrap; gap:9px; align-items:center;}
.creator-actions .btn.primary{padding:11px 20px; border-radius:7px; font-size:14px;
  background:linear-gradient(135deg,#ff7656,#ff4f72); border:0; box-shadow:0 8px 18px rgba(255,79,114,.22);}
.creator-actions .btn.preview{border-radius:7px; background:#fff; color:var(--ink); border-color:var(--line);}
.creator-ai-note{font-size:11.5px; color:var(--muted); line-height:1.6;}
.creator-result{display:none; margin-top:14px; padding:15px; border-radius:10px;
  border:1px solid #cbdce9; background:#f7fbff;}
.creator-result.on{display:block;}
.creator-result h3{margin:0 0 5px; font-size:17px;}
.creator-result p{margin:4px 0; color:var(--ink-2); font-size:12.5px; line-height:1.65;}
.creator-tags{display:flex; flex-wrap:wrap; gap:5px; margin-top:9px;}
.creator-tags span{font-size:11px; padding:4px 7px; border-radius:999px; background:#e7f2fa; color:#1c618c;}
.creator-spec{margin-top:9px; font-size:11px; color:var(--muted);}
.creator-download{display:inline-flex; margin-top:10px; padding:8px 11px; border-radius:6px;
  color:#fff; background:var(--ok); font-size:12px; font-weight:700; text-decoration:none;}
@media(max-width:820px){.studio{grid-template-columns:1fr}.studio-character{min-height:220px}.studio-character img{height:145px}}
@media(max-width:680px){.creator-options{grid-template-columns:1fr 1fr}}

/* ---- control (served locally only) ---- */
.ctl{background:var(--surface); border:1px solid var(--line); border-radius:3px;
     border-left:4px solid var(--accent); padding:18px 20px 20px;}
.ctl .acts{display:flex; flex-wrap:wrap; gap:10px; align-items:center;}
.control-grid{display:grid; gap:10px; margin-bottom:14px;}
.control-row{display:grid; grid-template-columns:minmax(120px,.35fr) minmax(0,1.65fr);
  gap:14px; align-items:center; padding:11px 12px; background:var(--surface-2);
  border:1px solid var(--line); border-radius:3px;}
.control-name{font-weight:700; color:var(--ink);}
.control-body{display:flex; flex-wrap:wrap; gap:8px; align-items:center; min-width:0;}
.control-note{font-size:12px; color:var(--muted);}
.control-reasons{flex-basis:100%; display:grid; gap:3px; font-size:11.5px;
  color:var(--blocked);}
.btn{font-family:'Archivo','Noto Sans KR',sans-serif; font-size:13.5px; font-weight:600;
     padding:9px 16px; border-radius:2px; cursor:pointer; color:var(--surface);
     background:var(--accent); border:1px solid var(--accent);}
.btn:hover{filter:brightness(1.12);}
.btn.ghost{background:transparent; color:var(--ink); border-color:var(--line);}
.btn:disabled{opacity:.45; cursor:not-allowed; filter:none;}
.btn:focus-visible, select:focus-visible{outline:2px solid var(--accent); outline-offset:2px;}
.combo{display:flex; gap:0; align-items:stretch; min-width:0; max-width:100%;}
.control-body > select,.combo select{font-family:'JetBrains Mono',monospace; font-size:12.5px; padding:8px 10px;
  background:var(--surface-2); color:var(--ink); border:1px solid var(--line);
  min-width:0; max-width:230px;}
.control-body > select{width:min(100%,230px); border-radius:2px;}
.combo select{border-right:0; border-radius:2px 0 0 2px;}
.combo .btn{border-radius:0 2px 2px 0;}
@media(max-width:620px){.control-row{grid-template-columns:1fr; gap:7px;}}

/* ---- order box ---- */
.order{display:grid; gap:11px;}
.order-row{display:flex; flex-wrap:wrap; gap:10px; align-items:center;}
.order-row .btn{margin-left:auto;}
.order select{font-family:'Archivo','Noto Sans KR',sans-serif; font-size:13px;
  padding:9px 11px; background:var(--surface-2); color:var(--ink);
  border:1px solid var(--line); border-radius:2px; max-width:100%;}
.order textarea{font-family:'Noto Sans KR',sans-serif; font-size:14px; line-height:1.7;
  padding:12px 14px; background:var(--surface-2); color:var(--ink);
  border:1px solid var(--line); border-radius:2px; resize:vertical; min-height:96px;
  width:100%; box-sizing:border-box;}
.order textarea:focus-visible{outline:2px solid var(--accent); outline-offset:1px;}
.order-scope{font-size:11.5px; color:var(--muted); min-width:0; word-break:break-word;}
.order-check{display:flex; gap:7px; align-items:center; font-size:12.5px; color:var(--ink-2);}
.order-how{font-size:12px; line-height:1.75; color:var(--muted);
  padding:10px 12px; background:var(--sunk); border-radius:2px;}
.order-how b{color:var(--ink);}
.order-closed{font-size:11.5px; color:var(--gate);}
.term{margin-top:14px; background:var(--sunk); border:1px solid var(--line); border-radius:2px;
      padding:12px 14px; font-family:'JetBrains Mono',monospace; font-size:12px;
      line-height:1.65; white-space:pre-wrap; word-break:break-word;
      max-height:340px; overflow-y:auto; color:var(--ink-2);}
.term:empty{display:none;}
.running{display:inline-flex; align-items:center; gap:8px; font-size:12.5px;
         color:var(--gate); font-weight:600;}
.running::before{content:""; width:7px; height:7px; border-radius:50%;
                 background:currentColor; animation:blip 1s ease-in-out infinite;}
@keyframes blip{50%{opacity:.25;}}
@media (prefers-reduced-motion: reduce){
  .running::before,.office-agent *,.live-dots::after{animation:none!important;}
  /* The dots are decoration; the phase text carries the meaning, so stopping
     them loses nothing. Left as an ellipsis so the line does not reflow. */
  .live-dots::after{content:'...';}
}

.warn{margin-top:14px; padding:14px 18px; border-radius:3px;
      background:var(--blocked-soft); border-left:4px solid var(--blocked);
      color:var(--ink); font-size:13.5px;}
.warn code{font-size:12.5px;}

footer{margin-top:52px; padding-top:18px; border-top:1px solid var(--line);
       color:var(--muted); font-size:12.5px; line-height:1.8;}
footer code{font-size:12px; background:var(--sunk); padding:2px 6px; border-radius:2px;}

@media (max-width:560px){
  h1{font-size:24px;}
  .wrap{padding:24px 16px 56px;}
  .stamp{text-align:left;}
  .log div{grid-template-columns:auto minmax(0,1fr); }
  .log .when{grid-column:1/-1;}
  .office-building{grid-template-columns:minmax(0,1fr);}
  .office-room--etc{grid-column:auto;}
  .office-agent{max-width:none; flex-basis:104px;}
  /* auto-fit will not shrink a track below its minmax floor, so on a 360px
     phone the 340px lane plus padding pushes the page sideways. One column. */
  .queue{grid-template-columns:minmax(0,1fr);}
  .q-lane{padding:16px 14px 18px;}
}

/* ---- pixel mini-homepage skin ---- */
:root,:root:not([data-theme="light"]),:root[data-theme="dark"]{
  --ground:#BFDDE8; --surface:#FFFDF4; --surface-2:#F2F7F2; --sunk:#E2EEF2;
  --ink:#243544; --ink-2:#40576A; --muted:#6D8190; --line:#7896A8;
  --accent:#27789A; --accent-soft:#D8EEF5;
  --ok:#238A68; --ok-soft:#DDF3E7;
  --gate:#B66B21; --gate-soft:#FFF0CE;
  --blocked:#B84656; --blocked-soft:#F9DDE2;
  --unknown:#687786; --unknown-soft:#E6EBEE;
}
html{scroll-behavior:smooth;}
body{
  background-color:#B8D8E4;
  background-image:linear-gradient(45deg,rgba(255,255,255,.35) 25%,transparent 25%),
    linear-gradient(-45deg,rgba(255,255,255,.35) 25%,transparent 25%),
    linear-gradient(45deg,transparent 75%,rgba(83,139,163,.14) 75%),
    linear-gradient(-45deg,transparent 75%,rgba(83,139,163,.14) 75%);
  background-size:16px 16px; background-position:0 0,0 8px,8px -8px,-8px 0;
  color:var(--ink); font-family:'Courier New','Malgun Gothic',monospace;
}
.wrap{position:relative; max-width:1260px; margin:34px auto 54px; padding:18px 22px 38px;
  background:#EAF5F7; border:4px solid #47697C; outline:4px solid #fff;
  box-shadow:10px 10px 0 rgba(54,91,108,.35);}
.mast{display:grid; grid-template-columns:142px minmax(0,1fr) auto; align-items:center;
  gap:18px; padding:12px; color:var(--ink); background:#FFFDF4;
  border:2px solid #7896A8; border-bottom:3px double #7896A8;}
.mast .sub,.stamp{color:var(--muted);}
.mast h1{font-family:'Courier New','Malgun Gothic',monospace; color:#27789A;
  font-size:27px; letter-spacing:-1px; text-shadow:2px 2px 0 #D7EDF5;}
.mini-owner{display:grid; gap:5px; text-align:center; padding:7px; background:#F7F3E6;
  border:2px solid #7896A8; box-shadow:3px 3px 0 #BDD1DA; font-size:10px;}
.mini-today{color:#E16C65; font-weight:700; font-size:9px; white-space:nowrap;}
.mini-owner-avatar{height:68px; display:grid; place-items:center; background:#DDECF0;
  border:2px inset #9CB7C4; overflow:hidden;}
.mini-owner-avatar span{font-size:48px; line-height:1; filter:saturate(.8);}
.mini-owner b{font-size:12px; color:#27789A;}
.mini-title{min-width:0;}
.mini-tabs{position:absolute; z-index:20; top:154px; right:-88px; display:grid; gap:5px;}
.mini-tabs a{width:84px; padding:9px 8px; color:#315368; background:#A9D8E7;
  border:2px solid #47697C; border-left:0; box-shadow:3px 3px 0 rgba(54,91,108,.28);
  text-decoration:none; font-weight:700; font-size:10px; letter-spacing:.04em;}
.mini-tabs a:first-child{background:#FFF3A8; color:#8A5A17;}
.mini-tabs a:hover,.mini-tabs a:focus-visible{transform:translateX(3px); background:#FFFDF4;}
.verdict{margin-top:14px; padding:10px 14px; border:2px dashed #7896A8;
  border-left:7px solid #27789A; border-radius:0; box-shadow:3px 3px 0 #C4DCE4;}
section{scroll-margin-top:12px; margin-top:22px; padding:14px; background:#FFFDF4;
  border:2px solid #7896A8; box-shadow:4px 4px 0 #B4CED8;}
.head{margin-bottom:10px; padding-bottom:7px; border-bottom:2px dotted #9DB5C1;}
.head h2{display:inline-block; padding:4px 9px; color:#315368; background:#D7EDF5;
  border:2px solid #7896A8; font-family:'Courier New','Malgun Gothic',monospace;
  font-size:13px; letter-spacing:0; text-shadow:none;}
.head .note{color:#6D8190;}
.virtual-office-shell{border:4px solid #3F596A; border-radius:0; background:#9CC9DB;
  box-shadow:5px 5px 0 #9EB9C6; image-rendering:pixelated;}
.virtual-office-image{image-rendering:pixelated; filter:saturate(.88) contrast(1.04);}
.office-zone-label{padding:4px 8px; color:#3C321C; background:#FFF3A8;
  border:2px solid #6E5730; border-radius:0; backdrop-filter:none;
  box-shadow:3px 3px 0 rgba(64,48,25,.42); font-size:10px;}
.agent-hotspot{padding:4px 7px 4px 5px; max-width:148px; color:#fff;
  background:#28485B; border:2px solid #DCEBF0; border-radius:0; backdrop-filter:none;
  box-shadow:3px 3px 0 rgba(19,40,52,.65); transition:transform .1s linear;}
.agent-hotspot:hover,.agent-hotspot:focus-visible{background:#3A6075;
  transform:translate(-50%,-54%); outline:2px solid #FFF3A8;}
.agent-photo-dot{width:18px; height:18px; flex-basis:18px; border-radius:0;
  border:2px solid #fff; box-shadow:none;}
.office-avatar{width:48px; height:66px; image-rendering:pixelated;
  filter:drop-shadow(3px 3px 0 rgba(27,48,58,.45));}
.office-avatar svg{shape-rendering:crispEdges; image-rendering:pixelated;}
.virtual-office-vignette{display:none;}
.virtual-office-legend{left:8px; bottom:8px; padding:5px; color:#fff; background:#28485B;
  border:2px solid #DCEBF0; border-radius:0; backdrop-filter:none; font-size:8px;}
.minecraft-office{border-color:#423729; outline:4px solid #78A34B; background:#77BCE8;}
.minecraft-office .virtual-office-stage{background:#77BCE8;}
.company-zone{max-width:29%; overflow:hidden; text-overflow:ellipsis;}
.company-legend{max-width:94%;}
.company-legend i{background:var(--st);}
.company-role-grid{display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:2px;
  padding:6px; background:#554738; border-top:4px solid #423729;}
.company-role-card{min-width:0; display:grid; grid-template-columns:minmax(0,1fr) auto;
  gap:2px 8px; padding:8px; color:var(--ink); background:#FFFDF4;
  border:2px solid var(--st); box-shadow:inset -3px -3px 0 rgba(65,54,40,.12);}
.company-role-card b{overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px;}
.company-role-dept{grid-column:1/-1; color:#6D8190; font-size:9px;}
.company-role-state{color:var(--st); font-size:9px; font-weight:700; white-space:nowrap;}
.company-role-card small{grid-column:1/-1; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; color:#6D8190; font-size:8px;}
.agent-hotspot.company-state--working{animation:office-pulse 1.1s steps(2,end) infinite;}
.panel,.q-lane,.task,.studio,.ctl,.roster,.row,.shot,.g,.term,.warn,
.creator-result,.creator-input,.order textarea,.order select,.control-row{
  border-radius:0!important; box-shadow:none;}
.row,.q-lane,.panel,.task,.ctl,.studio{background:#FFFDF4;}
.btn{font-family:'Courier New','Malgun Gothic',monospace; border:2px solid #315368;
  border-radius:0; box-shadow:3px 3px 0 #8AA8B7; background:#72B6CF; color:#17394A;
  font-size:12px; text-transform:uppercase;}
.btn:hover{filter:none; background:#FFF3A8;}
.btn:active{transform:translate(2px,2px); box-shadow:1px 1px 0 #8AA8B7;}
input,select,textarea{font-family:'Courier New','Malgun Gothic',monospace!important;
  border:2px solid #7896A8!important; border-radius:0!important;}
.pill,.task-state,.plan .t{border-radius:0; border:1px solid currentColor;}
footer{margin-top:28px; padding:14px; background:#D8EAF0; border:2px dotted #7896A8;}
@media(max-width:1380px){.mini-tabs{position:static; display:flex; flex-wrap:wrap;
  gap:5px; margin:10px 0 0}.mini-tabs a{width:auto; border-left:2px solid #47697C;}}
@media(max-width:760px){.wrap{margin:8px; padding:10px; outline:0; box-shadow:5px 5px 0 rgba(54,91,108,.3)}
  .mast{grid-template-columns:94px minmax(0,1fr)}.stamp{grid-column:1/-1;text-align:left}
  .mini-owner{grid-row:span 2}.mini-owner-avatar{height:54px}.mini-owner-avatar span{font-size:38px}
  .company-role-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""


def _agent_row(agent: Agent) -> str:
    evidence = (
        "".join(f"<code>{e(path)}</code>" for path in agent.evidence)
        if agent.evidence else '<span class="none">근거 없음 - 확인된 파일이 없습니다</span>'
    )
    version = (f'\n          <div class="ver mono">{e(agent.version)}</div>'
               if agent.version else "")
    return f"""      <div class="row s-{agent.state}" id="agent-{e(_agent_slug(agent.name))}">
        <div>
          <div class="who">{e(agent.name)}</div>
          <div class="role">{e(agent.role)}</div>
        </div>
        <div style="text-align:right">
          <span class="pill">{e(STATE_LABEL[agent.state])}</span>{version}
        </div>
        <div class="detail">{e(agent.detail)}</div>
        <div class="ev"><span class="k">근거</span>{evidence}</div>
      </div>"""


def _agent_slug(name: str) -> str:
    """A stable fragment shared by an office character and its roster row."""
    return re.sub(r"[^\w]+", "-", name.lower(), flags=re.UNICODE).strip("-") or "agent"


def _department_for_agent(name: str) -> str:
    return next((department for prefix, department in DEPARTMENT_BY_PREFIX.items()
                 if name.startswith(prefix)), "etc")


def _character_svg(department: str, state: str) -> str:
    """The desk characters, in one place so real art can replace them later.

    COMPOSITION. Front view: the character sits BEHIND the desk, so the desk
    band crosses its middle and only head, shoulders and hands show - which is
    what a desk looks like and also what lets the face be big. Draw order is
    character, then desk, then monitor, so the desk really does occlude.

    WHY THE PROPORTIONS ARE LIKE THIS. A big head on a small body is what
    reads as friendly at 112 pixels wide; a correctly proportioned figure at
    this size is a grey smudge. The face is the only part with detail, because
    it is the part a person looks at.

    THE MOVEMENT HAS TO BE BIG ENOUGH TO SEE. The first version moved arms by
    one or two pixels, which is invisible at this size - the office looked
    frozen even while everything was working. Hands now travel about a sixth
    of the head's width, which reads as typing across the room.

    Motion is never the ONLY signal, per the spec: colour, posture, the
    screen and the eyes each carry the state on their own, so the office is
    still readable with prefers-reduced-motion on.
    """
    # Drawn first, so the desk covers the character's waist.
    desk = ('<rect class="office-desk" x="3" y="60" width="106" height="8" rx="2.5"/>'
            '<path class="office-desk-leg" d="M13 68v22M99 68v22"/>')

    def face(eyes: str, mouth: str) -> str:
        return (f'<circle class="office-face" cx="42" cy="31" r="16.5"/>'
                f'<path class="office-hair" d="M27 25q3-16 15-16t15 16q-6-8-15-8t-15 8z"/>'
                f'<g class="office-eyes">{eyes}</g>{mouth}')

    # Named rather than inlined into the f-strings below: an f-string
    # expression may not contain a backslash before Python 3.12, and these
    # all carry escaped quotes.
    smile = '<path class="office-mouth" d="M38 38.5q4 4.5 8 0"/>'
    straight = '<path class="office-mouth" d="M38 39h8"/>'
    frown = '<path class="office-mouth" d="M38 40q4-3.5 8 0"/>'
    blink = ('<circle class="office-eye" cx="36" cy="31" r="2.4"/>'
             '<circle class="office-eye" cx="48" cy="31" r="2.4"/>')
    flat_eyes = '<path class="office-eye-line" d="M33 31h6M45 31h6"/>'
    cheeks = ('<circle class="office-cheek" cx="30" cy="36" r="3"/>'
              '<circle class="office-cheek" cx="54" cy="36" r="3"/>')
    # Shoulders only: everything below y=60 is behind the desk anyway.
    torso = '<path class="office-person" d="M22 64V56q0-12 20-12t20 12v8z"/>'

    if state == READY:
        return (
            '<svg class="office-character" viewBox="0 0 112 96" aria-hidden="true">'
            f'<g class="office-body">{torso}{face(blink, smile)}{cheeks}</g>'
            # Hands sit on the desk surface and alternate - the typing.
            '<g class="office-hand office-hand--l">'
            '<path class="office-limb" d="M25 55l-7 6"/>'
            '<circle class="office-fist" cx="17" cy="61" r="4"/></g>'
            '<g class="office-hand office-hand--r">'
            '<path class="office-limb" d="M59 55l7 6"/>'
            '<circle class="office-fist" cx="67" cy="61" r="4"/></g>'
            f'{desk}'
            '<g class="office-monitor"><rect class="office-screen office-screen--on"'
            ' x="72" y="30" width="33" height="24" rx="2.5"/>'
            '<rect class="office-cursor" x="77" y="36" width="2.5" height="9"/>'
            '<path class="office-code" d="M83 39h14M83 43h9M83 47h12"/>'
            '<path class="office-stand" d="M88.5 54v6M81 60h15"/></g></svg>')

    if state == GATED:
        # Standing, waiting to be let in: no hands on the desk, and the '!'
        # is what the eye lands on.
        return (
            '<svg class="office-character" viewBox="0 0 112 96" aria-hidden="true">'
            f'<g class="office-body office-body--waiting">{torso}'
            f'{face(blink, straight)}{cheeks}</g>'
            '<path class="office-limb" d="M25 55l-4 8M59 55l4 8"/>'
            f'{desk}'
            '<g class="office-monitor"><rect class="office-screen office-screen--wait"'
            ' x="72" y="30" width="33" height="24" rx="2.5"/>'
            '<path class="office-stand" d="M88.5 54v6M81 60h15"/></g>'
            '<g class="office-alert"><circle class="office-alert-bubble" cx="66" cy="14" r="11"/>'
            '<path class="office-alert-mark" d="M64 7h4l-1 9h-2zm0 11h4v4h-4z"/></g></svg>')

    if state == BLOCKED:
        # Not a sad face for its own sake: the spec forbids drawing a blocked
        # agent smiling, because the picture would be saying the opposite of
        # the label under it. Flat eyes, flat mouth, dark screen, chair away.
        return (
            '<svg class="office-character" viewBox="0 0 112 96" aria-hidden="true">'
            '<g class="office-body office-body--slumped">'
            f'{torso}{face(flat_eyes, frown)}</g>'
            '<path class="office-limb" d="M25 56l-5 7M59 56l5 7"/>'
            f'{desk}'
            '<g class="office-monitor"><rect class="office-screen" x="72" y="30"'
            ' width="33" height="24" rx="2.5"/>'
            '<path class="office-stand" d="M88.5 54v6M81 60h15"/></g></svg>')

    # UNKNOWN: an outline where somebody might be. No face - inventing an
    # expression would be claiming to know a state the files do not report.
    return (
        '<svg class="office-character" viewBox="0 0 112 96" aria-hidden="true">'
        '<g class="office-silhouette"><circle cx="42" cy="31" r="16.5"/>'
        '<path d="M22 64V56q0-12 20-12t20 12v8M25 55l-5 7M59 55l5 7"/></g>'
        f'{desk}'
        '<g class="office-monitor"><rect class="office-screen" x="72" y="30"'
        ' width="33" height="24" rx="2.5" stroke-dasharray="4 4"/>'
        '<path class="office-stand" d="M88.5 54v6M81 60h15"/></g></svg>')


def _office_caption(agent: Agent, working: bool) -> str:
    """What the character says under its feet.

    Not simply STATE_LABEL: bare '대기 중' on a GATED agent reads as "idle
    right now", when what it means is "a person has to do something first".
    The roster keeps the four plain labels; this is the caption only.
    """
    if working:
        return "작업 중"
    if agent.state == GATED:
        return "대기 중 · 사람 확인 필요"
    return STATE_LABEL[agent.state]


def _office_html(agents: list[Agent], working_prefix: str = "") -> str:
    """Draw the departments. `working_prefix` is the agent a live job drives.

    A running job is stronger evidence than any committed file, so it wins:
    Codex is GATED on paper because a login cannot be read from disk, but a
    Codex run that is producing output has plainly passed that gate. Without
    this the panel says '실행 중' while the character below it says '대기 중',
    and a page that contradicts itself is wrong however defensible each half.
    """
    grouped = {department: [] for department in DEPARTMENT_LABEL}
    for agent in agents:
        grouped[_department_for_agent(agent.name)].append(agent)

    def is_working(agent: Agent) -> bool:
        return bool(working_prefix) and agent.name.startswith(working_prefix)

    def room(department: str) -> str:
        occupants = grouped[department]
        working = sum(agent.state == READY or is_working(agent)
                      for agent in occupants)
        room_classes = f"office-room office-room--{department}"
        if occupants:
            seats = []
            for agent in occupants:
                busy = is_working(agent)
                caption = _office_caption(agent, busy)
                accessible = f"{agent.name} - {caption}"
                # A working agent is drawn with the READY (typing) character
                # whatever its file-based state says, and keeps its own state
                # class so colour still reports the gate underneath.
                drawn = READY if busy else agent.state
                extra = " office-agent--working" if busy else ""
                seats.append(
                    f'<a class="office-agent office-agent--{department} '
                    f'office-agent--{agent.state}{extra}" '
                    f'href="#agent-{e(_agent_slug(agent.name))}" title="{e(accessible)}" '
                    f'aria-label="{e(accessible)}">{_character_svg(department, drawn)}'
                    f'<span class="office-agent-name">{e(agent.name)}</span>'
                    f'<span class="office-agent-state">{e(caption)}</span></a>')
            body = f'<div class="office-seats">{"".join(seats)}</div>'
        else:
            body = '<div class="office-empty">배정된 AI 없음</div>'
        return (f'<div class="{room_classes}"><div class="office-room-head">'
                f'<h3>{e(DEPARTMENT_LABEL[department])}</h3>'
                f'<span class="office-room-count">{len(occupants)}명 중 {working}명 작업 중</span>'
                f'</div>{body}</div>')

    internal = "".join(room(department) for department in
                       ("dev", "design", "lab", "modeling"))
    if grouped["etc"]:
        internal += room("etc")
    return (f'<div class="office"><div class="office-building">{internal}</div>'
            f'{room("outsource")}</div>')


def _office_avatar_svg() -> str:
    """A block-built 16-bit person that can walk or sit without assets."""
    return '''<svg class="office-avatar-pixel" viewBox="0 0 32 48"
      shape-rendering="crispEdges" aria-hidden="true">
      <rect class="office-avatar-shadow" x="7" y="44" width="18" height="3"></rect>
      <g class="office-avatar-workstation">
        <rect class="office-avatar-chair" x="8" y="27" width="16" height="16"></rect>
      </g>
      <g class="office-avatar-person">
        <rect class="office-avatar-leg office-avatar-pants" x="9" y="34" width="5" height="9"></rect>
        <rect class="office-avatar-leg office-avatar-pants" x="18" y="34" width="5" height="9"></rect>
        <rect class="office-avatar-leg office-avatar-shoe" x="7" y="42" width="7" height="3"></rect>
        <rect class="office-avatar-leg office-avatar-shoe" x="18" y="42" width="7" height="3"></rect>
        <rect class="office-avatar-shirt" x="8" y="23" width="16" height="13"></rect>
        <rect class="office-avatar-shirt" x="5" y="25" width="3" height="10"></rect>
        <rect class="office-avatar-shirt" x="24" y="25" width="3" height="10"></rect>
        <rect class="office-avatar-skin" x="6" y="33" width="3" height="3"></rect>
        <rect class="office-avatar-skin" x="23" y="33" width="3" height="3"></rect>
        <rect class="office-avatar-skin" x="9" y="8" width="14" height="14"></rect>
        <rect class="office-avatar-skin" x="7" y="12" width="2" height="6"></rect>
        <rect class="office-avatar-skin" x="23" y="12" width="2" height="6"></rect>
        <rect class="office-avatar-hair" x="9" y="5" width="14" height="5"></rect>
        <rect class="office-avatar-hair" x="7" y="8" width="4" height="7"></rect>
        <rect class="office-avatar-hair" x="21" y="8" width="4" height="7"></rect>
        <rect class="office-avatar-eye" x="12" y="14" width="2" height="2"></rect>
        <rect class="office-avatar-eye" x="18" y="14" width="2" height="2"></rect>
        <rect class="office-avatar-mouth" x="14" y="19" width="4" height="1"></rect>
      </g>
      <g class="office-avatar-workstation">
        <rect class="office-avatar-screen" x="20" y="25" width="10" height="9"></rect>
        <rect class="office-avatar-screen-stand" x="24" y="34" width="2" height="3"></rect>
        <rect class="office-avatar-desk" x="2" y="36" width="28" height="5"></rect>
        <rect class="office-avatar-desk" x="4" y="41" width="3" height="7"></rect>
        <rect class="office-avatar-desk" x="25" y="41" width="3" height="7"></rect>
      </g>
    </svg>'''


COMPANY_STATE_LABEL = {
    "IDLE": "대기",
    "PLANNING": "기획 중",
    "WORKING": "작업 중",
    "WAITING": "순서 대기",
    "REVIEWING": "검토 중",
    "BLOCKED": "차단됨",
    "DONE": "완료",
}

COMPANY_STATE_TONE = {
    "IDLE": "unknown",
    "PLANNING": "ready",
    "WORKING": "ready",
    "WAITING": "gated",
    "REVIEWING": "gated",
    "BLOCKED": "blocked",
    "DONE": "ready",
}


def _company_role_runtime(roles: list[RegisteredAgent],
                          tasks: list[dict[str, Any]],
                          live_job: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Derive role states only from TASKBOARD and the active run."""
    done_ids = {str(task.get("id", "")) for task in tasks
                if task.get("status") == "done"}
    running_id = ""
    if (live_job and live_job.get("action") == "team-run" and
            not (live_job.get("progress") or {}).get("done")):
        running_id = str(live_job.get("arg", ""))

    runtime = []
    for role in roles:
        assigned = [task for task in tasks
                    if str(task.get("agent_role", "")) == role.id]
        current: dict[str, Any] | None = None
        state = "IDLE"

        if running_id:
            current = next((task for task in assigned
                            if str(task.get("id", "")) == running_id), None)
            if current:
                state = "WORKING"
        if current is None:
            for task_status, company_state in (
                ("in_progress", "WORKING"),
                ("review", "REVIEWING"),
                ("blocked", "BLOCKED"),
                ("todo", "PLANNING"),
            ):
                current = next((task for task in assigned
                                if task.get("status") == task_status), None)
                if current:
                    state = company_state
                    break
        if current and state == "PLANNING":
            dependencies = [str(value) for value in current.get("depends_on", [])]
            if any(dependency not in done_ids for dependency in dependencies):
                state = "WAITING"
        if current is None:
            current = next((task for task in reversed(assigned)
                            if task.get("status") == "done"), None)
            if current:
                state = "DONE"

        last = next((task for task in reversed(assigned)
                     if task.get("status") in ("done", "review", "blocked")), None)
        current_title = "배정된 작업 없음"
        if current and state not in ("DONE", "IDLE"):
            current_title = str(current.get("title_ko") or current.get("title") or
                                current.get("id") or "제목 없음")
        last_result = "기록 없음"
        if last:
            result = str(last.get("status", "")).upper()
            when = str(last.get("last_run", "")).strip()
            last_result = f"{result}{' · ' + when if when else ''}"

        runtime.append({
            "role": role,
            "state": state,
            "current_task": current_title,
            "last_result": last_result,
        })
    return runtime


def _company_office_html(roles: list[RegisteredAgent],
                         tasks: list[dict[str, Any]], image_src: str,
                         live_job: dict[str, Any] | None = None) -> str:
    """Render AGENTS.json in a twelve-room block office."""
    runtime = _company_role_runtime(roles, tasks, live_job)
    if not image_src:
        return '<div class="office-empty">픽셀 사무실 배경이 없습니다.</div>'

    pins: list[str] = []
    avatars: list[str] = []
    zones: list[str] = []
    cards: list[str] = []
    shirt_tokens = ("var(--accent)", "var(--gate)", "var(--ok)", "var(--blocked)")

    for index, item in enumerate(runtime):
        role = item["role"]
        state = str(item["state"])
        row, column = divmod(index, 4)
        # The office building occupies the middle of the wide background;
        # these are the real centres of its four furnished rooms.
        centre_x = (22.0, 41.0, 57.0, 76.0)[column]
        zone_y = 5.0 + row * 27.0
        pin_y = 14.0 + row * 27.0
        # The generated background's three walkable floor lines are not
        # evenly spaced. Anchor the SVG's bottom edge to those exact lines so
        # characters never appear to float through the room.
        floor_y = (34.0, 58.4, 85.4)[row]
        from_x = centre_x - 4.5
        to_x = centre_x + 4.5
        desk_x = centre_x - 2.0
        desk_y = floor_y
        tone = COMPANY_STATE_TONE[state]
        seated = state in ("PLANNING", "WORKING", "REVIEWING")
        walking = state in ("IDLE", "DONE")
        motion = "working" if seated else ("walking" if walking else "still")
        shirt = shirt_tokens[index % len(shirt_tokens)]
        role_id = e(role.id)
        display = e(role.display_name)
        department = e(role.department)
        current = e(item["current_task"])
        last_result = e(item["last_result"])
        label = e(COMPANY_STATE_LABEL[state])

        zones.append(
            f'<span class="office-zone-label company-zone" '
            f'style="left:{centre_x:.1f}%;top:{zone_y:.1f}%">{department}</span>')
        avatars.append(
            f'<div class="office-avatar office-avatar--{motion} avatar-style-{index % 4}" '
            f'data-agent="{role_id}" data-default-motion="{motion}" aria-hidden="true" '
            f'style="--avatar-shirt:{shirt};--from-x:{from_x:.1f}%;'
            f'--from-y:{floor_y:.1f}%;--to-x:{to_x:.1f}%;--to-y:{floor_y:.1f}%;'
            f'--desk-x:{desk_x:.1f}%;--desk-y:{desk_y:.1f}%;'
            f'--delay:-{index * 1.1:.1f}s;--speed:{11 + index % 4 * 2}s">'
            f'{_office_avatar_svg()}</div>')
        pins.append(
            f'<a class="agent-hotspot company-state--{state.lower()} s-{tone}" '
            f'style="left:{centre_x:.1f}%;top:{pin_y:.1f}%" '
            f'href="#company-agent-{role_id}" '
            f'title="현재: {current} · 최근: {last_result}" '
            f'aria-label="{display} - {label}">'
            f'<span class="agent-photo-dot" aria-hidden="true"></span>'
            f'<span class="agent-hotspot-text"><b>{display}</b><span>{label}</span></span></a>')
        cards.append(
            f'<article class="company-role-card company-state--{state.lower()} s-{tone}" '
            f'id="company-agent-{role_id}"><span class="company-role-dept">{department}</span>'
            f'<b>{display}</b><span class="company-role-state">{state} · {label}</span>'
            f'<small>현재 · {current}</small><small>최근 · {last_result}</small></article>')

    legend = "".join(
        f'<span><i class="s-{COMPANY_STATE_TONE[state]}"></i>{state}</span>'
        for state in COMPANY_STATE_LABEL
    )
    return f'''<div class="virtual-office-shell minecraft-office">
      <div class="virtual-office-stage">
        <img class="virtual-office-image" src="{e(image_src)}"
          alt="블록 기반 2D 픽셀 사무실에서 일하는 열두 AI 부서">
        <div class="virtual-office-vignette"></div>
        {"".join(zones)}{"".join(avatars)}{"".join(pins)}
        <div class="virtual-office-legend company-legend" aria-label="회사 상태 범례">
          {legend}
        </div>
      </div>
      <div class="company-role-grid">{"".join(cards)}</div>
    </div>'''


def _virtual_office_html(agents: list[Agent], image_src: str,
                         working_prefix: str = "") -> str:
    """Place live agent status over the photo-real 3D office scene."""
    if not image_src:
        return _office_html(agents, working_prefix)

    positions = {
        "Claude Code": (18.0, 23.0),
        "Gemini": (40.0, 18.0),
        "유료 API": (70.0, 20.0),
        "Ollama": (18.0, 45.0),
        "Qwen-Image": (36.0, 45.0),
        "Codex CLI": (68.0, 46.0),
        "Blender": (21.0, 69.0),
        "Stable Diffusion": (79.0, 70.0),
    }
    routes = {
        "Claude Code": (9.0, 29.0, 34.0, 29.0, 18.0, 27.5),
        "Gemini": (13.0, 29.0, 37.0, 29.0, 31.0, 27.5),
        "유료 API": (62.0, 29.0, 89.0, 29.0, 73.0, 27.5),
        "Ollama": (9.0, 58.0, 34.0, 58.0, 18.0, 55.5),
        "Qwen-Image": (15.0, 58.0, 38.0, 58.0, 31.0, 55.5),
        "Codex CLI": (62.0, 58.0, 89.0, 58.0, 71.0, 55.5),
        "Blender": (9.0, 86.0, 35.0, 86.0, 21.0, 83.0),
        "Stable Diffusion": (62.0, 86.0, 89.0, 86.0, 79.0, 83.0),
    }

    def coordinates(name: str, index: int) -> tuple[float, float]:
        for prefix, point in positions.items():
            if name.startswith(prefix):
                return point
        return 50.0 + (index % 3) * 8.0, 78.0

    def route(name: str, index: int) -> tuple[float, float, float, float, float, float]:
        for prefix, points in routes.items():
            if name.startswith(prefix):
                return points
        start = 46.0 + (index % 3) * 8.0
        return start, 82.0, start + 7.0, 82.0, start + 3.0, 79.0

    pins = []
    avatars = []
    for index, agent in enumerate(agents):
        x, y = coordinates(agent.name, index)
        from_x, from_y, to_x, to_y, desk_x, desk_y = route(agent.name, index)
        department = _department_for_agent(agent.name)
        working = bool(working_prefix) and agent.name.startswith(working_prefix)
        caption = _office_caption(agent, working)
        extra = " office-agent--working" if working else ""
        motion = "working" if working else "walking"
        avatars.append(
            f'<div class="office-avatar office-avatar--{motion} '
            f'office-agent--{department} avatar-style-{index % 4}" '
            f'data-agent="{e(agent.name)}" aria-hidden="true" '
            f'style="--from-x:{from_x:.1f}%;--from-y:{from_y:.1f}%;'
            f'--to-x:{to_x:.1f}%;--to-y:{to_y:.1f}%;'
            f'--desk-x:{desk_x:.1f}%;--desk-y:{desk_y:.1f}%;'
            f'--delay:-{index * 1.3:.1f}s;--speed:{12 + index % 4 * 2}s">'
            f'{_office_avatar_svg()}</div>')
        pins.append(
            f'<a class="agent-hotspot office-agent--{department} '
            f'office-agent--{agent.state}{extra}" '
            f'style="left:{x:.1f}%;top:{y:.1f}%" '
            f'href="#agent-{e(_agent_slug(agent.name))}" '
            f'title="{e(agent.detail)}" aria-label="{e(agent.name)} - {e(caption)}">'
            f'<span class="agent-photo-dot" aria-hidden="true"></span>'
            f'<span class="agent-hotspot-text"><b>{e(agent.name)}</b>'
            f'<span>{e(caption)}</span></span></a>')

    zones = [
        ("게임 기획 · 개발", 24, 7),
        ("전략 · 운영", 73, 7),
        ("로컬 AI 연구소", 23, 34),
        ("Unity 제작 · QA", 73, 34),
        ("3D 모델링", 23, 59),
        ("디자인 · 게임 스튜디오", 74, 59),
    ]
    if any(_department_for_agent(agent.name) == "etc" for agent in agents):
        zones.append((DEPARTMENT_LABEL["etc"], 58, 68))
    zone_html = "".join(
        f'<span class="office-zone-label" style="left:{x}%;top:{y}%">{e(label)}</span>'
        for label, x, y in zones
    )
    return f'''<div class="virtual-office-shell">
      <div class="virtual-office-stage">
        <img class="virtual-office-image" src="{e(image_src)}"
          alt="16비트 픽셀 아바타들이 움직이며 일하는 미니홈피 게임 회사 사무실">
        <div class="virtual-office-vignette"></div>
        {zone_html}{"".join(avatars)}{"".join(pins)}
        <div class="virtual-office-legend" aria-label="에이전트 상태 범례">
          <span><i style="--legend:var(--ok)"></i>작업 가능</span>
          <span><i style="--legend:var(--gate)"></i>대기</span>
          <span><i style="--legend:var(--blocked)"></i>사용 불가</span>
          <span><i style="--legend:var(--unknown)"></i>확인 불가</span>
        </div>
      </div>
    </div>'''


def _short_path(pattern: str) -> str:
    """Just enough of an allowlist entry to recognise it.

    Full repo-relative paths are what the board stores and what the allowlist
    check needs, but four of them on a card is a wall of "Assets/GameFactory/"
    with the only distinguishing part off the right edge.
    """
    cleaned = pattern.replace("\\", "/").rstrip("/")
    tail = cleaned.rsplit("/", 1)[-1] if cleaned else pattern
    return f"{tail}/" if pattern.rstrip().endswith("/") else tail


def _live_html(job: dict[str, Any] | None, panel: bool = False) -> str:
    """The Korean progress block for a running job.

    Renders nothing at all when there is no job - including on the static
    page, which has no server behind it and therefore no live state to read.
    Inventing one there would be the same lie as a button that cannot post.
    """
    if not job:
        return ""
    progress = job.get("progress") or {}
    phase = str(progress.get("phase", ""))
    if not phase:
        return ""

    done = bool(progress.get("done"))
    exit_code = progress.get("exit_code")
    tone = ""
    if done:
        tone = " live--done" if exit_code == 0 else " live--failed"
    where = " live--panel" if panel else ""
    dots = "" if done else ' <span class="live-dots" aria-hidden="true"></span>'

    elapsed = str(progress.get("elapsed", ""))
    elapsed_html = (f'<span class="live-elapsed">{e(elapsed)} 경과</span>'
                    if elapsed else "")

    what = str(progress.get("action_label", ""))
    arg = str(job.get("arg", ""))
    if arg:
        what = f"{what} · {arg}" if what else arg

    evidence = str(progress.get("evidence", ""))
    evidence_html = (f'<div class="live-evidence mono">{e(evidence)}</div>'
                     if evidence else "")

    # Said out loud rather than left to be discovered: run_pipeline prints its
    # step results only after Unity returns, so a build genuinely shows
    # nothing for a long time. Without this line the page looks hung.
    note = ""
    if progress.get("slow") and not done:
        note = ('<div class="live-note">이 단계는 외부 프로그램이 끝날 때까지 '
                '출력이 나오지 않습니다. 멈춘 것이 아닙니다.</div>')

    return f"""<div class="live{tone}{where}" aria-live="polite">
            <div class="live-top">
              <span class="live-phase">{e(phase)}{dots}</span>
              {elapsed_html}
            </div>
            <div class="live-what">{e(what)}</div>
            {evidence_html}
            {note}
          </div>"""


QUEUE_STATUSES = ("in_progress", "todo")

# Owner id -> the name shown on the queue lane. An owner not listed here still
# gets a lane, titled with its raw id, rather than having its work disappear.
OWNER_LABEL = {"claude": "Claude", "codex": "Codex"}


def _queue_item(index: int, task: dict[str, Any], served: bool,
                unmet: list[str], live: dict[str, Any] | None) -> str:
    running = live is not None
    status = str(task.get("status", "todo"))
    if running:
        kind, seat = "q-item--running", "▶"
    elif unmet:
        kind, seat = "q-item--waiting-deps", "!"
    else:
        kind, seat = "q-item--waiting", str(index)

    if running:
        label, css = "진행 중", "s-in_progress"
    elif unmet:
        label, css = "선행 작업 대기", "s-blocked-tag"
    elif status == "in_progress":
        # The board says a run started and nothing on this machine is running
        # it. That is a stale record, not work in flight - say which it is.
        label, css = "기록만 진행 중", "s-review"
    else:
        label, css = "대기", "s-todo"

    deps = ""
    if unmet:
        ids = ", ".join(f"<code>{e(d)}</code>" for d in unmet)
        deps = f'<div class="q-deps">선행 작업: {ids}</div>'

    action = ""
    if served and task.get("owner") == "codex" and not running:
        # data-blocked is what lock() reads to keep a button disabled when the
        # panel re-enables the rest, so it has to travel with disabled.
        disabled = ' disabled data-blocked="true"' if unmet else ""
        button = "작업 시작" if status == "todo" else "다시 실행"
        action = (f'<div class="q-act"><button class="btn" type="button" '
                  f'data-act="team-run" data-arg-value="{e(task.get("id", ""))}"'
                  f' data-agent-role="{e(task.get("agent_role", ""))}"'
                  f'{disabled}>{button}</button></div>')

    return f"""        <div class="q-item {kind}">
          <span class="q-seat" aria-hidden="true">{e(seat)}</span>
          <div class="q-head">
            <span class="q-title" title="{e(task.get('title', ''))}">{e(task_title(task))}</span>
            <span class="tag {css}">{e(label)}</span>
          </div>
          <div class="q-id mono">{e(task.get('id', ''))}</div>
          {deps}{_live_html(live)}{action}
        </div>"""


def _queue_html(tasks: list[dict[str, Any]], unmet_by_id: dict[str, list[str]],
                served: bool, live_job: dict[str, Any] | None) -> str:
    """Who is working on what, and what is next in line.

    Deliberately not the same list as the board section. Done and review are
    left out: review means a human owes it a look, which is not queue work,
    and a queue that includes finished items stops answering "what is next".
    """
    running_id = ""
    if live_job and live_job.get("action") == "team-run":
        running_id = str(live_job.get("arg", ""))

    by_owner: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if str(task.get("status", "")) in QUEUE_STATUSES:
            by_owner.setdefault(str(task.get("owner", "기타")), []).append(task)

    if not by_owner:
        return ('<div class="q-lane"><div class="q-empty">'
                '대기 중인 작업이 없습니다. 작업판의 모든 항목이 완료 또는 검토 상태입니다.'
                '</div></div>')

    lanes = []
    for owner in sorted(by_owner, key=lambda o: (o != "codex", o)):
        owned = by_owner[owner]
        # Running first, then runnable in board order, then dependency-blocked.
        def rank(task: dict[str, Any]) -> tuple[int, int]:
            task_id = str(task.get("id", ""))
            if task_id == running_id:
                return (0, 0)
            return (2 if unmet_by_id.get(task_id) else 1, owned.index(task))

        ordered = sorted(owned, key=rank)

        running = held = waiting = 0
        rows = []
        for task in ordered:
            task_id = str(task.get("id", ""))
            is_running = task_id == running_id
            unmet = unmet_by_id.get(task_id, [])
            if is_running:
                running += 1
            elif unmet:
                held += 1
            else:
                waiting += 1
            # The seat number counts only what is actually queued, so the
            # first runnable task is 1 whether or not something is running.
            rows.append(_queue_item(waiting, task, served, unmet,
                                    live_job if is_running else None))

        tally = f"대기 {waiting}"
        if held:
            tally += f" · 선행 대기 {held}"
        if running:
            tally = f"진행 중 {running} · " + tally
        lanes.append(f"""      <div class="q-lane">
        <h3>{e(OWNER_LABEL.get(owner, owner))}<span class="q-tally">{e(tally)}</span></h3>
{chr(10).join(rows)}
      </div>""")
    return chr(10).join(lanes)


def task_title(task: dict[str, Any]) -> str:
    """The title the page shows: Korean when the board has one.

    The board's `title` is what Codex is prompted with and stays English; the
    page is read by a Korean speaker and was the one place on it still in
    English. Falls back to `title` so a task without a translation is never
    blank.
    """
    return str(task.get("title_ko") or task.get("title") or task.get("id") or "")


def _task_row(task: dict[str, Any], served: bool = False,
              unmet_dependencies: list[str] | None = None) -> str:
    status = str(task.get("status", "todo"))
    css = "s-blocked-tag" if status == "blocked" else f"s-{status}"
    label = {"todo": "대기", "in_progress": "진행 중", "review": "검토 필요",
             "blocked": "막힘", "done": "완료"}.get(status, status)

    paths = task.get("files", []) or []
    files = " · ".join(_short_path(p) for p in paths[:4])
    if len(paths) > 4:
        files += f" 외 {len(paths) - 4}개"

    action = ""
    # One inline button, and only where nothing else offers one. The queue
    # owns todo/in_progress; the picker in AI 제어 covers review. That leaves
    # blocked, which is the status you actually re-run once its cause is
    # fixed. Before this the board drew a button per task and the page
    # carried 27 of them, 17 for review alone - a wall of identical controls
    # for the one action a review task does not normally need.
    if served and task.get("owner") == "codex" and status == "blocked":
        unmet = unmet_dependencies or []
        disabled = ' disabled data-blocked="true"' if unmet else ""
        blockers = ""
        if unmet:
            ids = ", ".join(e(dependency) for dependency in unmet)
            blockers = f'<span class="task-blockers">선행 작업: <code>{ids}</code></span>'
        action = (f'\n          <div class="task-action">'
                  f'<button class="btn task-run" type="button" data-act="team-run" '
                  f'data-arg-value="{e(task.get("id", ""))}" '
                  f'data-agent-role="{e(task.get("agent_role", ""))}"{disabled}>'
                  f'다시 실행</button>{blockers}</div>')
    original = str(task.get("title", ""))
    hover = f' title="{e(original)}"' if task.get("title_ko") and original else ""
    return f"""        <div class="task">
          <div class="t">
            <span class="title"{hover}>{e(task_title(task))}</span>
            <span class="tag {css}">{e(label)}</span>
          </div>
          <div class="id mono">{e(task.get('id', ''))}</div>
          <div class="files mono">{e(files)}</div>{action}
        </div>"""


SYNC_OUTCOME_LABEL = {
    "OK": ("동기화됨", "ok"),
    "UP-TO-DATE": ("최신 상태", "ok"),
    "BLOCKED": ("머지 보류", "blocked"),
    "FAILED": ("실패", "blocked"),
}

RUN_OUTCOME_LABEL = {
    "OK": ("성공", "ok"),
    "FAILED": ("실패", "blocked"),
    "RAISED": ("예외 발생", "blocked"),
    "UNKNOWN": ("확인 불가", "unknown"),
}


def _kv(label: str, value_html: str) -> str:
    return (f'<div class="kv"><span class="k">{e(label)}</span>'
            f'<span class="v">{value_html}</span></div>')


def _link_panel_sync(status: dict[str, str]) -> str:
    """How the PC's scheduled sync is doing, from the file it commits.

    The sync used to fail silently: its log is gitignored, so a dirty tracked
    file could freeze every merge for a day and nothing on this page moved.
    Now it commits its outcome, and this is where that lands.
    """
    if not status:
        body = _kv("상태", '<span style="color:var(--unknown)">확인 불가</span>')
        body += ('<div class="kv-note">Reports/sync-status/latest.txt 가 없습니다. '
                 'PC의 sync-and-run.ps1 이 이 파일을 쓰기 전이거나, 예약 작업이 돌지 않았습니다.</div>')
        return f'<div class="panel"><h3>PC 자동 동기화</h3>{body}</div>'

    outcome = status.get("Outcome", "")
    label, tone = SYNC_OUTCOME_LABEL.get(outcome, (outcome or "확인 불가", "unknown"))
    body = _kv("상태", f'<span class="big" style="color:var(--{tone})">{e(label)}</span>')

    age = hours_since(status.get("Generated", ""))
    stale = age is not None and age > SYNC_STALE_HOURS
    when = e(status.get("Generated", "") or "없음")
    if stale:
        when += f' <span style="color:var(--blocked)">· {age:.0f}시간 전</span>'
    body += _kv("마지막 실행", f'<span class="mono">{when}</span>')
    body += _kv("마지막 성공", f'<span class="mono">{e(status.get("Last-Success", "") or "없음")}</span>')

    local, upstream = status.get("Local-Head", ""), status.get("Upstream-Head", "")
    if local and upstream:
        behind = local != upstream
        heads = f'PC {e(local)} · Claude {e(upstream)}'
        if behind:
            heads += ' <span style="color:var(--gate)">· 다름</span>'
        body += _kv("커밋", f'<span class="mono">{heads}</span>')

    reason = status.get("Reason", "")
    if tone == "blocked" and reason:
        body += f'<div class="kv-note" style="color:var(--blocked)">{e(reason[:400])}</div>'
    if stale:
        body += ('<div class="kv-note" style="color:var(--blocked)">15분마다 돌아야 하는 예약 작업이 '
                 f'{age:.0f}시간 동안 상태를 남기지 않았습니다. 작업 스케줄러에서 등록 상태를 확인하세요.</div>')
    return f'<div class="panel"><h3>PC 자동 동기화</h3>{body}</div>'


def _link_panel_run(run: dict[str, str]) -> str:
    """The last orchestrator command that ran on the PC, from Reports/runs/."""
    if not run:
        body = _kv("상태", '<span style="color:var(--unknown)">확인 불가</span>')
        body += ('<div class="kv-note">Reports/runs/latest.txt 가 없습니다. 오케스트레이터가 '
                 '이 기록을 남기는 버전으로 아직 실행되지 않았습니다.</div>')
        return f'<div class="panel"><h3>마지막 오케스트레이터 실행</h3>{body}</div>'

    outcome = run.get("Outcome", "")
    label, tone = RUN_OUTCOME_LABEL.get(outcome, (outcome or "확인 불가", "unknown"))
    body = _kv("결과", f'<span class="big" style="color:var(--{tone})">{e(label)}</span>')
    body += _kv("명령", f'<span class="mono">{e(run.get("Command", "") or "?")}</span>')
    body += _kv("실행 시각", f'<span class="mono">{e(run.get("Generated", "") or "없음")}</span>')
    detail = " · ".join(x for x in (
        f'종료 코드 {run["Exit"]}' if run.get("Exit") not in (None, "", "-") else "",
        run.get("Duration", ""),
    ) if x)
    if detail:
        body += _kv("상세", f'<span class="mono">{e(detail)}</span>')
    if tone == "blocked":
        body += ('<div class="kv-note">전체 출력은 Reports/runs/latest.txt 에 있습니다. '
                 'Claude가 포크에서 직접 읽으므로 붙여넣을 필요가 없습니다.</div>')
    return f'<div class="panel"><h3>마지막 오케스트레이터 실행</h3>{body}</div>'


def _gallery_html(groups: list[dict[str, Any]]) -> str:
    blocks = []
    for group in groups:
        items = group["items"]
        if not items:
            body = f'<div class="gal-empty">{e(group["empty"])}</div>'
        else:
            shots = []
            for item in items:
                if item["src"]:
                    figure = (f'<img class="{"px" if item["pixel_art"] else ""}" '
                              f'src="{item["src"]}" alt="{e(item["name"])}" loading="lazy">')
                else:
                    figure = f'<div class="miss">{e(item["note"])}</div>'
                meta = " · ".join(x for x in (item["dimensions"],
                                              f"{item['kb']:.0f} KB") if x)
                shots.append(
                    f'<figure class="shot" style="margin:0">'
                    f'<div class="frame">{figure}</div>'
                    f'<figcaption class="cap"><b title="{e(item["rel"])}">'
                    f'{e(item["label"])}</b>'
                    f'<span class="mono">{e(meta)}</span></figcaption></figure>')
            body = f'<div class="gal">{"".join(shots)}</div>'

        count = f'{len(items)}장' if items else "0장"
        blocks.append(f"""    <div class="gal-wrap">
      <div class="h"><b>{e(group["title"])}</b><span>{e(count)} · {e(group["note"])}</span></div>
{body}
    </div>""")
    return "\n".join(blocks)


def _art_plan_html(plan: dict[str, Any]) -> str:
    """Why the gallery is shorter than the plan.

    Three reasons, kept apart because the fix for each is different: a copy
    step nobody ran, a task blocked on missing code, and a decision.
    """
    if not plan.get("source"):
        return ('<div class="gal-empty">art-mapping.json 을 읽지 못했습니다. '
                '계획 대비 실제를 비교할 수 없습니다.</div>')

    def clip(text: str, limit: int = 220) -> str:
        return text if len(text) <= limit else text[:limit].rstrip() + "…"

    rows = []
    for item in plan["missing"]:
        rows.append(
            f'<div class="r s-blocked-tag"><span class="t">복사 안 됨</span>'
            f'<span class="n">{e(item["path"])}</span>'
            f'<span class="w">{e(item["pack"])} 의 {e(item["file"])} 에서 와야 합니다. '
            f'PC에서 <code>AI_GAME_COMPANY\\tools\\apply-art-mapping.ps1 -Commit</code> '
            f'를 실행하면 복사되고 저장소에 올라옵니다.</span></div>')

    for item in plan["queued"]:
        rows.append(
            f'<div class="r s-review"><span class="t">대기</span>'
            f'<span class="n">{e(item["what"])}</span>'
            f'<span class="w">{e(clip(item["why"]))}</span></div>')

    for item in plan["declined"]:
        rows.append(
            f'<div class="r s-todo"><span class="t">제외</span>'
            f'<span class="n">{e(item["path"])}</span>'
            f'<span class="w">{e(clip(item["why"]))}</span></div>')

    if not rows:
        rows.append('<div class="r s-done"><span class="t">완료</span>'
                    '<span class="n">계획된 아트가 모두 저장소에 있습니다.</span></div>')

    return f'<div class="panel"><div class="plan">{"".join(rows)}</div></div>'


# Three rows of this panel used to draw a button for an action server.ACTIONS
# does not contain, so pressing it answered "알 수 없는 동작입니다." and nothing
# else. Section 10 is explicit that a control with nothing behind it should not
# be drawn, so the state is reported and the control is not. Wiring the three
# actions for real is queued separately - this only stops the page lying about
# what it can do.
_UNWIRED = ('<span class="control-note" style="color:var(--unknown)">'
            '실행 버튼 미연결 — 이 동작은 아직 서버에 없습니다</span>')


def _scope_label(pattern: str) -> str:
    """A team's allowlist entry, short enough for one line and still readable.

    _short_path takes the last segment, which is right for the board's
    directory entries and useless for a glob: every one of these patterns ends
    in '**', so three of them rendered as '** · ** · **'. What distinguishes
    them is the segment BEFORE the glob.
    """
    cleaned = pattern.replace("\\", "/")
    if cleaned.endswith("/**"):
        return cleaned[:-3].rsplit("/", 1)[-1] + "/"
    head, _, tail = cleaned.rpartition("/")
    if "*" in tail and head:
        # A filename glob keeps its directory: 'GameSpecs/*.json' says more
        # than '*.json', which could be anywhere.
        return f"{head.rsplit('/', 1)[-1]}/{tail}"
    return _short_path(pattern)


def _studio_html(snapshot: Snapshot) -> str:
    """The primary flow: idea -> safe plan -> GameSpec -> Unity -> APK."""
    character_src = ""
    for group in snapshot.gallery:
        for item in group.get("items", []):
            if str(item.get("rel", "")).replace("\\", "/") == \
                    "Assets/Common/Art/Runner/player.png":
                character_src = str(item.get("src", ""))
                break
        if character_src:
            break
    character = (
        f'<img src="{e(character_src)}" alt="공용 캐릭터 도리">'
        if character_src else '<div style="font-size:64px" aria-label="도리">🦔</div>'
    )

    enabled_models = [model["name"] for model in snapshot.ollama_models
                      if model.get("enabled")]
    if enabled_models:
        ai_note = f'Ollama 보강 가능 · {e(enabled_models[0])}'
    else:
        ai_note = ("로컬 자동 설계 엔진 사용 중 · Ollama는 라이선스와 메모리 검사를 "
                   "통과한 모델이 연결되면 기획 보강에 사용합니다.")

    next_game = next((game["id"] for game in snapshot.games if not game["spec"]), "새 슬롯 없음")
    return f"""  <section class="studio-section" id="studio">
    <div class="head">
      <h2>도리 AI 게임 스튜디오</h2>
      <span class="note">다음 생성 슬롯 · {e(next_game)}</span>
    </div>
    <div class="studio">
      <aside class="studio-character">
        {character}
        <h3>도리</h3>
        <div class="lock">🔒 모든 게임에서 같은 캐릭터</div>
        <p>캐릭터 원본과 조작 방식은 유지하고<br>규칙·속도·난이도·테마를 새로 설계합니다.</p>
      </aside>
      <div class="studio-main">
        <div class="studio-steps" aria-label="자동 생성 단계">
          <span class="studio-step"><b>1</b> 아이디어</span>
          <span class="studio-step"><b>2</b> AI 설계</span>
          <span class="studio-step"><b>3</b> Unity 검증</span>
          <span class="studio-step"><b>4</b> APK</span>
        </div>
        <div class="creator-form">
          <label>어떤 게임을 만들까요?
            <textarea id="creator-idea" maxlength="800" placeholder="예: 사탕 왕국에서 코인을 연속으로 모으며 거대 젤리를 피하는 빠른 러너"></textarea>
          </label>
          <label>게임 이름 <span style="font-weight:400;color:var(--muted)">비워두면 자동 생성</span>
            <input id="creator-title" maxlength="60" placeholder="도리 캔디 러시">
          </label>
          <div class="creator-options">
            <label>플레이 스타일
              <select id="creator-style">
                <option value="auto">AI 추천</option>
                <option value="adventure">어드벤처</option>
                <option value="treasure">코인 러시</option>
                <option value="gravity">중력 반전</option>
                <option value="speed">스피드 탈출</option>
                <option value="endurance">무한 생존</option>
              </select>
            </label>
            <label>난이도
              <select id="creator-difficulty">
                <option value="auto">AI 추천</option>
                <option value="Easy">쉬움</option>
                <option value="Medium">보통</option>
                <option value="Hard">어려움</option>
              </select>
            </label>
            <label>세계관
              <select id="creator-theme">
                <option value="auto">AI 추천</option>
                <option value="Factory">팩토리</option>
                <option value="Candy">캔디</option>
                <option value="Sky">스카이</option>
                <option value="Forest">포레스트</option>
                <option value="Neon">네온</option>
                <option value="Lava">라바</option>
              </select>
            </label>
            <label>자동화 범위
              <select id="creator-pipeline">
                <option value="full">테스트 + APK</option>
                <option value="build">APK 바로 빌드</option>
                <option value="test">테스트까지</option>
                <option value="spec">기획만 저장</option>
              </select>
            </label>
          </div>
          <div class="creator-actions">
            <button class="btn preview" id="creator-preview" type="button">AI 기획 미리보기</button>
            <button class="btn primary" id="creator-create" type="button">새 게임 자동 생성</button>
            <span class="creator-ai-note">{ai_note}</span>
          </div>
        </div>
        <div class="creator-result" id="creator-result" aria-live="polite"></div>
      </div>
    </div>
  </section>

"""


def _order_html(snapshot: Snapshot) -> str:
    """The command window: one sentence in, real work out.

    Rendered only behind a server, like the rest of the control panel - a
    static copy has nothing to POST to, and a box that swallowed an
    instruction and did nothing with it would be the worst control on the page.

    What it does NOT do is as important as what it does, and is said on the
    page rather than only in the code: the text becomes a task on the board,
    Codex reads it, and Codex cannot compile. So the order runs the Unity
    tests afterwards, and the page says that is why.
    """
    teams = []
    for dept in orders.DEPARTMENTS.values():
        if dept.unavailable:
            teams.append(
                f'<option value="{e(dept.id)}" disabled>'
                f'{e(dept.label)} · 지금은 맡길 수 없음</option>')
            continue
        teams.append(
            f'<option value="{e(dept.id)}" '
            f'data-summary="{e(dept.summary)}" '
            f'data-files="{e(" · ".join(_scope_label(f) for f in dept.files))}" '
            f'data-seat="{e(dept.seat)}">{e(dept.label)} · {e(dept.summary)}</option>')

    closed = [d for d in orders.DEPARTMENTS.values() if d.unavailable]
    closed_note = "".join(
        f'<div class="order-closed">{e(d.label)} — {e(d.unavailable)}</div>'
        for d in closed)

    games = [g for g in snapshot.games if g["spec"]]
    game_options = "".join(
        f'<option value="{e(g["id"])}">{e(g["id"])}</option>' for g in games)
    if games:
        verify_row = (
            '<label class="order-check"><input type="checkbox" id="order-verify" checked> '
            '끝나면 Unity 테스트까지 돌린다</label>'
            f'<select id="order-game" aria-label="테스트할 게임">{game_options}</select>')
    else:
        # No GameSpec means nothing to test against. Said, not silently
        # dropped: an order will still run, it just cannot be checked.
        verify_row = ('<span class="control-note">GameSpec 이 없어서 테스트 단계는 '
                      '건너뜁니다. Codex 결과는 컴파일 확인 없이 남습니다.</span>')

    return f"""  <section>
    <div class="head">
      <h2>명령창</h2>
      <span class="note">한 줄로 지시하면 담당 팀이 일합니다 · 최대 {orders.MAX_ORDER_CHARS}자</span>
    </div>
    <div class="ctl">
      <div class="order">
        <div class="order-row">
          <select id="order-dept" aria-label="지시를 맡길 팀">{"".join(teams)}</select>
          <span class="order-scope mono" id="order-scope"></span>
        </div>
        <textarea id="order-text" rows="4" maxlength="{orders.MAX_ORDER_CHARS}"
          aria-label="지시 내용"
          placeholder="예) 점프를 더 무겁게. 올라갈 때보다 내려올 때가 빠르게 느껴지도록."></textarea>
        <div class="order-row">
          {verify_row}
          <button class="btn" id="order-send">지시 보내기</button>
        </div>
        <div class="order-how">
          지시는 작업판에 <span class="mono">ORDER-날짜-번호</span> 로 접수되고, Codex가
          그 글을 그대로 읽고 작업합니다. <b>Codex는 컴파일을 못 합니다</b> — 그래서
          끝나면 Unity 테스트를 이어서 돌립니다. 커밋과 푸시는 하지 않으니
          결과는 검토한 뒤 직접 커밋하세요.
        </div>
        {closed_note}
      </div>
    </div>
  </section>

"""


def _control_html(snapshot: Snapshot, token: str,
                  live_job: dict[str, Any] | None = None) -> str:
    """The action panel. Rendered ONLY when a local server is behind it.

    A static copy of this page - the one written to Reports/ or published as an
    Artifact - has nothing to POST to, so it must not show buttons at all.
    A control that does nothing is worse than an absent one.
    """
    # Every re-runnable codex task in one picker. review is included on
    # purpose: those used to carry 17 inline buttons on the board, which is
    # the repetition this picker replaces. done is not re-runnable and is
    # deliberately absent.
    codex_open = [t for t in snapshot.tasks
                  if t.get("owner") == "codex"
                  and t.get("status") in ("todo", "in_progress", "review", "blocked")]
    task_options = "".join(
        f'<option value="{e(t["id"])}">{e(t["id"])} · {e(task_title(t))}</option>'
        for t in codex_open) or '<option value="">넘길 작업이 없습니다</option>'

    game_options = "".join(
        f'<option value="{e(g["id"])}">{e(g["id"])}</option>'
        for g in snapshot.games if g["spec"]) or '<option value="">GameSpec 없음</option>'

    if snapshot.ollama_error:
        ollama_control = (
            '<span class="control-note">설치 목록 확인 실패: '
            f'{e(snapshot.ollama_error)}</span>')
    elif not snapshot.ollama_models:
        ollama_control = '<span class="control-note">설치된 모델 없음</span>'
    else:
        enabled_models = [model for model in snapshot.ollama_models if model["enabled"]]
        selected = next((model["name"] for model in enabled_models
                         if model.get("active")),
                        enabled_models[0]["name"] if enabled_models else "")
        options = []
        reasons = []
        for model in snapshot.ollama_models:
            disabled = "" if model["enabled"] else " disabled"
            selected_attr = " selected" if model["name"] == selected else ""
            active = " · 사용 중" if model.get("active") else ""
            reason = f' · {model["reason"]}' if model.get("reason") else ""
            options.append(
                f'<option value="{e(model["name"])}"{disabled}{selected_attr}>'
                f'{e(model["name"])}{e(active)}{e(reason)}</option>')
            if model.get("reason"):
                reasons.append(
                    f'<span>{e(model["name"])} — {e(model["reason"])}</span>')
        # No button: server.ACTIONS has no 'ollama-use', so one drawn here
        # would POST an action the server answers "알 수 없는 동작" to. Section
        # 10 - a control with nothing behind it is worse than none - and this
        # panel had three of them. The list itself is still real information,
        # so it stays; only the dead control goes.
        ollama_control = (
            f'<select id="ollama-model" aria-label="설치된 Ollama 모델" disabled>'
            f'{"".join(options)}</select>'
            f'{_UNWIRED}'
            f'<div class="control-reasons">{"".join(reasons)}</div>')

    image_allowed = (
        snapshot.image_adapter
        and snapshot.policy.get("allow_local_image_generation") is True
        and snapshot.licences.get("stable-diffusion-v1-5") == "APPROVED"
    )
    if image_allowed:
        # Allowed by policy and licence, but see _UNWIRED: there is no
        # 'image-generate' action on the server, so the button is not drawn.
        image_control = (
            '<span class="control-note">정책·라이선스 통과 (stable-diffusion-v1-5)</span>'
            f'{_UNWIRED}')
    elif not snapshot.image_adapter:
        image_control = '<span class="control-note">generate-sprite.py 없음</span>'
    else:
        image_control = '<span class="control-note">정책 또는 모델 라이선스가 허용하지 않음</span>'

    if snapshot.gemini_adapter:
        gemini_agent = next(
            (agent for agent in snapshot.agents if agent.name == "Gemini"), None)
        # Reported from the agent row's own evidence (the key gate), then the
        # same _UNWIRED note: there is no 'gemini-design' action, and unlike
        # the other two there is no CLI subcommand behind one either.
        state_note = (f'<span class="control-note">{e(gemini_agent.detail)}</span>'
                      if gemini_agent else "")
        gemini_control = f"{state_note}{_UNWIRED}"
    else:
        gemini_control = '<span class="control-note">gemini_client.py 없음</span>'

    blender_control = '<span class="control-note">blender_runner.py 없음</span>'

    # Rendered server-side as well as by the poller: reloading the page during
    # a run must not blank the phase until the first fetch comes back.
    live_panel = _live_html(live_job, panel=True)

    return f"""  <section>
    <div class="head">
      <h2>AI 제어</h2>
      <span class="note">이 PC에서 실행됩니다 · 커밋과 푸시는 하지 않습니다</span>
    </div>
    <div class="ctl">
      <div class="control-grid">
        <div class="control-row">
          <div class="control-name">Codex</div>
          <div class="control-body"><div class="combo">
            <select id="task" aria-label="Codex에게 넘길 작업">{task_options}</select>
            <button class="btn" data-act="team-run" data-arg="task">Codex 실행</button>
          </div><button class="btn ghost" data-act="codex-doctor">진단</button></div>
        </div>
        <div class="control-row">
          <div class="control-name">Unity</div>
          <div class="control-body"><div class="combo">
            <select id="game" aria-label="빌드할 게임">{game_options}</select>
            <button class="btn" data-act="build" data-arg="game">빌드</button>
          </div></div>
        </div>
        <div class="control-row"><div class="control-name">Ollama</div>
          <div class="control-body">{ollama_control}<span class="control-note">다운로드 기능 없음</span></div>
        </div>
        <div class="control-row"><div class="control-name">로컬 이미지 생성</div>
          <div class="control-body">{image_control}</div>
        </div>
        <div class="control-row"><div class="control-name">Gemini</div>
          <div class="control-body">{gemini_control}</div>
        </div>
        <div class="control-row"><div class="control-name">Blender</div>
          <div class="control-body">{blender_control}</div>
        </div>
      </div>
      <div class="acts">
        <button class="btn ghost" data-act="git-status">변경된 파일</button>
        <button class="btn ghost" data-act="dashboard">새로고침</button>
        <span id="busy"></span>
      </div>
      <div id="live">{live_panel}</div>
      <pre class="term" id="term" aria-live="polite"></pre>
    </div>
  </section>

  <script>
  document.addEventListener('DOMContentLoaded', () => {{
    const TOKEN = {json.dumps(token)};
    const term = document.getElementById('term');
    const busy = document.getElementById('busy');
    const live = document.getElementById('live');
    const buttons = [...document.querySelectorAll('.btn[data-act]')];
    const send = document.getElementById('order-send');
    const creatorPreview = document.getElementById('creator-preview');
    const creatorCreate = document.getElementById('creator-create');
    const creatorResult = document.getElementById('creator-result');
    const officeAvatars = [...document.querySelectorAll('.office-avatar')];
    const actionAgent = {{
      'codex-doctor':'technical_director',
      'build':'release_engineer',
      'test':'qa_engineer'
    }};
    let poll = null;
    let createdGame = '';

    function setOfficeWorker(prefix) {{
      officeAvatars.forEach(avatar => {{
        const working = Boolean(prefix) && avatar.dataset.agent.startsWith(prefix);
        avatar.classList.remove('office-avatar--working', 'office-avatar--walking',
          'office-avatar--still');
        avatar.classList.add('office-avatar--' +
          (working ? 'working' : (avatar.dataset.defaultMotion || 'walking')));
      }});
    }}

    function lock(on, label) {{
      buttons.forEach(b => {{ b.disabled = on || b.dataset.blocked === 'true'; }});
      // The order button is not a data-act button - it posts to /order, not
      // /run - but one job at a time is one job at a time, so it locks too.
      if (send) send.disabled = on;
      if (creatorPreview) creatorPreview.disabled = on;
      if (creatorCreate) creatorCreate.disabled = on;
      busy.className = on ? 'running' : '';
      // The label is the button's own text, which already reads '...실행';
      // appending '실행 중' to it produced 'Codex 실행 실행 중'.
      busy.textContent = on ? (label || '작업') : '';
    }}

    const esc = t => String(t == null ? '' : t).replace(/[&<>"]/g,
      c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));

    // Mirrors _live_html in dashboard.py. Every phrase still comes from the
    // server's summariser - this only decides where it sits on the page.
    function showLive(data) {{
      const p = (data && data.progress) || null;
      if (!p || !p.phase) {{ live.innerHTML = ''; return; }}
      const tone = p.done ? (p.exit_code === 0 ? ' live--done' : ' live--failed') : '';
      const dots = p.done ? '' : ' <span class="live-dots" aria-hidden="true"></span>';
      // An order runs two steps, so say which one this is - otherwise the
      // page reads as if the whole order finished when only Codex did.
      const stage = (data.steps > 1)
        ? '[' + data.step + '/' + data.steps + '] ' : '';
      const what = stage + [data.title, p.action_label, data.arg]
        .filter(Boolean).join(' · ');
      const note = (p.slow && !p.done)
        ? '<div class="live-note">이 단계는 외부 프로그램이 끝날 때까지 출력이 나오지 않습니다. 멈춘 것이 아닙니다.</div>'
        : '';
      live.innerHTML =
        '<div class="live live--panel' + tone + '" aria-live="polite">' +
          '<div class="live-top"><span class="live-phase">' + esc(p.phase) + dots + '</span>' +
          (p.elapsed ? '<span class="live-elapsed">' + esc(p.elapsed) + ' 경과</span>' : '') +
          '</div>' +
          '<div class="live-what">' + esc(what) + '</div>' +
          (p.evidence ? '<div class="live-evidence mono">' + esc(p.evidence) + '</div>' : '') +
          note +
        '</div>';
    }}

    async function start(button) {{
      const action = button.dataset.act;
      const argId = button.dataset.arg;
      const hasDirectArg = button.dataset.argValue !== undefined;
      const arg = hasDirectArg ? button.dataset.argValue :
        (argId ? document.getElementById(argId).value : '');
      if ((argId || hasDirectArg) && !arg) {{ term.textContent = '선택할 항목이 없습니다.'; return; }}

      term.textContent = '';
      live.innerHTML = '';
      lock(true, button.textContent);
      try {{
        const res = await fetch('/run', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ token: TOKEN, action, arg }})
        }});
        const data = await res.json();
        if (!res.ok) {{ term.textContent = '거부됨: ' + (data.error || res.status); lock(false); return; }}
        setOfficeWorker(button.dataset.agentRole || actionAgent[action] || '');
        showLive(data);
        watch(data.job, button.textContent);
      }} catch (err) {{
        term.textContent = '서버에 연결할 수 없습니다: ' + err;
        lock(false);
      }}
    }}

    function watch(job, label) {{
      clearInterval(poll);
      poll = setInterval(async () => {{
        try {{
          const res = await fetch('/log?job=' + encodeURIComponent(job));
          const data = await res.json();
          showLive(data);
          term.textContent = data.output || '(출력 없음)';
          term.scrollTop = term.scrollHeight;
          if (data.done) {{
            clearInterval(poll);
            setOfficeWorker('');
            lock(false);
            term.textContent += '\\n\\n[종료 코드 ' + data.exit_code + ']' +
              (data.exit_code === 0 ? '' : ' - 실패했습니다. 위 출력을 그대로 Claude에게 주세요.');
            if (data.exit_code === 0 && data.action === 'build' && createdGame && creatorResult) {{
              creatorResult.classList.add('on');
              creatorResult.insertAdjacentHTML('beforeend',
                '<a class="creator-download" href="/artifact?game=' +
                encodeURIComponent(createdGame) + '">완성된 APK 받기</a>');
            }}
            // The board and the reports move as a result of these commands, so
            // a finished run makes the page above it stale.
            if (data.exit_code === 0 &&
                ['team-run','build','test','dashboard'].includes(data.action)) {{
              term.textContent += '\\n페이지를 새로 읽어옵니다...';
              setTimeout(() => location.reload(), 1400);
            }}
          }}
        }} catch (err) {{
          clearInterval(poll); setOfficeWorker(''); lock(false);
          term.textContent += '\\n로그를 읽지 못했습니다: ' + err;
        }}
      }}, 1000);
    }}

    buttons.forEach(b => b.addEventListener('click', () => start(b)));

    // ---- AI game creator ----
    function creatorBody() {{
      return {{
        token: TOKEN,
        idea: document.getElementById('creator-idea').value,
        title: document.getElementById('creator-title').value,
        style: document.getElementById('creator-style').value,
        difficulty: document.getElementById('creator-difficulty').value,
        theme: document.getElementById('creator-theme').value,
        pipeline: document.getElementById('creator-pipeline').value,
      }};
    }}

    function showPlan(plan, saved) {{
      if (!creatorResult || !plan) return;
      const spec = plan.spec || {{}};
      const player = spec.player || {{}};
      const level = spec.level || {{}};
      const theme = spec.theme || {{}};
      const tags = (plan.features || []).map(
        feature => '<span>' + esc(feature) + '</span>').join('');
      creatorResult.classList.add('on');
      creatorResult.innerHTML =
        '<h3>' + esc(plan.title) + ' <small class="mono">' + esc(plan.game_id) + '</small></h3>' +
        '<p>' + esc(plan.pitch) + '</p>' +
        '<p><b>' + esc(plan.style_label) + '</b> · ' + esc(level.difficulty) +
          ' · ' + esc(theme.environment) + ' · 속도 ' + esc(player.moveSpeed) + '</p>' +
        '<div class="creator-tags">' + tags + '</div>' +
        '<div class="creator-spec mono">' +
          (saved ? '저장됨 · GameSpecs/' + esc(plan.game_id) + '.json' :
                   '미리보기 · 아직 파일을 만들지 않았습니다') +
        '</div>';
    }}

    async function createGame(previewOnly) {{
      const body = creatorBody();
      if (!body.idea.trim()) {{
        creatorResult.classList.add('on');
        creatorResult.innerHTML = '<p>만들 게임의 아이디어를 입력하세요.</p>';
        document.getElementById('creator-idea').focus();
        return;
      }}
      term.textContent = '';
      lock(true, previewOnly ? 'AI 기획 중' : '게임 자동 생성');
      try {{
        const res = await fetch(previewOnly ? '/plan-game' : '/create-game', {{
          method: 'POST',
          headers: {{'Content-Type': 'application/json'}},
          body: JSON.stringify(body),
        }});
        const data = await res.json();
        if (!res.ok) {{
          creatorResult.classList.add('on');
          creatorResult.innerHTML = '<p>생성 실패: ' + esc(data.error || res.status) + '</p>';
          term.textContent = data.note || '';
          lock(false);
          return;
        }}
        showPlan(data.plan, Boolean(data.saved));
        if (previewOnly) {{
          term.textContent = 'AI 기획 미리보기가 준비되었습니다.';
          lock(false);
          return;
        }}
        createdGame = data.plan.game_id;
        term.textContent = 'GameSpecs/' + createdGame + '.json 저장 완료\\n' +
          ((data.steps || []).length ? '자동화: ' + data.steps.join(' → ') : '기획 저장만 완료');
        if (data.job) {{
          showLive(data);
          watch(data.job, '게임 자동 생성');
        }} else {{
          lock(false);
        }}
      }} catch (err) {{
        creatorResult.classList.add('on');
        creatorResult.innerHTML = '<p>서버에 연결할 수 없습니다: ' + esc(err) + '</p>';
        lock(false);
      }}
    }}

    if (creatorPreview) creatorPreview.addEventListener('click', () => createGame(true));
    if (creatorCreate) creatorCreate.addEventListener('click', () => createGame(false));

    // ---- the order box ----
    const dept = document.getElementById('order-dept');
    const scope = document.getElementById('order-scope');
    const text = document.getElementById('order-text');

    // Which files that team may touch, shown as the team is chosen. This is
    // the allowlist the run is checked against, so the user should see the
    // boundary BEFORE typing an instruction that falls outside it.
    function showScope() {{
      if (!dept || !scope) return;
      const picked = dept.options[dept.selectedIndex];
      const files = picked ? picked.dataset.files : '';
      const seat = picked ? picked.dataset.seat : '';
      scope.textContent = files ? (seat + ' · ' + files) : '';
    }}
    if (dept) {{ dept.addEventListener('change', showScope); showScope(); }}

    async function order() {{
      const body = {{
        token: TOKEN,
        department: dept ? dept.value : '',
        text: text ? text.value : '',
      }};
      const verifyBox = document.getElementById('order-verify');
      const gameBox = document.getElementById('order-game');
      // No checkbox on the page means there was no GameSpec to test, which
      // the section already says. Sending verify:false keeps the server from
      // having to guess what a missing field meant.
      body.verify = verifyBox ? verifyBox.checked : false;
      body.game = (body.verify && gameBox) ? gameBox.value : '';

      term.textContent = '';
      live.innerHTML = '';
      lock(true, '지시 처리');
      try {{
        const res = await fetch('/order', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify(body)
        }});
        const data = await res.json();
        if (!res.ok) {{
          term.textContent = '거부됨: ' + (data.error || res.status) +
            (data.note ? '\\n' + data.note : '');
          lock(false);
          return;
        }}
        let head = '접수: ' + data.order + ' → ' + data.department_label +
                   '\\n단계: ' + (data.steps || []).join(' → ');
        if (data.duplicate_of) {{
          head += '\\n같은 지시가 이미 ' + data.duplicate_of + ' 로 대기 중입니다.';
        }}
        term.textContent = head + '\\n\\n';
        // Cleared only once the order is accepted: a rejected order should
        // leave the text where the user can fix it instead of retyping it.
        if (text) text.value = '';
        showLive(data);
        watch(data.job, '지시 처리');
      }} catch (err) {{
        term.textContent = '서버에 연결할 수 없습니다: ' + err;
        lock(false);
      }}
    }}

    if (send) send.addEventListener('click', order);
    // Ctrl+Enter sends, because Enter has to stay a newline in a textarea -
    // an order is often two or three sentences.
    if (text) text.addEventListener('keydown', ev => {{
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter' && !send.disabled) order();
    }});
  }});
  </script>
"""


def render(snapshot: Snapshot, control_token: str | None = None,
           live_job: dict[str, Any] | None = None) -> str:
    """Draw the page. `live_job` is a running job's snapshot, or None.

    Only the served page ever gets one - the static file has no server behind
    it, so it renders the committed evidence alone rather than a fabricated
    running state.
    """
    # One guard rather than a check at every use: a page with no server behind
    # it has no live state to read, and the file written to Reports/ or
    # published as an Artifact must never carry a running state that was true
    # only at the moment it was written.
    if control_token is None:
        live_job = None

    counts = {state: sum(1 for a in snapshot.agents if a.state == state)
              for state in (READY, GATED, BLOCKED, UNKNOWN)}

    working_prefix = (progress_mod.agent_prefix_for(str(live_job.get("action", "")))
                      if live_job and not (live_job.get("progress") or {}).get("done")
                      else "")
    if working_prefix == "Unity":
        working_prefix = "Codex"
    office = (_company_office_html(
        snapshot.company_roles, snapshot.tasks, snapshot.office_image, live_job)
        if snapshot.company_roles
        else _virtual_office_html(snapshot.agents, snapshot.office_image, working_prefix))
    roster = "\n".join(_agent_row(agent) for agent in snapshot.agents)

    task_models = [Task.from_dict(task) for task in snapshot.tasks]
    task_board = TaskBoard(path=Path(), tasks=task_models)
    unmet_by_id = {task.id: task_board.unmet_dependencies(task)
                   for task in task_models if task.owner == "codex" and task.status != "done"}

    queue = _queue_html(snapshot.tasks, unmet_by_id,
                        served=control_token is not None, live_job=live_job)

    lanes = []
    for owner, label in (("claude", "Claude"), ("codex", "Codex")):
        owned = [t for t in snapshot.tasks if t.get("owner") == owner]
        rows = "\n".join(
            _task_row(t, served=control_token is not None,
                      unmet_dependencies=unmet_by_id.get(str(t.get("id", "")), []))
            for t in owned) or \
            '<div class="task"><span class="files">배정된 작업이 없습니다.</span></div>'
        lanes.append(f"""      <div class="lane">
        <h3>{e(label)}<span class="count">{len(owned)}개</span></h3>
{rows}
      </div>""")

    if snapshot.builds:
        build_rows = "\n".join(
            f'        <div class="kv"><span class="k">{e(b["game"] or b["file"])}</span>'
            f'<span class="v"><span class="big">{e(b["size"])}</span><br>'
            f'<span class="mono" style="font-size:11.5px;color:var(--muted)">'
            f'sha256:{e(b["sha"])}</span></span></div>'
            for b in snapshot.builds)
        build_note = (f'<div class="kv"><span class="k">빌드 시각</span>'
                      f'<span class="v mono">{e(snapshot.builds[0]["built"])}</span></div>')
    else:
        build_rows = ('        <div class="kv"><span class="k">검증된 APK</span>'
                      '<span class="v">없음</span></div>')
        build_note = ""

    def _error_cell(label: str, key: str) -> str:
        # A count that was never parsed is NOT zero. Defaulting it to 0 here
        # would print a clean bill of health for a section the report does not
        # actually contain.
        if key not in snapshot.errors:
            return (f'<div class="kv"><span class="k">{e(label)}</span>'
                    f'<span class="v" style="color:var(--unknown)">확인 불가</span></div>')
        count = snapshot.errors[key]
        colour = "var(--blocked)" if count else "var(--ok)"
        return (f'<div class="kv"><span class="k">{e(label)}</span>'
                f'<span class="v big" style="color:{colour}">{count}</span></div>')

    error_rows = "".join(_error_cell(label, key) for label, key in (
        ("컴파일 에러", "compile"),
        ("Obsolete API 경고", "obsolete"),
        ("런타임 예외", "runtime"),
    ))

    game_cells = "\n".join(
        f'      <div class="g g-{g["state"]}"><div class="n">{e(g["id"][4:])}</div>'
        f'<div class="s">{e({"done": "완료", "active": "진행 중", "todo": "미착수"}[g["state"]])}</div></div>'
        for g in snapshot.games)
    done_games = sum(1 for g in snapshot.games if g["state"] == "done")

    log = "\n".join(
        f'      <div><span class="sha mono">{e(c["sha"])}</span>'
        f'<span class="when mono">{e(c["date"])}</span>'
        f'<span class="what">{e(c["subject"])}</span></div>'
        for c in snapshot.commits) or '      <div><span class="what">git 기록을 읽지 못했습니다.</span></div>'

    # Plain ink, not the muted key colour: these ARE the content of the panel,
    # not labels for something else sitting beside them.
    gates = snapshot.policy.get("human_gates", [])
    gate_items = "".join(
        f'<div class="kv"><span class="v mono" style="text-align:left">{e(g)}</span></div>'
        for g in gates) or \
        '<div class="kv"><span class="k">정책 파일에 human_gates 가 없습니다.</span></div>'

    missing_block = ""
    if snapshot.missing:
        files = "".join(f"<code>{e(path)}</code> " for path in snapshot.missing)
        missing_block = (f'<div class="warn"><b>읽지 못한 파일이 있습니다.</b> {files}<br>'
                         "그만큼 이 페이지의 상태는 비어 있거나 '확인 불가'로 표시됩니다 - "
                         "빈 칸을 정상으로 바꿔 읽지 마세요.</div>")

    machine = snapshot.profile.get("machineName", "?")
    hardware = snapshot.profile.get("hardware", {})
    unity = snapshot.profile.get("unity", {})

    # Order box first, then the fixed-button panel. Both only exist behind a
    # server: the static copy has nothing to POST to, and section 10's rule
    # that a control which cannot act should not be drawn covers a text box
    # every bit as much as a button.
    control = (_studio_html(snapshot) + _order_html(snapshot)
               + _control_html(snapshot, control_token, live_job)
               if control_token else "")
    shot_count = sum(len(g["items"]) for g in snapshot.gallery)

    plan = snapshot.art_plan
    plan_note = (f'{len(plan.get("present", []))}개 반영 · '
                 f'{len(plan.get("missing", []))}개 미복사 · '
                 f'{len(plan.get("queued", []))}개 대기 · '
                 f'{e(plan.get("source") or "근거 파일 없음")}')
    # Said plainly rather than left to the reader: the static copy has no
    # server, so it has no buttons, and that difference should not look like
    # a missing feature.
    mode = ("제어 가능 · 이 PC의 로컬 서버" if control_token
            else "읽기 전용 · 제어는 PC에서 'orchestrator serve' 로 엽니다")

    return f"""<title>도리 AI 팀 미니홈피</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;700&family=Noto+Sans+KR:wght@400;500;700&family=JetBrains+Mono:wght@400;500&display=swap">
<style>{CSS}</style>

<div class="wrap" id="top">
  <header class="mast">
    <aside class="mini-owner" aria-label="미니홈피 주인">
      <span class="mini-today">TODAY {len(snapshot.tasks)} · TOTAL {shot_count}</span>
      <div class="mini-owner-avatar"><span aria-hidden="true">🦔</span></div>
      <b>도리 사장실</b>
      <span>AI GAME CLUB</span>
    </aside>
    <div class="mini-title">
      <h1>도리 AI 팀 미니홈피</h1>
      <div class="sub">도리의 AI 팀 미니홈피 · 오늘도 새 게임 만드는 중</div>
    </div>
    <div class="stamp mono">
      생성 {e(snapshot.generated_at)}<br>
      {e(machine)} · RAM {e(f"{hardware.get('ramTotalGb', 0):.1f}")} GB<br>
      Unity {e(unity.get('requiredByProject', '?'))} · {e(unity.get('status', '?'))}<br>
      {e(mode)}
    </div>
  </header>
  <nav class="mini-tabs" aria-label="미니홈피 메뉴">
    <a href="#top">HOME</a>
    <a href="#miniroom">MINIROOM</a>
    <a href="#studio">GAME</a>
    <a href="#profile">AI TEAM</a>
  </nav>

  <div class="verdict">
    <b>연동 도구 {counts[READY]}개 작업 가능</b>
    <span>{counts[GATED]}개 대기 · {counts[BLOCKED]}개 사용 불가 · {counts[UNKNOWN]}개 확인 불가</span>
    <span>설치된 것과 실제로 돌아가는 것은 다릅니다. 아래 각 줄은 그 판단의 근거 파일을 함께 표시합니다.</span>
  </div>
  {missing_block}
  <section id="miniroom">
    <div class="head">
      <h2>12부서 AI 팀 가상 사무실</h2>
      <span class="note">AGENTS.json + TASKBOARD 실제 상태 · 대기 중 순찰, 작업 중 책상 착석</span>
    </div>
    {office}
  </section>
{control}

  <section id="progress">
    <div class="head">
      <h2>진행 확인</h2>
      <span class="note">지금 돌고 있는 것과 다음 차례 · 실행 버튼은 여기에만 있습니다</span>
    </div>
    <div class="queue">
{queue}
    </div>
  </section>

  <details class="more">
    <summary>자세히 — 연동 상태 · 작업판 · 파이프라인 · 이미지 · 기록</summary>

  <section id="profile">
    <div class="head">
      <h2>연동된 AI</h2>
      <span class="note">근거 = 이 상태를 읽어온 파일</span>
    </div>
    <div class="roster">
{roster}
    </div>
  </section>

  <section>
    <div class="head">
      <h2>PC 연결</h2>
      <span class="note">이 두 파일이 PC에서 Claude로 오는 유일한 통로입니다 · 없으면 확인 불가</span>
    </div>
    <div class="grid2">
      {_link_panel_sync(snapshot.sync_status)}
      {_link_panel_run(snapshot.last_run)}
    </div>
  </section>

  <section>
    <div class="head">
      <h2>공유 작업판</h2>
      <span class="note">AI_GAME_COMPANY/config/TASKBOARD.json · 완료는 빌드 통과 후 사람이 정합니다</span>
    </div>
    <div class="board">
{chr(10).join(lanes)}
    </div>
  </section>

  <section>
    <div class="head">
      <h2>파이프라인</h2>
      <span class="note">디스크의 실제 파일만 셉니다</span>
    </div>
    <div class="grid2">
      <div class="panel">
        <h3>검증된 빌드</h3>
{build_rows}
        {build_note}
        <div class="kv"><span class="k">리포트 시각</span>
          <span class="v mono">{e(snapshot.build_report_at or '없음')}</span></div>
      </div>
      <div class="panel">
        <h3>Unity 에러</h3>
        {error_rows}
        <div class="kv"><span class="k">리포트 시각</span>
          <span class="v mono">{e(snapshot.error_report_at or '없음')}</span></div>
      </div>
    </div>
  </section>

  <section>
    <div class="head">
      <h2>이미지</h2>
      <span class="note">{shot_count}장 · 파일에 들어 있는 그대로</span>
    </div>
{_gallery_html(snapshot.gallery)}

    <div class="gal-wrap">
      <div class="h"><b>계획 대비 실제</b><span>{e(plan_note)}</span></div>
{_art_plan_html(snapshot.art_plan)}
    </div>
  </section>

  <section>
    <div class="head">
      <h2>10개 게임</h2>
      <span class="note">{done_games} / {len(snapshot.games)} 완료 · 리포트와 실제 APK가 둘 다 있어야 완료</span>
    </div>
    <div class="games">
{game_cells}
    </div>
  </section>

  <section>
    <div class="head"><h2>최근 작업</h2></div>
    <div class="log mono">
{log}
    </div>
  </section>

  <section>
    <div class="head">
      <h2>사람만 할 수 있는 일</h2>
      <span class="note">이 목록에 없는 것은 자동으로 진행합니다</span>
    </div>
    <div class="panel">
      {gate_items}
    </div>
  </section>
  </details>

  <footer>
    이 페이지는 <code>python -m company.orchestrator.main dashboard</code> 로 다시 생성합니다.
    네트워크나 도구를 실행하지 않고, 커밋된 파일과 정책이 지정한 Gemini 키의 존재 여부만
    읽습니다. 키 값은 저장하거나 표시하지 않습니다.<br>
    빈 칸은 "이상 없음"이 아니라 "확인된 근거가 없음"입니다.
  </footer>
</div>
"""


def standalone(page: str) -> str:
    """Wrap a rendered page as a complete HTML document.

    render() deliberately emits no doctype or <head>: the Artifact publisher
    supplies those. Everywhere else needs them, and the charset in particular
    is load-bearing - every label on this page is Korean, and a browser
    handed this file without a declared encoding falls back to Latin-1 and
    renders the whole thing as mojibake.
    """
    return ('<!doctype html>\n<html lang="ko">\n<head>\n'
            '<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
            '</head>\n<body>\n' + page + '\n</body>\n</html>\n')


def write(repo_root: Path, out_path: Path | None = None) -> Path:
    target = out_path or (repo_root / "Reports" / "dashboard.html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(standalone(render(collect(repo_root))), encoding="utf-8")
    return target
