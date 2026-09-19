"""
置信度校准 + 成本敏感阈值扫描 + 分数进度
==========================================
用法：  python scripts_calibration.py
产出：  evals/calibration.png   reliability diagram + ECE
        evals/threshold.png     阈值-期望成本曲线（含最优阈值）
        evals/progress.png      evals/history.jsonl 的分数折线
        evals/calibration.json  原始数字，供 slides / Q&A 引用

"正确与否"的真值来源
--------------------
打分服务是黑盒，拿不到逐邮件 gold。但当前 submission.json 在四轴上全部 1.0，
即它在所有被打分字段上与 gold 一致 → 用它作为参考答案。
为了让校准/阈值曲线有真正的权衡可看，脚本同时跑**修复前**的比对核心
（BASELINE_COMMIT，用 git worktree 隔离，那版有 12 处错误）与当前核心，两条曲线并排。
"""
from __future__ import annotations
import json, os, pathlib, subprocess, sys

# ============================================================================
# ⚠️ 成本参数目前是假设值。9/21 Workshop 2 向 Averis 求证真实比例后，改这三行即可。
# ============================================================================
COST_MISSED      = 8.0    # 漏掉一个真实差异：改单、延误、清关问题
COST_FALSE_ALARM = 1.0    # 误报：操作员白看一眼
COST_REVIEW      = 0.35   # 转人工：一次复核的人工成本
# ============================================================================

BASELINE_COMMIT = "e7dd042"          # 修复前的比对核心（规则版基线）
ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
EVALS = ROOT / "evals"

# 参考调色板（dataviz 规范）
C_CURRENT, C_BASELINE = "#2a78d6", "#eb6834"
C_SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"]
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"


# ----------------------------------------------------------------------------
# --collect 模式：在指定代码树下跑比对核心，输出每封邮件的 (min_confidence, 判定)
# ----------------------------------------------------------------------------
def collect(tree: pathlib.Path, data: pathlib.Path) -> dict:
    here = str(pathlib.Path(__file__).resolve().parent)
    sys.path = [str(tree)] + [p for p in sys.path if p not in (here, "")]   # 必须先于本树
    from pipeline.run import classify_attachments
    from shipdoc_core.compare import compare_documents, Outcome
    from shipdoc_core.fields import FIELD_KEYS

    out = {}
    for f in sorted((data / "inbox").glob("*.json")):
        e = json.load(open(f, encoding="utf-8"))
        atts = e.get("attachments") or []
        has_si = any("_SI." in os.path.basename(a).upper() for a in atts)
        has_bl = any("_BL." in os.path.basename(a).upper() for a in atts)
        if not (has_si and has_bl):
            continue
        res = classify_attachments(data, atts)
        if isinstance(res[0], dict):                       # 当前签名 (slots, unassigned)
            si = (res[0]["SI"] or (None, None))[0]; bl = (res[0]["BL"] or (None, None))[0]
        else:                                              # 基线签名 (si, src, bl, src)
            si, _, bl, _ = res
        if si is None or bl is None or not si.readable or not bl.readable:
            continue
        if si.doc_type != "SI" or bl.doc_type != "BL":
            continue
        if any(k in si.blanks or k in bl.blanks or si.fields.get(k) is None or bl.fields.get(k) is None
               for k in FIELD_KEYS):
            continue
        rep = compare_documents(e["email_id"], si.fields, bl.fields)
        out[e["email_id"]] = {
            "min_confidence": rep.min_confidence,
            "mismatched_fields": sorted(rep.mismatched_fields),
            "needs_review": rep.needs_human_review,
        }
    return out


def ensure_worktree(commit: str) -> pathlib.Path:
    wt = ROOT / ".cache" / "worktrees" / commit
    if not (wt / "shipdoc_core").exists():
        wt.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "worktree", "add", "--detach", str(wt), commit],
                       cwd=ROOT, check=True, capture_output=True)
    return wt


def collect_via_subprocess(tree: pathlib.Path) -> dict:
    r = subprocess.run([sys.executable, str(pathlib.Path(__file__).resolve()),
                        "--collect", str(tree), str(DATA)],
                       capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(r.stdout)


# ----------------------------------------------------------------------------
# 与参考答案对齐 → (confidences, correct, is_flagged)
# ----------------------------------------------------------------------------
def align(collected: dict, reference: dict):
    """正确 = 核心标出的差异字段集合与参考答案一致。
    不把 needs_review 算进正确性：转不转人工正是阈值扫描要决定的事。"""
    conf, correct, flagged, wrong = [], [], [], []
    for eid, v in collected.items():
        ref = reference[eid]
        ok = set(v["mismatched_fields"]) == set(ref["defect_fields"])
        conf.append(v["min_confidence"]); correct.append(ok); flagged.append(bool(v["mismatched_fields"]))
        if not ok:
            wrong.append((eid, v["min_confidence"], v["mismatched_fields"], ref["defect_fields"]))
    return conf, correct, flagged, wrong


# ----------------------------------------------------------------------------
# 画图
# ----------------------------------------------------------------------------
def _style(ax, title: str):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=11, loc="left", pad=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_calibration(series: dict, path: pathlib.Path):
    import matplotlib.pyplot as plt
    from shipdoc_core.evaluate import calibration
    fig, axes = plt.subplots(1, len(series), figsize=(5.2 * len(series), 4.6), facecolor=SURFACE)
    axes = list(axes) if len(series) > 1 else [axes]
    results = {}
    for ax, (name, (color, conf, correct)) in zip(axes, series.items()):
        bins, ece = calibration(conf, correct, bins=10)
        results[name] = {"ece": ece, "n": len(conf),
                         "bins": [b.__dict__ for b in bins]}
        _style(ax, f"{name}   n={len(conf)}   ECE={ece:.3f}")
        ax.plot([0, 1], [0, 1], color=AXIS, linewidth=1, linestyle=(0, (4, 3)), zorder=1)
        for b in bins:
            x = (b.lo + b.hi) / 2
            ax.bar(x, b.accuracy, width=0.1 - 0.006, color=color, zorder=2)
            ax.text(x, b.accuracy + 0.02, f"n={b.n}", ha="center", va="bottom", fontsize=8, color=INK2)
        ax.set_xlim(0, 1); ax.set_ylim(0, 1.08)
        ax.set_xlabel("min_confidence (bin)", color=MUTED, fontsize=9)
        ax.set_ylabel("accuracy in bin", color=MUTED, fontsize=9)
        ax.text(0.02, 0.96, "dashed = perfectly calibrated", transform=ax.transAxes,
                ha="left", va="top", fontsize=8, color=MUTED)
    fig.suptitle("Comparison core: reliability diagram", x=0.01, ha="left", color=INK, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(path, dpi=160, facecolor=SURFACE); plt.close(fig)
    return results


def plot_threshold(series: dict, path: pathlib.Path):
    import matplotlib.pyplot as plt
    from shipdoc_core.evaluate import optimal_threshold
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8.5, 6.4), sharex=True, facecolor=SURFACE,
                                   gridspec_kw={"height_ratios": [3, 2]})
    results = {}
    for name, (color, conf, correct, flagged) in series.items():
        best, pts = optimal_threshold(conf, correct, flagged,
                                      cost_missed=COST_MISSED, cost_false_alarm=COST_FALSE_ALARM,
                                      cost_review=COST_REVIEW)
        xs = [p.threshold for p in pts]
        ax1.plot(xs, [p.expected_cost for p in pts], color=color, linewidth=2, label=name, zorder=2)
        ax1.plot([best.threshold], [best.expected_cost], "o", color=color, markersize=9,
                 markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)
        right = best.threshold > 0.7                      # 靠右的最优点，注释放左侧免得裁切
        ax1.annotate(f"optimum {best.threshold:.2f}\ncost {best.expected_cost:.1f}, review {best.review_rate:.0%}",
                     (best.threshold, best.expected_cost), textcoords="offset points",
                     xytext=(-12, 12) if right else (10, 12), ha="right" if right else "left",
                     fontsize=8.5, color=INK2)
        ax2.plot(xs, [p.review_rate for p in pts], color=color, linewidth=2, label=name)
        results[name] = {"best": best.__dict__, "curve": [p.__dict__ for p in pts]}
    _style(ax1, f"Expected cost vs. auto-pass threshold   "
                f"(missed={COST_MISSED:g}, false alarm={COST_FALSE_ALARM:g}, review={COST_REVIEW:g})")
    _style(ax2, "Share routed to human review")
    ax1.set_ylabel("expected cost", color=MUTED, fontsize=9)
    ax2.set_ylabel("review rate", color=MUTED, fontsize=9)
    ax2.set_xlabel("threshold: cases with min_confidence below it go to a human", color=MUTED, fontsize=9)
    ax2.set_ylim(0, 1.05); ax1.set_xlim(0, 1)
    ax1.legend(frameon=False, fontsize=9, labelcolor=INK2)
    fig.suptitle("Cost-sensitive threshold sweep", x=0.01, ha="left", color=INK, fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(path, dpi=160, facecolor=SURFACE); plt.close(fig)
    return results


def plot_progress(history: list[dict], path: pathlib.Path):
    import matplotlib.pyplot as plt
    keys = [("final_score", "final"), ("stage1_macro_f1", "macro-F1"), ("defect_f1", "defect F1"),
            ("end_to_end", "end-to-end"), ("esc_precision", "esc. precision")]
    xs = list(range(1, len(history) + 1))
    fig, ax = plt.subplots(figsize=(8.5, 4.6), facecolor=SURFACE)
    _style(ax, "Score after each eval run   (evals/history.jsonl)")
    last = {}
    for i, (k, label) in enumerate(keys):
        ys = [h[k] for h in history]
        lw, ms = (3, 9) if k == "final_score" else (1.6, 6)
        ax.plot(xs, ys, color=C_SERIES[i], linewidth=lw, marker="o", markersize=ms,
                markeredgecolor=SURFACE, markeredgewidth=1.5, label=label, zorder=3 if k == "final_score" else 2)
        last.setdefault(round(ys[-1], 3), []).append(label)
    for y, labels in last.items():                      # 终点相同的系列合并成一个标签，避免重叠
        txt = f"all {len(labels)} = {y:.3f}" if len(labels) == len(keys) else f"{', '.join(labels)} {y:.3f}"
        ax.text(xs[-1] + 0.08, y, txt, va="center", fontsize=8.5, color=INK2)
    # 每次 run 在 final 线上直接标值
    for x, h in zip(xs, history):
        ax.text(x, h["final_score"] + 0.035, f"{h['final_score']:.3f}", ha="center", fontsize=8.5, color=INK)
    ax.set_xticks(xs)
    ax.set_xticklabels([f"run {i}\n{h['ts'][5:16].replace('T', ' ')}\n{h['sha']}" for i, h in zip(xs, history)],
                       fontsize=8)
    ax.set_ylim(0, 1.06); ax.set_xlim(0.7, len(xs) + 1.4)
    ax.set_ylabel("score", color=MUTED, fontsize=9)
    ax.legend(frameon=False, fontsize=9, labelcolor=INK2, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=160, facecolor=SURFACE); plt.close(fig)


# ----------------------------------------------------------------------------
def main():
    import matplotlib
    matplotlib.use("Agg")
    matplotlib.rcParams["font.family"] = ["Segoe UI", "Microsoft YaHei", "DejaVu Sans", "sans-serif"]
    EVALS.mkdir(exist_ok=True)

    reference = json.load(open(ROOT / "submission.json", encoding="utf-8"))
    print(f"参考答案: submission.json（当前版本，四轴 1.0）")

    cur = collect_via_subprocess(ROOT)
    base = collect_via_subprocess(ensure_worktree(BASELINE_COMMIT))
    c_conf, c_ok, c_flag, c_wrong = align(cur, reference)
    b_conf, b_ok, b_flag, b_wrong = align(base, reference)
    print(f"比对核心实际判定的邮件: 当前 {len(cur)} 封（错 {len(c_wrong)}），基线 {BASELINE_COMMIT} {len(base)} 封（错 {len(b_wrong)}）")

    cal = plot_calibration({f"before fixes ({BASELINE_COMMIT})": (C_BASELINE, b_conf, b_ok),
                            "current": (C_CURRENT, c_conf, c_ok)}, EVALS / "calibration.png")
    thr = plot_threshold({f"before fixes ({BASELINE_COMMIT})": (C_BASELINE, b_conf, b_ok, b_flag),
                          "current": (C_CURRENT, c_conf, c_ok, c_flag)}, EVALS / "threshold.png")
    history = [json.loads(l) for l in open(EVALS / "history.jsonl", encoding="utf-8") if l.strip()]
    plot_progress(history, EVALS / "progress.png")

    json.dump({"costs": {"missed": COST_MISSED, "false_alarm": COST_FALSE_ALARM, "review": COST_REVIEW},
               "calibration": cal, "threshold": thr,
               "baseline_errors": b_wrong, "current_errors": c_wrong},
              open(EVALS / "calibration.json", "w", encoding="utf-8"), indent=1, ensure_ascii=False)

    print("\n=== ECE ===")
    for name, r in cal.items():
        print(f"  {name:28} ECE {r['ece']:.4f}   n={r['n']}")
        for b in r["bins"]:
            print(f"      [{b['lo']:.1f},{b['hi']:.1f})  n={b['n']:3d}  mean_conf={b['mean_confidence']:.3f}  acc={b['accuracy']:.3f}")
    print("\n=== 最优阈值 ===")
    for name, r in thr.items():
        b = r["best"]
        print(f"  {name:28} threshold={b['threshold']:.3f}  cost={b['expected_cost']:.2f}  "
              f"review={b['review_n']}/{b['review_n']+b['auto_n']} ({b['review_rate']:.0%})  "
              f"missed={b['missed']}  false_alarms={b['false_alarms']}")
    if b_wrong:
        print(f"\n=== 基线核心的 {len(b_wrong)} 处错误（min_conf, 我们标的字段, 参考字段）===")
        for w in b_wrong: print("  ", w)
    print(f"\n图已写入 {EVALS / 'calibration.png'}, {EVALS / 'threshold.png'}, {EVALS / 'progress.png'}")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--collect":
        print(json.dumps(collect(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3]))))
    else:
        main()
