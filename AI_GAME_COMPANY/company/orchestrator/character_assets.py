"""Turn one character drawing into the separate pieces a Unity rig needs.

The user's 도리 is a single opaque cut-out PNG: front-facing, arms and legs
painted into the silhouette. Nothing in it can move, which is why the runner
read as an image sliding sideways. A rig needs the pieces apart - a body with
no limbs, and each limb on its own with a joint to turn around.

Gemini does the drawing (policy allow_gemini_design, rule 2), because that is
image work and image work is not Claude's or Codex's to do here. This module
is only the plumbing: what to ask for, where to put it, and what Unity needs
to know about it afterwards.

The joint table is NOT generated. A model can redraw a paw; it cannot tell you
where the shoulder is in world units to two decimal places, and guessing costs
a build to find out. The numbers below were measured off player.png and live
in the manifest so a human can correct one without touching code.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from company.orchestrator.gemini_client import (
    GeminiClient,
    GeminiInputRejected,
    GeminiKeyMissing,
    GeminiLimited,
    GeminiModelNotAllowed,
    GeminiResponseError,
    GeminiUnavailable,
)
from company.orchestrator.policy import PolicyViolation

RIG_FOLDER = Path("Assets/Common/Art/Runner/rig")
MANIFEST_NAME = "rig.json"
IMAGE_MODEL = "gemini-2.5-flash-image"

STYLE_RULE = (
    "Match the reference exactly: same colours, same fur texture, same soft "
    "shading, same line quality. This is not a redesign - it is the same "
    "drawing taken apart. Output a single PNG with a fully transparent "
    "background, no shadow on the ground, no frame, no text, no watermark."
)


@dataclass(frozen=True)
class RigPart:
    """One piece of the character, and where it hinges.

    joint: where the piece turns, in fractions of the FULL character image
      (x from the left, y from the top). The shoulder for an arm; the hip for
      a leg, which sits up inside the belly so the foot swings on an arc
      instead of pivoting on its own ankle.
    anchor: where that same joint falls inside the piece's own image. Gemini is
      told to draw the joint at the top centre, so this is (0.5, 0.0) for a
      limb - but it stays a field, because the day a piece comes back framed
      differently, this is the one number that fixes it.
    """

    name: str
    joint: tuple[float, float]
    anchor: tuple[float, float]
    prompt: str


# Measured from Assets/Common/Art/Runner/player.png (136x192). The paws sit at
# 62-78% of the height and the feet below 87%; the hips are placed at 79.5%,
# inside the belly, so a leg swings rather than flaps.
PARTS: tuple[RigPart, ...] = (
    RigPart(
        name="body",
        joint=(0.5, 0.5),
        anchor=(0.5, 0.5),
        prompt=(
            "Redraw this character with BOTH front paws and BOTH feet removed. "
            "Keep the head, ears, spines, face, bow tie and belly exactly as "
            "they are. Where a paw or a foot was, continue the belly fur so "
            "the body looks whole and has a smooth, rounded bottom edge - no "
            "holes, no stumps, no cut lines. Same canvas size and the body in "
            "the same position as the reference. " + STYLE_RULE
        ),
    ),
    RigPart(
        name="arm_l",
        joint=(0.197, 0.612),
        anchor=(0.5, 0.0),
        prompt=(
            "Draw ONLY this character's front paw on its LEFT side of the "
            "image, by itself, nothing else in the frame. Keep its exact "
            "shape, colour and shading. Point the paw straight down with the "
            "shoulder end at the TOP CENTRE of the canvas, so it can be "
            "rotated around that point. " + STYLE_RULE
        ),
    ),
    RigPart(
        name="arm_r",
        joint=(0.797, 0.612),
        anchor=(0.5, 0.0),
        prompt=(
            "Draw ONLY this character's front paw on its RIGHT side of the "
            "image, by itself, nothing else in the frame. Keep its exact "
            "shape, colour and shading. Point the paw straight down with the "
            "shoulder end at the TOP CENTRE of the canvas, so it can be "
            "rotated around that point. " + STYLE_RULE
        ),
    ),
    RigPart(
        name="foot_l",
        joint=(0.264, 0.795),
        anchor=(0.5, 0.0),
        prompt=(
            "Draw ONLY this character's foot on its LEFT side of the image, "
            "by itself, nothing else in the frame - no leg, no belly. Keep its "
            "exact shape, colour and shading, seen from the front. Put the "
            "ankle end at the TOP CENTRE of the canvas. " + STYLE_RULE
        ),
    ),
    RigPart(
        name="foot_r",
        joint=(0.722, 0.795),
        anchor=(0.5, 0.0),
        prompt=(
            "Draw ONLY this character's foot on its RIGHT side of the image, "
            "by itself, nothing else in the frame - no leg, no belly. Keep its "
            "exact shape, colour and shading, seen from the front. Put the "
            "ankle end at the TOP CENTRE of the canvas. " + STYLE_RULE
        ),
    ),
)


SIDE_VIEW_PATH = Path("Assets/Common/Art/Runner/player_side.png")

SIDE_VIEW_PROMPT = (
    "Redraw this exact character in a SIDE VIEW, in profile, facing RIGHT. "
    "Same character: same colours, same fur and spines, same face, same bow "
    "tie, same proportions. Only the camera angle changes - this is the same "
    "hedgehog seen from its left side, not a new design and not a three-quarter "
    "turn. One eye visible. Standing upright on both feet, arms at its sides, "
    "the whole body in frame from the tips of the spines to below the feet. "
    "Neutral standing pose, not running - the run is animated afterwards. "
    + STYLE_RULE
)


class CharacterAssetError(RuntimeError):
    """The rig could not be produced, with a reason worth printing."""


def generate_side_view(client: GeminiClient, repo_root: Path, reference: Path,
                       out_path: Path | None = None) -> Path:
    """Redraws the character in profile and writes it next to the front art.

    WHY THIS IS A SEPARATE STEP. The game scrolls sideways and the character
    is drawn facing the camera, so it can only ever bounce on the spot - a
    front view has no stride to animate. No amount of image processing turns
    one into the other; it is a redraw, and a redraw is Gemini's job under
    rule 2.

    It deliberately produces the WHOLE character, not the cut-up parts. Where
    a shoulder and a hip land is measured off the finished drawing and written
    into the slicer's table; asking a model to place a joint to two decimal
    places and trusting the answer is how a limb ends up hinged through the
    middle of a belly.
    """
    if not reference.is_file():
        raise CharacterAssetError(f"Reference image not found: {reference}")

    target = out_path or (repo_root / SIDE_VIEW_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)

    try:
        png = client.generate_image(IMAGE_MODEL, SIDE_VIEW_PROMPT,
                                    images=[reference.read_bytes()])
    except GeminiLimited as exc:
        raise CharacterAssetError(f"{exc} Nothing was written.") from None
    except GeminiKeyMissing as exc:
        raise CharacterAssetError(str(exc)) from None
    except (GeminiModelNotAllowed, PolicyViolation) as exc:
        raise CharacterAssetError(str(exc)) from None
    except (GeminiInputRejected, GeminiResponseError, GeminiUnavailable) as exc:
        raise CharacterAssetError(f"side view: {exc}") from None

    target.write_bytes(png)
    return target


def manifest_for(source: str, parts: "list[str]") -> dict:
    """What Unity reads. Written next to the images, not into the code."""
    return {
        "_comment": (
            "Character rig for the Runner genre. 'source' says who drew these: "
            "gemini for the real path, local-slicer for the fallback cut out of "
            "player.png. Unity's CharacterRigGenerator reads joint/anchor from "
            "here - correct a number here, regenerate, do not edit the C#."
        ),
        "source": source,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "reference": "Assets/Common/Art/Runner/player.png",
        "parts": [
            {
                "name": part.name,
                "joint": {"x": part.joint[0], "y": part.joint[1]},
                "anchor": {"x": part.anchor[0], "y": part.anchor[1]},
            }
            for part in PARTS if part.name in parts
        ],
    }


def generate(client: GeminiClient, repo_root: Path, reference: Path,
             out_folder: Path | None = None) -> list[str]:
    """Asks Gemini for each piece and writes it. Returns the names written.

    One request per part rather than one sheet: a sheet has to be cut up
    afterwards by guessing at its layout, and a wrong guess is indistinguishable
    from a bad generation. Separate files fail visibly instead.
    """
    if not reference.is_file():
        raise CharacterAssetError(f"Reference image not found: {reference}")

    data = reference.read_bytes()
    folder = out_folder or (repo_root / RIG_FOLDER)
    folder.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for part in PARTS:
        try:
            png = client.generate_image(IMAGE_MODEL, part.prompt, images=[data])
        except GeminiLimited as exc:
            # Free tier is spent. Policy on_codex_limit applies here too:
            # degrade, never promote to a billed tier or another project.
            raise CharacterAssetError(
                f"{exc} Stopped after {len(written)} of {len(PARTS)} parts; "
                "the ones already written are still on disk."
            ) from None
        except GeminiKeyMissing as exc:
            raise CharacterAssetError(str(exc)) from None
        except (GeminiModelNotAllowed, PolicyViolation) as exc:
            raise CharacterAssetError(str(exc)) from None
        except (GeminiInputRejected, GeminiResponseError, GeminiUnavailable) as exc:
            raise CharacterAssetError(f"{part.name}: {exc}") from None

        (folder / f"{part.name}.png").write_bytes(png)
        written.append(part.name)

    (folder / MANIFEST_NAME).write_text(
        json.dumps(manifest_for("gemini", written), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return written
