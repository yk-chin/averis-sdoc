"""shipdoc_core - the deterministic core for shipping-document checks (no LLM, no network, pure functions)"""
from .fields import FIELDS, FIELD_KEYS, resolve_label
from .compare import compare_documents, compare_field, Outcome, ComparisonReport
from .evaluate import (classification_report, field_level_prf, calibration,
                       optimal_threshold, prf)
__all__ = ["FIELDS","FIELD_KEYS","resolve_label","compare_documents","compare_field",
           "Outcome","ComparisonReport","classification_report","field_level_prf",
           "calibration","optimal_threshold","prf"]
