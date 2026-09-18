"""
确定性比对器 (Deterministic Comparator)
======================================
SI 是基准（用例原文：the SI is the reference for this check）。

设计铁律：
  1. 本文件零 LLM、零网络。纯函数。同样输入永远同样输出。
  2. 每个判定都带 evidence（原始值 + 规范化值 + 理由），可审计。
  3. 拿不准 → UNDETERMINED → 走人工，绝不猜。
     用例原文："escalate to a person with the relevant context,
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
    EXACT = "exact"                    # 原文就一样
    NORMALIZED_MATCH = "normalized"    # 仅格式差异 —— 绝不报为 mismatch
    MISMATCH = "mismatch"              # 真实差异
    MISSING_SI = "missing_si"
    MISSING_BL = "missing_bl"
    UNDETERMINED = "undetermined"      # 无法可靠判定 → 人工


# 重量容差：千分之一，且至少 0.5kg。用于吸收单位换算与四舍五入误差，
# 而不是掩盖真实差异（3 vs 4 个柜、22000 vs 23000 kg 都远超容差）。
WEIGHT_REL_TOL = 0.001
WEIGHT_ABS_TOL = 0.5

# 公司名模糊相似度灰区：低于 LOW 判不同，高于 HIGH 判相同，中间交人工。
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
    confidence: float          # 本字段判定的可信度 0-1
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
    """比对单个字段。si_conf/bl_conf 是抽取阶段（LLM/OCR）给出的置信度。"""
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
        return mk(Outcome.MISSING_SI, None, None, "SI 缺少该字段", 1.0)
    if not _present(bl_raw):
        return mk(Outcome.MISSING_BL, None, None, "BL 缺少该字段", 1.0)

    if basic_clean(str(si_raw)) == basic_clean(str(bl_raw)):
        return mk(Outcome.EXACT, str(si_raw), str(bl_raw), "原文一致", 1.0)

    # ---------------- PARTY ----------------
    if spec.kind is FieldKind.PARTY:
        a, b = normalize_party(si_raw), normalize_party(bl_raw)
        if not a or not b:
            return mk(Outcome.UNDETERMINED, a, b, "规范化后为空，无法比对", 0.3)
        if a == b:
            return mk(Outcome.NORMALIZED_MATCH, a, b, "仅公司名写法差异", 0.98)
        r = _fuzzy(a, b)
        if r >= PARTY_FUZZY_HIGH:
            return mk(Outcome.NORMALIZED_MATCH, a, b,
                      f"高度相似({r:.2f})，判为同一主体", round(r, 3))
        if r <= PARTY_FUZZY_LOW:
            return mk(Outcome.MISMATCH, a, b,
                      f"主体名不同(相似度 {r:.2f})", round(1 - r, 3))
        return mk(Outcome.UNDETERMINED, a, b,
                  f"相似度 {r:.2f} 落在灰区，需人工确认", round(r, 3))

    # ---------------- PORT ----------------
    if spec.kind is FieldKind.PORT:
        pa, pb = normalize_port(si_raw), normalize_port(bl_raw)
        same, why = ports_match(pa, pb)
        if same is None:
            return mk(Outcome.UNDETERMINED, pa.key(), pb.key(), why, 0.3)
        if same:
            return mk(Outcome.NORMALIZED_MATCH, pa.key(), pb.key(),
                      f"同一港口（{why}）", 0.97)
        return mk(Outcome.MISMATCH, pa.key(), pb.key(), f"港口不同（{why}）", 0.95)

    # ---------------- COUNT ----------------
    if spec.kind is FieldKind.COUNT:
        a, b = normalize_count(si_raw), normalize_count(bl_raw)
        if a is None or b is None:
            return mk(Outcome.UNDETERMINED, a, b, "数量无法解析", 0.2)
        if a == b:
            return mk(Outcome.NORMALIZED_MATCH, a, b, "数量一致（写法不同）", 0.99)
        return mk(Outcome.MISMATCH, a, b, f"集装箱数量不同：SI {a} / BL {b}", 0.99)

    # ---------------- WEIGHT ----------------
    a, b = normalize_weight_kg(si_raw), normalize_weight_kg(bl_raw)
    if a is None or b is None:
        return mk(Outcome.UNDETERMINED, a, b, "重量无法解析", 0.2)
    tol = max(WEIGHT_ABS_TOL, abs(a) * WEIGHT_REL_TOL)
    if abs(a - b) <= tol:
        return mk(Outcome.NORMALIZED_MATCH, a, b,
                  f"重量一致（差 {abs(a-b):.2f}kg，在容差 {tol:.2f}kg 内）", 0.98)
    return mk(Outcome.MISMATCH, a, b,
              f"毛重不同：SI {a:g}kg / BL {b:g}kg（差 {abs(a-b):g}kg）", 0.99)


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
        """人类可读的差异报告 —— 直接用于 demo 视频和人工复核界面。"""
        lines = [f"Email: {self.email_id}"]
        if not self.has_mismatch and not self.needs_human_review:
            lines.append("No mismatch detected.")     # 用例指定的原话
        for r in self.results:
            if r.outcome is Outcome.MISMATCH:
                lines.append(
                    f"  [MISMATCH] {r.label}: SI: {r.si_norm} / BL: {r.bl_norm}  — {r.reason}"
                )
            elif r.needs_review:
                lines.append(f"  [REVIEW]   {r.label}: {r.reason}")
        if self.needs_human_review:
            lines.append(f"  → 已转人工复核：{'; '.join(self.review_reasons)}")
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
    比对一封邮件所附的 SI 与 BL。

    si / bl：{field_key: value} —— 由抽取层（LLM/OCR）产出并经 schema 校验
    review_threshold：低于此置信度即转人工。默认值来自成本敏感阈值扫描
                      （见 evaluate.optimal_threshold），不是拍脑袋定的。
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
            reasons.append(f"{r.label}: 置信度 {r.confidence:.2f} 低于阈值 {review_threshold}")
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
