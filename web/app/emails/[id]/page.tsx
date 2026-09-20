import type { Metadata } from "next";
import { Suspense } from "react";
import { notFound } from "next/navigation";
import { fetchReport } from "@/lib/api";
import { CATEGORY_LABEL, REASON_LABEL, STATUS_LABEL, fmtRaw, fmtTime } from "@/lib/format";
import type { AttachmentEvidence } from "@/lib/types";
import { Board } from "@/components/Board";
import { Chip } from "@/components/Chip";
import { Icon } from "@/components/Icon";
import { BackLink } from "@/components/BackLink";
import { ReviewPanel } from "@/components/ReviewPanel";

export const revalidate = 15;

export async function generateMetadata({ params }: { params: Promise<{ id: string }> }): Promise<Metadata> {
  const { id } = await params;
  return { title: id };
}

export default async function EmailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const rep = await fetchReport(id);
  if (!rep) notFound();
  const res = rep.result;
  const d = rep.effective_decision ?? res?.decision ?? null;
  const ev = res?.evidence;
  const fields = ev?.fields ?? [];
  const cls = ev?.classification;
  const detail = res?.review_detail ?? [];
  const email = rep.email;

  return (
    <div className="sections">
      <header className="stack-s reveal">
        <Suspense fallback={<span className="hint" style={{ width: "fit-content" }}><Icon name="arrow.left" />Inbox</span>}><BackLink /></Suspense>
        <h1 style={{ fontSize: 32 }}>{email?.subject || rep.email_id}</h1>
        <p className="muted">
          <span className="mono">{rep.email_id}</span>{email?.from ? <> · from {email.from}</> : null} · processed {fmtTime(rep.updated)}
          {rep.review ? <> · reviewed by {rep.review.reviewer} ({rep.review.human_decision})</> : null}
        </p>
        {d ? (
          <div className="chips">
            <Chip>{CATEGORY_LABEL[d.category]}</Chip>
            <Chip status={d.status}>{STATUS_LABEL[d.status]}</Chip>
            {d.review_reason ? <Chip status="NEEDS_REVIEW">reason: {REASON_LABEL[d.review_reason]}</Chip> : null}
            {d.defect_fields.length ? <Chip status="MISMATCH">defects: {d.defect_fields.join(", ")}</Chip> : null}
            <Chip>decided by {d.decided_by}</Chip>
            {ev?.decision_confidence !== undefined ? <Chip>confidence {ev.decision_confidence.toFixed(2)}</Chip> : null}
          </div>
        ) : <Chip status={rep.status}>{rep.status}{rep.error ? ` · ${rep.error}` : ""}</Chip>}
        {detail.length ? <p className="note"><Icon name="exclamationmark.triangle" /><span>{detail.join(" · ")}</span></p> : null}
      </header>

      {fields.length ? (
        <section className="card reveal" style={{ "--i": 1 } as React.CSSProperties} aria-labelledby="h-board">
          <div className="card-head"><span className="numeral">01</span><h2 id="h-board">SI vs. draft BL</h2><span className="tag">{fields.filter((f) => f.outcome === "mismatch").length} mismatch · {fields.filter((f) => f.outcome === "normalized").length} normalised · {fields.filter((f) => f.outcome === "exact").length} exact</span></div>
          <Board fields={fields} />
        </section>
      ) : null}

      {ev?.provisional ? (
        <section className="card reveal" style={{ "--i": 1 } as React.CSSProperties} aria-labelledby="h-prov">
          <div className="card-head"><span className="numeral">01</span><h2 id="h-prov">Provisional comparison (vision proposal)</h2><span className="tag">confidence capped at 0.60 · cannot auto-pass</span></div>
          <p className="note" style={{ marginBottom: "var(--s-4)" }}><Icon name="exclamationmark.triangle" /><span>{ev.provisional.note}</span></p>
          <Board fields={ev.provisional.fields} />
        </section>
      ) : null}

      {res?.decision ? (
        <div className="reveal" style={{ "--i": 2 } as React.CSSProperties}>
          <ReviewPanel reportKey={rep.key} ai={res.decision} review={rep.review ?? null} />
        </div>
      ) : null}

      {ev?.attachments ? (
        <section className="card reveal" style={{ "--i": 2 } as React.CSSProperties} aria-labelledby="h-att">
          <div className="card-head"><span className="numeral">{fields.length || ev?.provisional ? "02" : "01"}</span><h2 id="h-att">Attachments</h2><span className="tag">{email?.attachments?.length ?? 0} file(s)</span></div>
          <div className="att">
            <AttCard slot="SI" title="Shipping Instruction" a={ev.attachments.SI} />
            <AttCard slot="BL" title="Draft Bill of Lading" a={ev.attachments.BL} />
          </div>
          {email?.attachments?.length ? <p className="meta" style={{ marginTop: "var(--s-3)" }}>files: {email.attachments.join(", ")}</p> : null}
        </section>
      ) : null}

      <section className="grid-2 reveal" style={{ "--i": 3 } as React.CSSProperties}>
        <div className="card stack-s">
          <h3>Email</h3>
          <pre className="body">{email?.body || "(body not stored)"}</pre>
        </div>
        <div className="card stack-s">
          <h3>Classification basis</h3>
          {cls ? (
            <div className="kv">
              <span className="k">rules</span><span className="v">{cls.rule_category} · {cls.rule_confidence.toFixed(2)}</span>
              <span className="k">LLM</span><span className="v" style={cls.llm ? undefined : { fontFamily: "inherit", fontSize: 14 }}>{cls.llm ? `${cls.llm.category} · ${cls.llm.confidence.toFixed(2)}` : `not needed here — the rules were confident (${cls.rule_confidence.toFixed(2)}). Gemini classifies the emails they are not sure about: 55% of this inbox.`}</span>
              {cls.llm ? <><span className="k">reason</span><span className="v" style={{ fontFamily: "inherit", fontSize: 14 }}>{cls.llm.reason}</span></> : null}
              {cls.confidence !== undefined ? <><span className="k">final</span><span className="v">{cls.confidence.toFixed(2)}</span></> : null}
            </div>
          ) : <p className="muted">No classification recorded.</p>}
          {ev?.report_text ? <><h3 style={{ marginTop: "var(--s-3)" }}>Report</h3><pre>{ev.report_text}</pre></> : null}
        </div>
      </section>
    </div>
  );
}

function AttCard({ slot, title, a }: { slot: string; title: string; a: AttachmentEvidence | null }) {
  if (!a) {
    return <div className="card stack-s"><h3>{title}</h3><p className="hint"><Icon name="xmark.circle" />not attached</p></div>;
  }
  const entries = Object.entries(a.fields);
  return (
    <div className="card stack-s">
      <h3>{title}</h3>
      <div className="chips">
        <Chip small>{a.source}</Chip>
        <Chip small status={a.doc_type === slot ? "OK" : "MISMATCH"}>type {a.doc_type ?? "unknown"}</Chip>
        <Chip small status={a.readable ? "OK" : "NEEDS_REVIEW"}>{a.readable ? "readable" : "unreadable"}</Chip>
      </div>
      {entries.length ? (
        <div className="kv">
          {entries.map(([k, v]) => <span key={k} style={{ display: "contents" }}><span className="k">{k}</span><span className="v">{fmtRaw(v)}</span></span>)}
        </div>
      ) : <p className="meta">no fields extracted by the deterministic parser</p>}
      {a.vision ? <Vision v={a.vision} /> : null}
    </div>
  );
}

function Vision({ v }: { v: NonNullable<AttachmentEvidence["vision"]> }) {
  if (v.status !== "proposal") {
    return <p className="hint" style={{ marginTop: "var(--s-2)" }}><Icon name="xmark.circle" /><span>Vision: {v.reason ?? v.status}</span></p>;
  }
  const entries = Object.entries(v.fields ?? {});
  return (
    <div className="stack-s" style={{ marginTop: "var(--s-2)" }}>
      <div className="chips">
        <Chip small status="normalized">vision proposal · {v.model}</Chip>
        <Chip small status="NEEDS_REVIEW">confidence ≤ {(v.max_confidence ?? 0.6).toFixed(2)} — a person confirms</Chip>
        {v.document_type ? <Chip small>reads as {v.document_type}</Chip> : null}
      </div>
      <div className="kv">
        {entries.map(([k, val]) => <span key={k} style={{ display: "contents" }}><span className="k">{k}</span><span className="v">{fmtRaw(val)} <span className="meta" style={{ display: "inline" }}>({(v.confidence?.[k] ?? 0).toFixed(2)})</span></span></span>)}
      </div>
    </div>
  );
}
