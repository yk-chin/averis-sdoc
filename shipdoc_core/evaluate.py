"""
评估与校准 (Evaluation & Calibration)
=====================================
对应 rubric：Technical Feasibility & Validation 15 分
          —— "Critical assumptions are validated with clear evidence"

主办方提供了 self-evaluation 端点。绝大多数队会把它当玩具跑两次；
我们把它当 KPI 仪表盘：每次改动都跑，记录版本、分数、回归。

本文件还实现了别的队不会做的两件事：
  1. 置信度校准 —— 系统说 0.8 时，它真的有 80% 正确吗？(ECE + reliability diagram)
  2. 成本敏感阈值优化 —— 漏报一个真实差异的代价 ≠ 误报的代价。
     阈值应当是解出来的，不是拍脑袋定的。
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


# ----------------------------------------------------------- 分类指标

@dataclass
class PRF:
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int

    def as_dict(self) -> dict:
        return asdict(self)


def prf(tp: int, fp: int, fn: int) -> PRF:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return PRF(round(p, 4), round(r, 4), round(f, 4), tp, fp, fn)


def classification_report(y_true: list[str], y_pred: list[str]) -> dict:
    """邮件分类的 per-class PRF + macro F1 + accuracy。"""
    labels = sorted(set(y_true) | set(y_pred))
    per: dict[str, PRF] = {}
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lab and p == lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lab and p != lab)
        per[lab] = prf(tp, fp, fn)
    acc = sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true) if y_true else 0.0
    macro = sum(v.f1 for v in per.values()) / len(per) if per else 0.0
    return {
        "accuracy": round(acc, 4),
        "macro_f1": round(macro, 4),
        "per_class": {k: v.as_dict() for k, v in per.items()},
        "confusion": _confusion(y_true, y_pred, labels),
    }


def _confusion(y_true, y_pred, labels) -> dict:
    m = {t: {p: 0 for p in labels} for t in labels}
    for t, p in zip(y_true, y_pred):
        m[t][p] += 1
    return m


def field_level_prf(gold: dict[str, set[str]], pred: dict[str, set[str]]) -> PRF:
    """
    字段级差异检出 PRF —— 比邮件级严格得多，也更能暴露问题。
    gold / pred：{email_id: {有差异的字段名}}
    """
    tp = fp = fn = 0
    for eid in set(gold) | set(pred):
        g, p = gold.get(eid, set()), pred.get(eid, set())
        tp += len(g & p)
        fp += len(p - g)
        fn += len(g - p)
    return prf(tp, fp, fn)


# ----------------------------------------------------------- 校准

@dataclass
class CalibrationBin:
    lo: float
    hi: float
    n: int
    mean_confidence: float
    accuracy: float


def calibration(confidences: list[float], correct: list[bool], bins: int = 10):
    """
    可靠性分箱 + Expected Calibration Error。
    ECE 越接近 0，说明置信度越"诚实"。
    这张图放进 slides，就是 Technical Feasibility 那 15 分的直接证据。
    """
    if not confidences:
        return [], 0.0
    n = len(confidences)
    out: list[CalibrationBin] = []
    ece = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [
            j for j, c in enumerate(confidences)
            if (lo <= c < hi) or (i == bins - 1 and c == 1.0)
        ]
        if not idx:
            continue
        mc = sum(confidences[j] for j in idx) / len(idx)
        acc = sum(1 for j in idx if correct[j]) / len(idx)
        out.append(CalibrationBin(lo, hi, len(idx), round(mc, 4), round(acc, 4)))
        ece += (len(idx) / n) * abs(acc - mc)
    return out, round(ece, 4)


# ----------------------------------------------------------- 阈值优化

@dataclass
class ThresholdPoint:
    threshold: float
    auto_n: int
    review_n: int
    false_alarms: int      # 自动放行里的误报
    missed: int            # 自动放行里的漏报
    review_rate: float
    expected_cost: float


def optimal_threshold(
    confidences: list[float],
    correct: list[bool],
    is_flagged: list[bool],
    *,
    cost_missed: float = 8.0,      # 漏掉真实差异：改单、延误、清关问题
    cost_false_alarm: float = 1.0, # 误报：操作员白看一眼
    cost_review: float = 0.35,     # 转人工：一次复核的人工成本
    grid: int = 41,
):
    """
    成本敏感阈值扫描。

    低于阈值的 case 转人工（付 cost_review，不再产生错误成本）；
    高于阈值的自动放行，错了就付 cost_missed 或 cost_false_alarm。

    返回 (最优点, 完整扫描曲线)。曲线画进 slides，
    Q&A 被问"阈值怎么定的"时直接指着它回答。
    """
    pts: list[ThresholdPoint] = []
    n = len(confidences)
    for k in range(grid):
        th = k / (grid - 1)
        auto = [i for i, c in enumerate(confidences) if c >= th]
        review = n - len(auto)
        fa = sum(1 for i in auto if not correct[i] and is_flagged[i])
        ms = sum(1 for i in auto if not correct[i] and not is_flagged[i])
        cost = fa * cost_false_alarm + ms * cost_missed + review * cost_review
        pts.append(ThresholdPoint(
            threshold=round(th, 4), auto_n=len(auto), review_n=review,
            false_alarms=fa, missed=ms,
            review_rate=round(review / n, 4) if n else 0.0,
            expected_cost=round(cost, 4),
        ))
    best = min(pts, key=lambda p: (p.expected_cost, -p.threshold))
    return best, pts
