"""Claude-backed correction *suggestions* for a page.

Design constraint, and the whole point of this module: the model never produces the
document. It proposes small, span-anchored edits that a human accepts or rejects in
the review UI. Raw OCR is never overwritten.

Two things keep it honest:
  1. The page image goes with the text. A model that can't see the scan can only guess
     fluently at a damaged matra; one that can is actually reading the page.
  2. Every suggestion's `original` must occur verbatim in the OCR text, or we drop it
     (see `_anchored`). A model that invents a span to "fix" gets filtered, not trusted.
"""

import base64
import io
import os

import anthropic
import cv2
import numpy as np
from PIL import Image
from pydantic import BaseModel, Field

MODEL = os.environ.get("PUSTAK_CLAUDE_MODEL", "claude-opus-4-8")
EFFORT = os.environ.get("PUSTAK_CLAUDE_EFFORT", "high")

# Opus 4.8 accepts up to 2576px on the long edge; larger is downscaled server-side
# anyway, so cap here and save the upload.
MAX_EDGE = 2576

SYSTEM = """\
You are assisting a human editor who is proof-reading OCR output from a scanned
Devanagari (Hindi) book, page by page. The scanned page image and the raw OCR text
for that same page are both given to you.

Your job is NOT to rewrite the text. It is to point at the specific places where the
OCR text disagrees with what is actually printed on the page image, so the human can
look at those spots first.

Rules:
- Read the page image. Every suggestion must be justified by what is visibly printed
  there, not by what reads well or what you would expect the sentence to say.
- Only report a span where you can see the OCR is wrong. Typical real cases: a dropped
  or wrong matra, a broken conjunct, a missing shirorekha joining, nukta present or
  absent, digits misread, two words run together or one word split.
- `original` MUST be copied character-for-character from the OCR text, exactly as it
  appears there. It is used to locate the span. Keep it short - just the affected word
  or two, not the sentence.
- If you cannot read the printed text clearly enough to be sure, either omit the span
  or mark it confidence "low" and say what is unclear. Do not guess a plausible word.
- Do not fix spelling, grammar, or style that the page itself contains. If the book
  prints it that way, it is correct.
- Do not translate, summarise, modernise, or "improve" anything.
- An empty list is a perfectly good answer for a clean page. Do not invent work.
"""


class Suggestion(BaseModel):
    original: str = Field(description="Exact span copied from the OCR text, verbatim.")
    corrected: str = Field(description="What is actually printed on the page image.")
    reason: str = Field(description="What is visibly different, in one short phrase.")
    confidence: str = Field(description="One of: high, medium, low.")


class PageSuggestions(BaseModel):
    suggestions: list[Suggestion]


class AIUnavailable(RuntimeError):
    pass


def _client() -> anthropic.Anthropic:
    # Zero-arg constructor resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an
    # `ant auth login` profile — don't hardcode or demand a specific one.
    try:
        return anthropic.Anthropic()
    except Exception as exc:
        raise AIUnavailable(f"could not construct Anthropic client: {exc}") from exc


def _encode(image_path: str) -> tuple[str, str]:
    image = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise AIUnavailable(f"page image unreadable: {image_path}")

    height, width = image.shape[:2]
    scale = MAX_EDGE / max(height, width)
    if scale < 1.0:
        image = cv2.resize(
            image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
        )

    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, format="PNG", optimize=True)
    return base64.standard_b64encode(buffer.getvalue()).decode("ascii"), "image/png"


def _anchored(suggestions: list[Suggestion], text: str) -> list[dict]:
    """Keep only suggestions whose `original` really occurs in the OCR text.

    This is the guard that makes the whole feature safe to ship. A span the model
    invented cannot be located, so it cannot be applied — and a model confabulating
    a correction is exactly the failure mode that would otherwise put fluent,
    plausible, wrong Hindi into the book without anyone noticing.
    """
    kept = []
    for s in suggestions:
        if not s.original or s.original == s.corrected:
            continue
        count = text.count(s.original)
        if count == 0:
            continue  # not on this page — discard rather than guess where it goes
        kept.append(
            {
                "original": s.original,
                "corrected": s.corrected,
                "reason": s.reason,
                "confidence": s.confidence if s.confidence in {"high", "medium", "low"} else "low",
                "occurrences": count,
            }
        )
    return kept


def suggest(image_path: str, ocr_text: str) -> dict:
    """Ask Claude which spans of `ocr_text` disagree with the page image."""
    if not ocr_text.strip():
        return {"suggestions": [], "dropped": 0, "model": MODEL}

    data, media_type = _encode(image_path)
    client = _client()

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=8000,
            system=SYSTEM,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            output_format=PageSuggestions,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": data,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Scanned page above. Raw OCR text for the same page, "
                                "between the markers:\n\n"
                                f"<ocr>\n{ocr_text}\n</ocr>\n\n"
                                "List only the spans where the OCR disagrees with the "
                                "printed page."
                            ),
                        },
                    ],
                }
            ],
        )
    except anthropic.AuthenticationError as exc:
        raise AIUnavailable(
            "Claude rejected the credentials. Check ANTHROPIC_API_KEY, or re-run `ant auth login`."
        ) from exc
    except anthropic.RateLimitError as exc:
        raise AIUnavailable("Rate limited by the Claude API — wait a moment and retry.") from exc
    except anthropic.APIStatusError as exc:
        raise AIUnavailable(f"Claude API error {exc.status_code}: {exc.message}") from exc
    except anthropic.APIConnectionError as exc:
        raise AIUnavailable(f"Could not reach the Claude API: {exc}") from exc
    except TypeError as exc:
        # With no credentials at all the SDK raises TypeError while building the
        # request — not AuthenticationError, and not at client construction. This is
        # the first thing a user without a key hits, so it must not surface as a 500.
        if "authentication" in str(exc).lower():
            raise AIUnavailable(
                "No Claude credentials found. Set ANTHROPIC_API_KEY in your environment "
                "(or run `ant auth login`), then restart the server."
            ) from exc
        raise

    if response.stop_reason == "refusal":
        raise AIUnavailable("request was declined by the model's safety system")

    parsed = response.parsed_output
    kept = _anchored(parsed.suggestions, ocr_text)
    return {
        "suggestions": kept,
        "dropped": len(parsed.suggestions) - len(kept),
        "model": response.model,
        "usage": {
            "input": response.usage.input_tokens,
            "output": response.usage.output_tokens,
        },
    }
