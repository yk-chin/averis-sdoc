"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import type { Decision, Report, Status } from "@/lib/types";
import { STATUSES } from "@/lib/types";
import { STATUS_LABEL, fmtTime } from "@/lib/format";
import { Chip } from "./Chip";
import { Icon } from "./Icon";

/* Human in the loop, on the page where the evidence is. Confirm = "the AI is right". Correct = the reviewer
   states the decision the record should carry; the AI's original decision is never overwritten - the API
   stores an audit trail next to it and exposes effective_decision. The review credential (review token) is
   asked once per browser session; production replaces it with an OIDC identity (docs/DEPLOY.md). */

const FIELD_KEYS = ["shipper", "consignee", "notify_party", "port_of_loading", "port_of_discharge", "container_count", "gross_weight_kg"];

export function ReviewPanel({ reportKey, ai, review }: { reportKey: string; ai: Decision; review: Report["review"] | null }) {
  const router = useRouter();
  const [mode, setMode] = useState<"idle" | "confirm" | "correct">("idle");
  const [reviewer, setReviewer] = useState("");
  const [token, setToken] = useState("");
  const [note, setNote] = useState("");
  const [status, setStatus] = useState<Status>(ai.status);
  const [defects, setDefects] = useState<string[]>(ai.defect_fields);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Report["review"] | null>(review ?? null);

  useEffect(() => {
    try {
      setReviewer(localStorage.getItem("shipdoc.reviewer") ?? "");
      setToken(sessionStorage.getItem("shipdoc.reviewToken") ?? "");
    } catch { /* storage unavailable: the fields simply start empty */ }
  }, []);

  const submit = async (decision: "confirmed" | "corrected") => {
    setBusy(true); setError(null);
    try {
      try { localStorage.setItem("shipdoc.reviewer", reviewer); sessionStorage.setItem("shipdoc.reviewToken", token); } catch { /* ignore */ }
      const body: Record<string, unknown> = { decision, reviewer, reviewer_note: note };
      if (decision === "corrected") {
        body.corrected_fields = { status, has_defect: status === "MISMATCH", defect_fields: status === "MISMATCH" ? [...defects].sort() : [],
                                  review_reason: status === "NEEDS_REVIEW" ? (ai.review_reason ?? "missing_value") : null };
      }
      const r = await fetch(`/api/report/${encodeURIComponent(reportKey)}/review`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-API-Key": token }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : `HTTP ${r.status}`);
      setDone(j.review); setMode("idle");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const credentials = (
    <div className="row">
      <div className="field"><label htmlFor="rv-name">Reviewer</label>
        <input id="rv-name" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="your name" autoComplete="name" /></div>
      <div className="field"><label htmlFor="rv-token">Review token</label>
        <input id="rv-token" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="asked once per session" autoComplete="off" /></div>
    </div>
  );

  return (
    <section className="card" aria-labelledby="h-review">
      <div className="card-head"><Icon name="person.crop.circle" size={20} /><h2 id="h-review">Human review</h2>
        <span className="tag">{done ? `reviewed by ${done.reviewer} · ${done.human_decision}` : "not yet reviewed"}</span></div>

      {done ? (
        <div className="stack-s" style={{ marginBottom: mode === "idle" ? 0 : "var(--s-6)" }}>
          <div className="chips">
            <Chip status={done.human_decision === "confirmed" ? "OK" : "NEEDS_REVIEW"}>{done.human_decision}</Chip>
            <Chip>{done.reviewer}</Chip>
            <Chip status={done.identity?.verified ? "OK" : undefined}>{done.identity?.verified ? "identity verified (OIDC)" : `identity asserted (${done.identity?.method ?? "key"})`}</Chip>
            <Chip>{fmtTime(done.reviewed_at)}</Chip>
          </div>
          <div className="kv">
            <span className="k">AI decision</span><span className="v">{done.original_ai_decision.status}{done.original_ai_decision.defect_fields.length ? ` · ${done.original_ai_decision.defect_fields.join(", ")}` : ""}{done.original_ai_decision.review_reason ? ` · ${done.original_ai_decision.review_reason}` : ""}</span>
            {Object.keys(done.corrections ?? {}).length ? <><span className="k">corrections</span><span className="v">{JSON.stringify(done.corrections)}</span></> : null}
            {done.reviewer_note ? <><span className="k">note</span><span className="v" style={{ fontFamily: "inherit", fontSize: 14 }}>{done.reviewer_note}</span></> : null}
            {done.request_id ? <><span className="k">request</span><span className="v">{done.request_id}</span></> : null}
          </div>
          <p className="meta">The AI decision above is kept verbatim; the record&apos;s effective decision is the corrected one. Audit trail stored under <code>reports/{"{key}"}.review</code>.</p>
        </div>
      ) : null}

      {mode === "idle" ? (
        <div className="actions">
          <button type="button" className="btn btn-primary" onClick={() => setMode("confirm")}><Icon name="checkmark.circle" />Confirm AI decision</button>
          <button type="button" className="btn btn-secondary" onClick={() => setMode("correct")}><Icon name="arrow.clockwise" />Correct</button>
          {done ? <span className="meta">Reviewing again replaces the audit entry with a new one (the AI decision stays).</span> : null}
        </div>
      ) : null}

      {mode === "confirm" ? (
        <div className="stack-s">
          <p className="hint"><Icon name="info.circle" /><span>You confirm that <b>{STATUS_LABEL[ai.status]}</b>{ai.defect_fields.length ? <> with defects <b>{ai.defect_fields.join(", ")}</b></> : null} is the right call.</span></p>
          {credentials}
          <div className="field"><label htmlFor="rv-note">Note (optional)</label><textarea id="rv-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} /></div>
          {error ? <p className="note"><Icon name="exclamationmark.triangle" /><span>{error}</span></p> : null}
          <div className="actions">
            <button type="button" className="btn btn-primary" disabled={busy || !reviewer} onClick={() => submit("confirmed")}><Icon name="checkmark.circle" />{busy ? "Saving…" : "Confirm"}</button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setMode("idle")}>Cancel</button>
          </div>
        </div>
      ) : null}

      {mode === "correct" ? (
        <div className="stack-s">
          <div className="row">
            <div className="field"><label htmlFor="rv-status">Status</label>
              <select id="rv-status" value={status} onChange={(e) => setStatus(e.target.value as Status)}>
                {STATUSES.map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
              </select></div>
            <div className="field"><span className="label">Defect fields {status !== "MISMATCH" ? <span className="meta">(only for Mismatch)</span> : null}</span>
              <div className="chips">
                {FIELD_KEYS.map((k) => (
                  <label key={k} className={`fchk ${defects.includes(k) ? "on" : ""}`} aria-disabled={status !== "MISMATCH"}>
                    <input type="checkbox" disabled={status !== "MISMATCH"} checked={defects.includes(k)}
                      onChange={(e) => setDefects(e.target.checked ? [...defects, k] : defects.filter((x) => x !== k))} />{k}
                  </label>))}
              </div></div>
          </div>
          {credentials}
          <div className="field"><label htmlFor="rv-note2">Why (note)</label><textarea id="rv-note2" value={note} onChange={(e) => setNote(e.target.value)} rows={2} placeholder="e.g. BL was amended by the carrier on 21 Sep" /></div>
          {error ? <p className="note"><Icon name="exclamationmark.triangle" /><span>{error}</span></p> : null}
          <div className="actions">
            <button type="button" className="btn btn-primary" disabled={busy || !reviewer || (status === "MISMATCH" && defects.length === 0)} onClick={() => submit("corrected")}><Icon name="checkmark.circle" />{busy ? "Saving…" : "Save correction"}</button>
            <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setMode("idle")}>Cancel</button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
