import type { Metadata } from "next";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Chip } from "@/components/Chip";
import { Icon } from "@/components/Icon";
import { SectionLabel } from "@/components/SectionLabel";
import { fmtScore } from "@/lib/format";

export const metadata: Metadata = { title: "Eval" };

/* Static artefacts copied from ../evals by `npm run sync-evals` (git-ignored at their source, committed here). */
interface Metrics {
  ts: string; sha: string;
  scores: { final_score: number; stage1_macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number };
  classification: { accuracy: number; macro_f1: number;
    per_class: Record<string, { precision: number; recall: number; f1: number; tp: number; fp: number; fn: number }>;
    confusion: Record<string, Record<string, number>> };
  field_level: { n_emails: number; n_gold_defect_fields: number; precision: number; recall: number; f1: number; tp: number; fp: number; fn: number };
}
interface Run { ts: string; sha: string; final_score: number; stage1_macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number }
interface Pert { desc?: string; final_score: number; macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number; pred_review: number }

const BEFORE_HARDENING: Record<string, number> = { P4b: 0.6805, P5: 0.3 };   // docs/PERTURBATION_REPORT.md
const PERT_ORDER = ["baseline", "P1", "P2", "P3", "P4a", "P4b", "P5"];

function load() {
  const dir = join(process.cwd(), "public", "evals");
  const metrics = JSON.parse(readFileSync(join(dir, "metrics_latest.json"), "utf8")) as Metrics;
  const history = readFileSync(join(dir, "history.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Run);
  const pert = JSON.parse(readFileSync(join(dir, "perturbation.json"), "utf8")) as Record<string, Pert>;
  return { metrics, history, pert };
}

/* Breakdown row: label · value, then a bar that grows to its share (staggered). */
function Rows({ items, tone = "" }: { items: { k: string; label: string; v: number; max?: number; tone?: string; hint?: string }[]; tone?: string }) {
  return (
    <ul className="rows">
      {items.map((it, i) => (
        <li key={it.k}>
          <div className="lv"><span>{it.label}{it.hint ? <span className="meta" style={{ display: "inline", marginLeft: 8 }}>{it.hint}</span> : null}</span><span className="v">{it.v.toFixed(3)}</span></div>
          <div className="track"><div className={`bar ${it.tone ?? tone}`} style={{ "--w": `${(it.v / (it.max ?? 1)) * 100}%`, "--d": `${260 + i * 90}ms` } as React.CSSProperties} /></div>
        </li>
      ))}
    </ul>
  );
}

export default function EvalPage() {
  const { metrics: m, history, pert } = load();
  const classes = Object.keys(m.classification.per_class);
  return (
    <div className="sections">
      <div className="phead reveal">
        <div className="copy">
          <h1>Evaluation</h1>
          <p className="lead">Scored black-box through the organiser&apos;s scoring service: we read aggregate scores only, never the ground truth.</p>
          <p className="hint"><Icon name="info.circle" /><span>1.0 on the organiser&apos;s v2 dataset and on six semantics-preserving perturbations of it — not a claim about unseen data.</span></p>
        </div>
        <div className="meta">last run {m.ts.replace("T", " ")} · commit <code>{m.sha}</code> · {history.length} runs recorded</div>
      </div>

      <section className="reveal" style={{ "--i": 1 } as React.CSSProperties}>
        <div className="scores">
          <div className="score final"><span className="v">{fmtScore(m.scores.final_score)}</span><span className="k">final score</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.stage1_macro_f1)}</span><span className="k">macro-F1</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.defect_f1)}</span><span className="k">defect F1</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.end_to_end)}</span><span className="k">end-to-end</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.esc_precision)}</span><span className="k">esc. precision</span></div>
        </div>
      </section>

      <section className="card reveal" style={{ "--i": 2 } as React.CSSProperties} aria-labelledby="h-trend">
        <SectionLabel id="h-trend" trailing={<Chip small status="normalized">not by AI · scored by the organiser&apos;s service</Chip>}>Score after each eval run</SectionLabel>
        <Trend runs={history} />
        <p className="meta" style={{ marginTop: "var(--s-3)" }}>0.766 rules only → 0.890 LLM fallback → 0.891 intent-aware escalation → 1.000 comparison fixes. The two dips are LLM quota outages during cache rebuilds, kept on record.</p>
      </section>

      <section className="grid-2 reveal" style={{ "--i": 3 } as React.CSSProperties}>
        <div className="card">
          <SectionLabel trailing="F1 per class · n = 520">Classification</SectionLabel>
          <Rows tone="ok" items={classes.map((c) => ({ k: c, label: c, v: m.classification.per_class[c].f1, hint: `n ${m.classification.per_class[c].tp + m.classification.per_class[c].fn}` }))} />
          <p className="meta" style={{ marginTop: 20 }}>Precision and recall are 1.000 for every class — the table view is the confusion matrix on the right.</p>
        </div>
        <div className="card">
          <SectionLabel trailing="rows = gold · columns = predicted">Confusion</SectionLabel>
          <div className="table-wrap confusion">
            <table>
              <thead><tr><th />{classes.map((c) => <th key={c} className="num" title={c}>{c.slice(0, 3)}</th>)}</tr></thead>
              <tbody>{classes.map((g) => (
                <tr key={g}><td className="muted tight">{g}</td>{classes.map((p) => { const v = m.classification.confusion[g][p]; return (
                  <td key={p} className={`num ${g === p ? "diag" : "off"} ${v === 0 ? "zero" : ""}`}>{v}</td>); })}</tr>))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="grid-2 reveal" style={{ "--i": 4 } as React.CSSProperties}>
        <div className="card">
          <SectionLabel trailing="hand-annotated golden set">Field level</SectionLabel>
          <Rows tone="ok" items={[
            { k: "p", label: "Precision", v: m.field_level.precision },
            { k: "r", label: "Recall", v: m.field_level.recall },
            { k: "f", label: "F1", v: m.field_level.f1 },
          ]} />
          <p className="meta" style={{ marginTop: 20 }}>{m.field_level.n_emails} emails, {m.field_level.n_gold_defect_fields} gold defect fields (tp {m.field_level.tp} · fp {m.field_level.fp} · fn {m.field_level.fn}).</p>
        </div>
        <div className="card">
          <SectionLabel trailing="before → after hardening">Perturbations</SectionLabel>
          <ul className="rows">
            {PERT_ORDER.filter((k) => pert[k]).map((k, i) => {
              const before = BEFORE_HARDENING[k] ?? pert[k].final_score, after = pert[k].final_score;
              return (
                <li key={k}>
                  <div className="lv"><span><span className="mono" style={{ fontSize: 13, color: "var(--text-3)", marginRight: 8 }}>{k}</span>{pert[k].desc ?? "unperturbed"}</span>
                    <span className="v">{before !== after ? <><span style={{ color: "var(--bad-text)" }}>{fmtScore(before)}</span> → </> : null}{fmtScore(after)}</span></div>
                  <div className="track"><div className={`bar ${before !== after ? "bad" : "ok"}`} style={{ "--w": `${before * 100}%`, "--d": `${260 + i * 90}ms` } as React.CSSProperties} /></div>
                  {before !== after ? <div className="track" style={{ marginTop: -4 }}><div className="bar ok" style={{ "--w": `${after * 100}%`, "--d": `${400 + i * 90}ms` } as React.CSSProperties} /></div> : null}
                </li>
              );
            })}
          </ul>
        </div>
      </section>

      <section className="reveal" style={{ "--i": 5 } as React.CSSProperties}>
        <SectionLabel trailing={<span>generated by <code>scripts_calibration.py</code></span>}>Charts</SectionLabel>
        <div className="figs">
          <figure className="fig wide"><div className="img"><img src="/evals/progress.png" alt="Score after each eval run: final score and the four axes over the recorded runs" /></div>
            <figcaption>progress.png — every eval run in <code>history.jsonl</code></figcaption></figure>
          <figure className="fig"><div className="img"><img src="/evals/calibration.png" alt="Reliability diagram: comparison confidence vs. accuracy, before fixes and current, with ECE" /></div>
            <figcaption>calibration.png — reliability diagram and ECE of the comparison core, baseline vs. current</figcaption></figure>
          <figure className="fig"><div className="img"><img src="/evals/threshold.png" alt="Cost-sensitive sweep of the auto-pass threshold and the share routed to human review" /></div>
            <figcaption>threshold.png — cost-sensitive threshold sweep (costs are placeholders until Averis confirms ratios)</figcaption></figure>
        </div>
      </section>
    </div>
  );
}

/* A small trend line, drawn by hand: no chart library. Fibonacci-derived box (610 × 233 ≈ φ). */
function Trend({ runs }: { runs: Run[] }) {
  const W = 610, H = 233, px = 8, py = 12, n = runs.length;
  const x = (i: number) => px + (i * (W - 2 * px)) / Math.max(1, n - 1);
  const y = (v: number) => py + (1 - v) * (H - 2 * py);
  const series: { k: keyof Run; label: string; c: string }[] = [
    { k: "final_score", label: "final", c: "var(--accent)" }, { k: "stage1_macro_f1", label: "macro-F1", c: "var(--ok)" },
    { k: "defect_f1", label: "defect F1", c: "var(--warn)" }, { k: "end_to_end", label: "end-to-end", c: "var(--bad)" },
    { k: "esc_precision", label: "esc. precision", c: "var(--text-3)" },
  ];
  const path = (k: keyof Run) => runs.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(Number(r[k])).toFixed(1)}`).join(" ");
  return (
    <div>
      <svg className="trend" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Score trend across eval runs">
        {[0.5, 0.75, 1].map((g) => <g key={g}><line x1={px} x2={W - px} y1={y(g)} y2={y(g)} stroke="var(--line)" /><text x={W - px} y={y(g) - 3} textAnchor="end" fontSize="10" fill="var(--text-3)">{g.toFixed(2)}</text></g>)}
        {series.map((s) => <path key={s.k} d={path(s.k)} fill="none" stroke={s.c} strokeWidth={s.k === "final_score" ? 2 : 1.25} strokeLinejoin="round" strokeLinecap="round" opacity={s.k === "final_score" ? 1 : 0.7} />)}
        {runs.map((r, i) => <circle key={i} cx={x(i)} cy={y(r.final_score)} r={2.5} fill="var(--accent)" />)}
      </svg>
      <div className="chips" style={{ marginTop: "var(--s-2)" }}>
        {series.map((s) => <span key={s.k} className="chip sm"><span style={{ width: 12, height: 2, background: s.c, display: "inline-block", borderRadius: 1 }} />{s.label}</span>)}
        <span className="chip sm">{n} runs · {runs[0]?.ts.slice(0, 10)} → {runs[n - 1]?.ts.slice(0, 10)}</span>
      </div>
    </div>
  );
}
