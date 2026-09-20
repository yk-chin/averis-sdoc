import type { Metadata } from "next";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { Chip } from "@/components/Chip";
import { Icon } from "@/components/Icon";
import { fmtScore } from "@/lib/format";

export const metadata: Metadata = { title: "Eval" };

/* Static artefacts copied from ../evals by `npm run sync-evals` (git-ignored at their source, committed here). */
interface Metrics {
  ts: string; sha: string;
  scores: { final_score: number; stage1_macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number };
  classification: { accuracy: number; macro_f1: number;
    per_class: Record<string, { precision: number; recall: number; f1: number; tp: number; fp: number; fn: number }>;
    confusion: Record<string, Record<string, number>> };
  field_level: { n_emails: number; n_gold_defect_fields: number; precision: number; recall: number; f1: number; tp: number; fp: number; fn: number;
                 precision_ci95?: [number, number]; recall_ci95?: [number, number] };
  ai_usage?: AiUsage;
}
interface Run { ts: string; sha: string; final_score: number; stage1_macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number }
interface Pert { desc?: string; final_score: number; macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number; pred_review: number }

const BEFORE_HARDENING: Record<string, string> = { P4b: "0.6805", P5: "0.3000" };   // docs/PERTURBATION_REPORT.md
interface Ablation { ts: string; results: Record<string, { final_score: number; stage1_macro_f1: number; defect_f1: number; end_to_end: number; esc_precision: number; statuses?: Record<string, number> }> }
interface Holdout { ts: string; llm: boolean; n_emails: number; n_bl_comparison: number; final_score: number; stage1_macro_f1: number; defect_f1: number;
                    end_to_end: number; end_to_end_ci95: [number, number]; esc_precision: number; esc_recall: number; misses: { email_id: string; diff: Record<string, [unknown, unknown]> }[] }
interface AiUsage { emails: number; decided_by_rule: number; decided_by_llm: number; llm_share: number; vision_documents: number; scoring_runs_recorded: number; distinct_code_versions_scored: number }
const PERT_ORDER = ["baseline", "P1", "P2", "P3", "P4a", "P4b", "P5"];

function load() {
  const dir = join(process.cwd(), "public", "evals");
  const metrics = JSON.parse(readFileSync(join(dir, "metrics_latest.json"), "utf8")) as Metrics;
  const history = readFileSync(join(dir, "history.jsonl"), "utf8").split("\n").filter(Boolean).map((l) => JSON.parse(l) as Run);
  const pert = JSON.parse(readFileSync(join(dir, "perturbation.json"), "utf8")) as Record<string, Pert>;
  const opt = (f: string) => { try { return JSON.parse(readFileSync(join(dir, f), "utf8")); } catch { return null; } };
  return { metrics, history, pert, ablation: opt("ablation.json") as Ablation | null, holdout: opt("holdout_result_llm.json") as Holdout | null };
}

export default function EvalPage() {
  const { metrics: m, history, pert, ablation, holdout } = load();
  const ai = m.ai_usage;
  const classes = Object.keys(m.classification.per_class);
  return (
    <div className="sections">
      <div className="phead reveal">
        <div className="copy">
          <h1>Evaluation</h1>
          <p className="lead">Scored black-box through the organiser&apos;s scoring service: we read aggregate scores only, never the ground truth.</p>
          <p className="hint"><Icon name="info.circle" /><span>These are 1.0 on the organiser&apos;s v2 dataset and on six semantics-preserving perturbations of it — not a claim about unseen data.</span></p>
        </div>
        <div className="meta">last eval run {m.ts.replace("T", " ")} · eval commit <code>{m.sha}</code> (the API build in the header may be newer) · {history.length} runs recorded</div>
      </div>

      <section className="reveal" style={{ "--i": 1 } as React.CSSProperties}>
        <div className="scores">
          <div className="score final"><span className="v">{fmtScore(m.scores.final_score)}</span><span className="k">final score</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.stage1_macro_f1)}</span><span className="k">classification macro-F1</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.defect_f1)}</span><span className="k">defect F1</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.end_to_end)}</span><span className="k">end-to-end</span></div>
          <div className="score"><span className="v">{fmtScore(m.scores.esc_precision)}</span><span className="k">escalation precision</span></div>
        </div>
      </section>

      <section className="card reveal" style={{ "--i": 2 } as React.CSSProperties} aria-labelledby="h-trend">
        <div className="card-head"><span className="numeral">01</span><h2 id="h-trend">Score after each eval run</h2><span className="tag">evals/history.jsonl</span></div>
        <Trend runs={history} />
        <p className="meta" style={{ marginTop: "var(--s-3)" }}>0.766 rules only → 0.890 LLM fallback → 0.891 intent-aware escalation → 1.000 comparison fixes. The two dips are LLM quota outages during cache rebuilds, kept on record.</p>
      </section>

      <section className="grid-2 reveal" style={{ "--i": 3 } as React.CSSProperties}>
        <div className="card">
          <div className="card-head"><span className="numeral">02</span><h2>Per class</h2><span className="tag">n = 520</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Class</th><th className="num">P</th><th className="num">R</th><th className="num">F1</th><th className="num">n</th></tr></thead>
              <tbody>{classes.map((c) => { const p = m.classification.per_class[c]; return (
                <tr key={c}><td>{c}</td><td className="num">{p.precision.toFixed(3)}</td><td className="num">{p.recall.toFixed(3)}</td><td className="num">{p.f1.toFixed(3)}</td><td className="num">{p.tp + p.fn}</td></tr>); })}
              </tbody>
            </table>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><span className="numeral">03</span><h2>Confusion</h2><span className="tag">rows = gold · columns = predicted</span></div>
          <div className="table-wrap confusion">
            <table>
              <thead><tr><th />{classes.map((c) => <th key={c} className="num" title={c}>{c.slice(0, 3)}</th>)}</tr></thead>
              <tbody>{classes.map((g) => (
                <tr key={g}><td className="muted">{g}</td>{classes.map((p) => { const v = m.classification.confusion[g][p]; return (
                  <td key={p} className={`num ${g === p ? "diag" : "off"} ${v === 0 ? "zero" : ""}`}>{v}</td>); })}</tr>))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="grid-2 reveal" style={{ "--i": 4 } as React.CSSProperties}>
        <div className="card">
          <div className="card-head"><span className="numeral">04</span><h2>Field level</h2><span className="tag">hand-annotated golden set</span></div>
          <div className="stats" style={{ gridTemplateColumns: "repeat(3,1fr)" }}>
            <div className="stat"><span className="v">{m.field_level.precision.toFixed(3)}</span><span className="k">precision</span></div>
            <div className="stat"><span className="v">{m.field_level.recall.toFixed(3)}</span><span className="k">recall</span></div>
            <div className="stat"><span className="v">{m.field_level.f1.toFixed(3)}</span><span className="k">F1</span></div>
          </div>
          <p className="meta" style={{ marginTop: "var(--s-3)" }}>{m.field_level.n_emails} emails, {m.field_level.n_gold_defect_fields} gold defect fields (tp {m.field_level.tp} · fp {m.field_level.fp} · fn {m.field_level.fn}).
            {m.field_level.precision_ci95 ? <> Small n: 95% Wilson CI precision {m.field_level.precision_ci95[0].toFixed(2)}–{m.field_level.precision_ci95[1].toFixed(2)}, recall {m.field_level.recall_ci95?.[0].toFixed(2)}–{m.field_level.recall_ci95?.[1].toFixed(2)} — the interval is the honest number.</> : null}</p>
        </div>
        <div className="card">
          <div className="card-head"><span className="numeral">05</span><h2>Perturbations</h2><span className="tag">before → after hardening</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>#</th><th>What changed</th><th className="num">before</th><th className="num">after</th></tr></thead>
              <tbody>{PERT_ORDER.filter((k) => pert[k]).map((k) => (
                <tr key={k}><td className="mono">{k}</td><td>{pert[k].desc ?? "unperturbed"}</td>
                  <td className="num">{BEFORE_HARDENING[k] ? <Chip small status="MISMATCH">{BEFORE_HARDENING[k]}</Chip> : fmtScore(pert[k].final_score)}</td>
                  <td className="num"><Chip small status="OK">{fmtScore(pert[k].final_score)}</Chip></td></tr>))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section className="card reveal" style={{ "--i": 5 } as React.CSSProperties} aria-labelledby="h-validity">
        <div className="card-head"><span className="numeral">06</span><h2 id="h-validity">Threats to validity · AI usage · Ablation · Hold-out</h2><span className="tag">read this before the 1.0</span></div>
        <div className="grid-2">
          <div className="stack-s">
            <h3>Threats to validity</h3>
            <p className="muted">{ai ? <>{ai.scoring_runs_recorded} scoring queries over {ai.distinct_code_versions_scored} code versions were made against the same held-out set. Even black-box (aggregate score only), repeated querying is adaptive overfitting risk. </> : null}
              Counter-measures: six semantics-preserving perturbations (above), confidence intervals on every small sample, and an independent hold-out set authored by us, never tuned on — its number is below, not hidden.</p>
            <h3 style={{ marginTop: "var(--s-2)" }}>AI usage on this inbox</h3>
            {ai ? <div className="chips">
              <Chip small>rules decided {ai.decided_by_rule}</Chip>
              <Chip small status="normalized">Gemini decided {ai.decided_by_llm} ({(ai.llm_share * 100).toFixed(1)}%)</Chip>
              <Chip small status="normalized">vision proposals for {ai.vision_documents} scanned documents</Chip>
            </div> : <p className="meta">ai_usage not recorded</p>}
            <p className="muted">LLM proposes, the deterministic core disposes: Gemini classifies the emails the rules are not sure about and reads scans the parser cannot; every verdict is still a pure function with evidence.</p>
          </div>
          <div className="stack-s">
            <h3>Ablation — same dataset, same scorer</h3>
            {ablation ? <div className="table-wrap"><table>
              <thead><tr><th>configuration</th><th className="num">final</th><th className="num">macro-F1</th><th className="num">defect F1</th><th className="num">end-to-end</th><th className="num">esc. precision</th></tr></thead>
              <tbody>{["rules_only", "llm_only", "hybrid"].filter((k) => ablation.results[k]).map((k) => { const r = ablation.results[k]; return (
                <tr key={k}><td>{k === "hybrid" ? <b>hybrid (shipped)</b> : k.replace("_", " ")}</td><td className="num">{fmtScore(r.final_score)}</td><td className="num">{r.stage1_macro_f1.toFixed(3)}</td><td className="num">{r.defect_f1.toFixed(3)}</td><td className="num">{r.end_to_end.toFixed(3)}</td><td className="num">{r.esc_precision < 0.5 ? <Chip small status="MISMATCH">{r.esc_precision.toFixed(3)}</Chip> : r.esc_precision.toFixed(3)}</td></tr>); })}
              </tbody></table></div> : <p className="meta">ablation.json not present</p>}
            {ablation?.results.llm_only?.statuses ? <p className="meta">LLM only sends {ablation.results.llm_only.statuses.NEEDS_REVIEW ?? "?"} of 220 comparisons to a human (20 are genuine) — the false-alarm failure mode the deterministic core exists to prevent.</p> : null}
            <h3 style={{ marginTop: "var(--s-2)" }}>Independent hold-out ({holdout?.n_emails ?? "—"} self-authored emails, never tuned on)</h3>
            {holdout ? <>
              <div className="chips">
                <Chip small status={holdout.final_score >= 0.9 ? "OK" : "NEEDS_REVIEW"}>final {fmtScore(holdout.final_score)}</Chip>
                <Chip small>macro-F1 {holdout.stage1_macro_f1.toFixed(3)}</Chip>
                <Chip small>defect F1 {holdout.defect_f1.toFixed(3)}</Chip>
                <Chip small>end-to-end {holdout.end_to_end.toFixed(3)} (CI {holdout.end_to_end_ci95[0].toFixed(2)}–{holdout.end_to_end_ci95[1].toFixed(2)})</Chip>
                <Chip small>esc. P {holdout.esc_precision.toFixed(2)} / R {holdout.esc_recall.toFixed(2)}</Chip>
              </div>
              <p className="meta">{holdout.misses.length} misses, all listed in <code>evals/holdout_result_llm.json</code>: company-suffix synonyms beyond our table (K.K. ↔ Kabushiki Kaisha), UN/LOCODEs outside our 45 codes when one side gives only the code, label abbreviations outside our alias list, and grey-zone parties sent to a person instead of called a mismatch. Reported, not patched — that is the point of a hold-out.</p>
            </> : <p className="meta">holdout_result_llm.json not present</p>}
          </div>
        </div>
      </section>

      <section className="reveal" style={{ "--i": 6 } as React.CSSProperties}>
        <div className="figs">
          <figure className="fig wide"><div className="img"><img src="/evals/progress.png" alt="Score after each eval run: final score and the four axes over the recorded runs" /></div>
            <figcaption>progress.png — every eval run in <code>history.jsonl</code>; generated by <code>scripts/calibration.py</code></figcaption></figure>
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
