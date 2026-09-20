import type { Metadata } from "next";
import { Suspense } from "react";
import { notFound } from "next/navigation";
import { fetchReport } from "@/lib/api";
import { CATEGORY_LABEL, REASON_LABEL, STATUS_LABEL, fmtRaw, fmtTime } from "@/lib/format";
import type { AttachmentEvidence } from "@/lib/types";
import { BackLink } from "@/components/BackLink";
import { Board } from "@/components/Board";
import { Chip } from "@/components/Chip";
import { Icon } from "@/components/Icon";
import { SectionLabel } from "@/components/SectionLabel";

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
  const n = (o: string) => fields.filter((f) => f.outcome === o).length;

  return (
    <div className="sections">
      <header className="stack-s reveal">
        <Suspense fallback={<span className="hint"><Icon name="arrow.left" />Inbox</span>}><BackLink /></Suspense>
        <h1 style={{ fontSize: 32, lineHeight: 1.15, letterSpacing: "-.02em" }}>{email?.subject || rep.email_id}</h1>
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
          <SectionLabel id="h-board" trailing={<span className="chips"><Chip small status="MISMATCH">{n("mismatch")} mismatch</Chip><Chip small status="normalized">{n("normalized")} normalised</Chip><Chip small status="exact">{n("exact")} exact</Chip></span>}>SI vs. draft BL</SectionLabel>
          <Board fields={fields} />
        </section>
      ) : null}

      {ev?.attachments ? (
        <section className="card reveal" style={{ "--i": 2 } as React.CSSProperties} aria-labelledby="h-att">
          <SectionLabel id="h-att" trailing={`${email?.attachments?.length ?? 0} file(s)`}>Attachments</SectionLabel>
          <div className="att">
            <AttCard slot="SI" title="Shipping Instruction" a={ev.attachments.SI} />
            <AttCard slot="BL" title="Draft Bill of Lading" a={ev.attachments.BL} />
          </div>
          {email?.attachments?.length ? <p className="meta" style={{ marginTop: "var(--s-3)" }}>files: {email.attachments.join(", ")}</p> : null}
        </section>
      ) : null}

      <section className="grid-2 reveal" style={{ "--i": 3 } as React.CSSProperties}>
        <div className="card">
          <SectionLabel>Email</SectionLabel>
          <pre className="body">{email?.body || "(body not stored)"}</pre>
        </div>
        <div className="card">
          <SectionLabel trailing={cls?.llm ? <Chip small status="normalized">LLM consulted</Chip> : <Chip small status="OK">rules only</Chip>}>Classification basis</SectionLabel>
          {cls ? (
            <div className="kv">
              <span className="k">rules</span><span className="v">{cls.rule_category} · {cls.rule_confidence.toFixed(2)}</span>
              <span className="k">LLM</span><span className="v">{cls.llm ? `${cls.llm.category} · ${cls.llm.confidence.toFixed(2)}` : "not consulted (rules were confident)"}</span>
              {cls.llm ? <><span className="k">reason</span><span className="v" style={{ fontFamily: "inherit", fontSize: 15 }}>{cls.llm.reason}</span></> : null}
              {cls.confidence !== undefined ? <><span className="k">final</span><span className="v">{cls.confidence.toFixed(2)}</span></> : null}
            </div>
          ) : <p className="muted">No classification recorded.</p>}
          {ev?.report_text ? <><SectionLabel>Report</SectionLabel><pre>{ev.report_text}</pre></> : null}
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
      ) : <p className="meta">no fields extracted</p>}
    </div>
  );
}
