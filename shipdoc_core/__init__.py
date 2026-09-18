"""shipdoc_core —— 航运单证核对的确定性内核（零 LLM、零网络、纯函数）"""
from .fields import FIELDS, FIELD_KEYS, resolve_label
from .compare import compare_documents, compare_field, Outcome, ComparisonReport
from .evaluate import (classification_report, field_level_prf, calibration,
                       optimal_threshold, prf)
__all__ = ["FIELDS","FIELD_KEYS","resolve_label","compare_documents","compare_field",
           "Outcome","ComparisonReport","classification_report","field_level_prf",
           "calibration","optimal_threshold","prf"]
