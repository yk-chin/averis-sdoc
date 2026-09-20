"""
Vision extraction for image-only PDFs
=====================================
The deterministic parser reads text. A scanned PDF has none, so until now it was escalated as `unreadable`
and a person opened the file blind. This module asks a vision-capable Gemini model to *propose* the seven
fields from the page images. Two rules keep it honest:

  1. A proposal is never a verdict. Its confidence is capped at VISION_MAX_CONF (0.60), below the comparator's
     review threshold (0.62), so anything built on it must go to a human. The decision stays
     NEEDS_REVIEW / unreadable; what changes is that the reviewer now sees "the scan seems to say X, Y, Z -
     please confirm" instead of nothing. LLM proposes, the deterministic core disposes.
  2. Only genuine scans are sent: a file that is not a valid PDF (corrupt bytes) is reported as such and
     never uploaded anywhere.

Cache: .cache/vision.json keyed by the file's sha256 + prompt version. Shares the client, model chain,
pacing and cooldowns with pipeline/classify_llm.py. VISION=off disables it (tests, or a quota emergency).
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import threading
import time
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from shipdoc_core.fields import FIELDS
from pipeline import classify_llm as llm

PROMPT_VERSION = "vision-v1"
VISION_MAX_CONF = 0.60           # strictly below compare.review_threshold (0.62): a proposal cannot auto-pass
CACHE_PATH = pathlib.Path(__file__).resolve().parents[1] / ".cache" / "vision.json"
_lock = threading.Lock()
_cache: Optional[dict] = None
STATS = {"calls": 0, "cache_hits": 0, "failures": 0, "skipped_not_pdf": 0}


class VisionField(BaseModel):
    key: str = Field(description="one of the field keys listed in the prompt")
    value: Optional[str] = Field(None, description="the value exactly as printed, or null if absent / illegible")
    confidence: float = Field(ge=0.0, le=1.0, description="how legible and unambiguous the value is on the scan")


class VisionExtraction(BaseModel):
    document_type: str = Field(description="SI | BL | INVOICE | PACKING | COO | UNKNOWN")
    fields: list[VisionField]


def _system_prompt() -> str:
    lines = [f"- {f.key}: {f.label} (also labelled {', '.join(f.aliases[:4])})" for f in FIELDS]
    return ("You read scanned shipping documents (Shipping Instruction, Bill of Lading). Extract only the fields "
            "below, verbatim as printed - do not normalise, translate or guess. If a field is absent or illegible "
            "return null with a low confidence. Field keys:\n" + "\n".join(lines) +
            "\nDo not confuse Place of Receipt with Port of Loading, Place of Delivery with Port of Discharge, "
            "or net weight with gross weight.")


def is_scanned_pdf(path: pathlib.Path) -> bool:
    """A valid PDF whose pages carry images but no extractable text. Corrupt files return False."""
    try:
        import pdfplumber
        with pdfplumber.open(str(path)) as pdf:
            if not pdf.pages:
                return False
            text = "".join((p.extract_text() or "") for p in pdf.pages[:3]).strip()
            has_image = any(p.images for p in pdf.pages[:3])
            return has_image and len(text) < 40
    except Exception:                                   # noqa: BLE001 - pdfminer raises many types on garbage
        return False


def _cache_load() -> dict:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:                               # noqa: BLE001
            _cache = {}
    return _cache


def _cache_save() -> None:
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(_cache, indent=1), encoding="utf-8")
    except Exception:                                   # noqa: BLE001
        pass


def _extract_remote(pdf_bytes: bytes) -> tuple[Optional[VisionExtraction], Optional[str]]:
    """One vision call through the shared model chain. Returns (result, model) or (None, None). Never raises."""
    client = llm._get_client()
    if client is None:
        return None, None
    from google.genai import types
    part = types.Part.from_bytes(data=pdf_bytes, mime_type="application/pdf")
    config = types.GenerateContentConfig(system_instruction=_system_prompt(), response_mime_type="application/json",
                                         response_schema=VisionExtraction, temperature=0.0, max_output_tokens=1024)
    last = None
    for model in llm._available_models():
        for attempt in (1, 2):
            try:
                llm._pace()
                STATS["calls"] += 1
                resp = client.models.generate_content(
                    model=model, contents=[part, "Extract the fields from this scanned document."], config=config)
                parsed = resp.parsed
                if parsed is None:
                    parsed = VisionExtraction.model_validate_json(resp.text or "")
                elif not isinstance(parsed, VisionExtraction):
                    parsed = VisionExtraction.model_validate(parsed)
                return parsed, model
            except (ValidationError, Exception) as e:   # noqa: BLE001
                last = e
                kind = llm._err_kind(e)
                if kind == "gone":
                    llm._cooldown_until[model] = float("inf"); break
                if attempt == 2:
                    llm._cooldown_until[model] = time.time() + llm.COOLDOWN_S
                else:
                    time.sleep(6.0 if kind == "quota" else 1.0)
    STATS["failures"] += 1
    if STATS["failures"] <= 3:
        print(f"  [vision] extraction failed: {type(last).__name__ if last else '-'}: {str(last)[:160] if last else 'no model'}",
              file=sys.stderr)
    return None, None


def extract_fields(path: pathlib.Path) -> dict:
    """Vision proposal for one attachment. Always returns a dict:
       {"status": "proposal", "fields": {key: value}, "confidence": {key: c <= 0.60}, "document_type", "model"}
       {"status": "not_a_pdf" | "disabled" | "failed", "reason": "..."}"""
    if os.getenv("VISION", "on").strip().lower() in ("off", "0", "false"):
        return {"status": "disabled", "reason": "VISION=off"}
    path = pathlib.Path(path)
    if path.suffix.lower() != ".pdf" or not is_scanned_pdf(path):
        STATS["skipped_not_pdf"] += 1
        return {"status": "not_a_pdf", "reason": "file is not a readable PDF (corrupt or not a scan): nothing to send"}
    data = path.read_bytes()
    key = hashlib.sha256(PROMPT_VERSION.encode() + data).hexdigest()
    with _lock:
        cache = _cache_load()
        if key in cache:
            STATS["cache_hits"] += 1
            return cache[key]
    parsed, model = _extract_remote(data)
    if parsed is None:
        return {"status": "failed", "reason": "vision model unavailable (quota, network or model chain exhausted)"}
    known = {f.key for f in FIELDS}
    fields = {vf.key: (vf.value.strip() if vf.value else None) for vf in parsed.fields if vf.key in known}
    conf = {vf.key: round(min(vf.confidence, VISION_MAX_CONF), 3) for vf in parsed.fields if vf.key in known}
    result = {"status": "proposal", "document_type": parsed.document_type, "fields": fields, "confidence": conf,
              "model": model, "max_confidence": VISION_MAX_CONF}
    with _lock:
        _cache_load()[key] = result
        _cache_save()
    return result
