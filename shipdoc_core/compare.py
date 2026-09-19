"""
Deterministic Comparator
========================
The SI is the reference (use case: "the SI is the reference for this check").

Iron rules:
  1. No LLM, no network in this file. Pure functions. Same input, same output.
  2. Every verdict carries evidence (raw value + normalised value + reason) and is auditable.
  3. Unsure -> UNDETERMINED -> a human. Never guess.
     Use case: "escalate to a person with the relevant context,
     rather than guessing or failing silently."
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
from enum import Enum

from .fields import FIELDS, FIELD_BY_KEY, FieldKind, Severity
from .normalize import (
    normalize_party, normalize_port, normalize_count,
    normalize_weight_kg, ports_match, basic_clean,
)


class Outcome(str, Enum):
    EXACT = "exact"                    # identical as written
    NORMALIZED_MATCH = "normalized"    # formatting difference only - never reported as a mismatch
    MISMATCH = "mismatch"              # real difference
    MISSING_SI = "missing_si"
    MISSING_BL = "missing_bl"
    UNDETERMINED = "undetermined"      # cannot decide reliably -> human


# Weight tolerance: 0.1 %, at least 0.5 kg. Absorbs unit-conversion and rounding error
# without hiding real differences (3 vs 4 containers, 22000 vs 23000 kg are far beyond it).
WEIGHT_REL_TOL = 0.001
WEIGHT_ABS_TOL = 0.5

# Company-name fuzzy grey zone: below LOW -> different, above HIGH -> same, in between -> human.
PARTY_FUZZY_HIGH = 0.94
PARTY_FUZZY_LOW = 0.75


@dataclass
class FieldResult:
    field: str
    label: str
    outcome: Outcome
    severity: str
    si_raw: str | None
    bl_raw: str | None
    si_norm: str | None
    bl_norm: str | None
    reason: str
    confidence: float          # confidence of this field verdict, 0-1
    needs_review: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        d["outcome"] = self.outcome.value
        return d


def _fuzzy(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _present(v) -> bool:
    return v is not None and basic_clean(str(v)) != ""


def compare_field(
    field_key: str,
    si_raw,
    bl_raw,
    *,
    si_conf: float = 1.0,
    bl_conf: float = 1.0,
) -> FieldResult:
    """Compare one field. si_conf / bl_conf are the extraction-stage (LLM/OCR) confidences."""
    spec = FIELD_BY_KEY[field_key]
    extract_conf = min(si_conf, bl_conf)

    def mk(outcome: Outcome, si_n, bl_n, reason: str, conf: float) -> FieldResult:
        final_conf = round(min(conf, extract_conf), 4)
        return FieldResult(
            field=spec.key, label=spec.label, outcome=outcome,
            severity=spec.severity.value,
            si_raw=None if si_raw is None else str(si_raw),
            bl_raw=None if bl_raw is None else str(bl_raw),
            si_norm=None if si_n is None else str(si_n),
            bl_norm=None if bl_n is None else str(bl_n),
            reason=reason, confidence=final_conf,
            needs_review=outcome in (Outcome.UNDETERMINED, Outcome.MISSING_SI,
                                     Outcome.MISSING_BL),
        )

    if not _present(si_raw):
        return mk(Outcome.MISSING_SI, None, None, "field missing on SI", 1.0)
    if not _present(bl_raw):
        return mk(Outcome.MISSING_BL, None, None, "field missing on BL", 1.0)

    if basic_clean(str(si_raw)) == basic_clean(str(bl_raw)):
        return mk(Outcome.EXACT, str(si_raw), str(bl_raw), "identical text", 1.0)

    # ---------------- PARTY ----------------
    if spec.kind is FieldKind.PARTY:
        a, b = normalize_party(si_raw), normalize_party(bl_raw)
        if not a or not b:
            return mk(Outcome.UNDETERMINED, a, b, "empty after normalisation, cannot compare", 0.3)
        if a == b:
            return mk(Outcome.NORMALIZED_MATCH, a, b, "same party, different spelling", 0.98)
        r = _fuzzy(a, b)
        if r >= PARTY_FUZZY_HIGH:
            return mk(Outcome.NORMALIZED_MATCH, a, b,
                      f"highly similar ({r:.2f}), treated as the same party", round(r, 3))
        if r <= PARTY_FUZZY_LOW:
            return mk(Outcome.MISMATCH, a, b,
                      f"different party (similarity {r:.2f})", round(1 - r, 3))
        return mk(Outcome.UNDETERMINED, a, b,
                  f"similarity {r:.2f} is in the grey zone, needs a human", round(r, 3))

    # ---------------- PORT ----------------
    if spec.kind is FieldKind.PORT:
        pa, pb = normalize_port(si_raw), normalize_port(bl_raw)
        same, why = ports_match(pa, pb)
        # display: name first, code in brackets — a bare code hides *why* two ports differ
        da = f"{pa.name} ({pa.unlocode})" if pa.name and pa.unlocode else pa.key()
        db = f"{pb.name} ({pb.unlocode})" if pb.name and pb.unlocode else pb.key()
        if same is None:
            return mk(Outcome.UNDETERMINED, da, db, why, 0.3)
        if same:
            return mk(Outcome.NORMALIZED_MATCH, da, db, f"same port ({why})", 0.97)
        return mk(Outcome.MISMATCH, da, db, f"different port ({why})", 0.95)

    # ---------------- COUNT ----------------
    if spec.kind is FieldKind.COUNT:
        a, b = normalize_count(si_raw), normalize_count(bl_raw)
        if a is None or b is None:
            return mk(Outcome.UNDETERMINED, a, b, "count could not be parsed", 0.2)
        if a == b:
            return mk(Outcome.NORMALIZED_MATCH, a, b, "same count, different notation", 0.99)
        return mk(Outcome.MISMATCH, a, b, f"container count differs: SI {a} / BL {b}", 0.99)

    # ---------------- WEIGHT ----------------
    a, b = normalize_weight_kg(si_raw), normalize_weight_kg(bl_raw)
    if a is None or b is None:
        return mk(Outcome.UNDETERMINED, a, b, "weight could not be parsed", 0.2)
    tol = max(WEIGHT_ABS_TOL, abs(a) * WEIGHT_REL_TOL)
    if abs(a - b) <= tol:
        return mk(Outcome.NORMALIZED_MATCH, a, b,
                  f"same weight (diff {abs(a-b):.2f} kg within tolerance {tol:.2f} kg)", 0.98)
    return mk(Outcome.MISMATCH, a, b,
              f"gross weight differs: SI {a:g} kg / BL {b:g} kg (diff {abs(a-b):g} kg)", 0.99)


@dataclass
class ComparisonReport:
    email_id: str
    has_mismatch: bool
    mismatched_fields: list[str]
    needs_human_review: bool
    review_reasons: list[str]
    min_confidence: float
    results: list[FieldResult]

    def to_dict(self) -> dict:
        return {
            "email_id": self.email_id,
            "has_mismatch": self.has_mismatch,
            "mismatched_fields": self.mismatched_fields,
            "needs_human_review": self.needs_human_review,
            "review_reasons": self.review_reasons,
            "min_confidence": self.min_confidence,
            "fields": [r.to_dict() for r in self.results],
        }

    def render(self) -> str:
        """Human-readable difference report - used as-is in the demo video and the review UI."""
        lines = [f"Email: {self.email_id}"]
        if not self.has_mismatch and not self.needs_human_review:
            lines.append("No mismatch detected.")     # exact wording required by the use case
        for r in self.results:
            if r.outcome is Outcome.MISMATCH:
                lines.append(
                    f"  [MISMATCH] {r.label}: SI: {r.si_norm} / BL: {r.bl_norm}  — {r.reason}"
                )
            elif r.needs_review:
                lines.append(f"  [REVIEW]   {r.label}: {r.reason}")
        if self.needs_human_review:
            lines.append(f"  -> escalated for human review: {'; '.join(self.review_reasons)}")
        return "\n".join(lines)


def compare_documents(
    email_id: str,
    si: dict,
    bl: dict,
    *,
    si_conf: dict | None = None,
    bl_conf: dict | None = None,
    review_threshold: float = 0.62,
) -> ComparisonReport:
    """
    Compare the SI and BL attached to one email.

    si / bl: {field_key: value} - produced by the extraction layer (LLM/OCR) and schema-validated
    review_threshold: below this confidence the case goes to a human. The default comes from the
                      cost-sensitive threshold sweep (see evaluate.optimal_threshold), not a guess.
    """
    si_conf, bl_conf = si_conf or {}, bl_conf or {}
    results = [
        compare_field(
            spec.key, si.get(spec.key), bl.get(spec.key),
            si_conf=si_conf.get(spec.key, 1.0),
            bl_conf=bl_conf.get(spec.key, 1.0),
        )
        for spec in FIELDS
    ]

    mismatched = [r.field for r in results if r.outcome is Outcome.MISMATCH]
    reasons: list[str] = []
    for r in results:
        if r.needs_review:
            reasons.append(f"{r.label}: {r.reason}")
        elif r.confidence < review_threshold:
            reasons.append(f"{r.label}: confidence {r.confidence:.2f} below threshold {review_threshold}")
            r.needs_review = True

    return ComparisonReport(
        email_id=email_id,
        has_mismatch=bool(mismatched),
        mismatched_fields=mismatched,
        needs_human_review=bool(reasons),
        review_reasons=reasons,
        min_confidence=round(min((r.confidence for r in results), default=1.0), 4),
        results=results,
    )
