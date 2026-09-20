"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import type { ReportRow, ReviewReason } from "@/lib/types";
import { INBOX_PREFIX } from "@/lib/api";
import { REASON_LABEL, isException, isIncomplete } from "@/lib/format";
import { Chip } from "./Chip";
import { Icon } from "./Icon";
import { SectionLabel } from "./SectionLabel";

/* Two queues, deliberately separate: the exception queue is work for a documentation specialist
   (something arrived and could not be read, recognised or decided); incomplete requests are work for
   whoever chases the sender (nothing usable was attached). Different owner, different action. */

const EXC: ReviewReason[] = ["unreadable", "wrong_doc_type", "missing_value"];

export function Queues({ initial }: { initial: ReportRow[] }) {
  const [rows, setRows] = useState(initial);
  const [at, setAt] = useState<Date | null>(null);
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(`/api/reports?limit=1000&prefix=${INBOX_PREFIX}&status=NEEDS_REVIEW`, { cache: "no-store" });
        if (r.ok && alive) { setRows((await r.json()).items); setAt(new Date()); }
      } catch { /* keep the last good list */ }
    };
    const t = setInterval(tick, 15000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  const exc = rows.filter((r) => isException(r.review_reason)).sort(byId);
  const inc = rows.filter((r) => isIncomplete(r.review_reason)).sort(byId);

  return (
    <>
      <div className="phead reveal">
        <div className="copy">
          <h1>Queues</h1>
          <p className="lead">Escalations split by what a person has to do — not one undifferentiated review pile.</p>
        </div>
        <div className="stats">
          <div className="stat warn"><span className="v">{rows.length}</span><span className="k">review</span></div>
          <div className="stat"><span className="v">{exc.length}</span><span className="k">exceptions</span></div>
          <div className="stat"><span className="v">{inc.length}</span><span className="k">incomplete</span></div>
          <div className="stat"><span className="v" style={{ fontSize: 15, fontWeight: 500, lineHeight: 1.6 }}>{at ? at.toLocaleTimeString("en-GB") : "server"}</span><span className="k">refreshed</span></div>
        </div>
      </div>

      <div className="grid-2 reveal" style={{ "--i": 1 } as React.CSSProperties}>
        <section className="card" aria-labelledby="h-exc">
          <SectionLabel id="h-exc" trailing={<span className="stat-inline">{exc.length}</span>}>Exception queue</SectionLabel>
          <p className="muted" style={{ marginBottom: "var(--s-4)" }}>Documents arrived but could not be read, were not an SI/BL, or left a field undecidable. A documentation specialist opens the report.</p>
          <div className="chips" style={{ marginBottom: "var(--s-4)" }}>
            {EXC.map((k) => <Chip key={k} small status="NEEDS_REVIEW">{REASON_LABEL[k]} · {exc.filter((r) => r.review_reason === k).length}</Chip>)}
          </div>
          <QueueTable rows={exc} empty="No exceptions — every attached document was read and decided." showAtt />
        </section>

        <section className="card" aria-labelledby="h-inc">
          <SectionLabel id="h-inc" trailing={<span className="stat-inline">{inc.length}</span>}>Incomplete requests</SectionLabel>
          <p className="muted" style={{ marginBottom: "var(--s-4)" }}>The body asks for a comparison but nothing usable was attached. Nothing to review — chase the sender for the files.</p>
          <div className="chips" style={{ marginBottom: "var(--s-4)" }}>
            <Chip small status="NEEDS_REVIEW">{REASON_LABEL.missing_attachment} · {inc.length}</Chip>
          </div>
          <QueueTable rows={inc} empty="No incomplete requests." showFrom />
        </section>
      </div>
    </>
  );
}

const byId = (a: ReportRow, b: ReportRow) => a.email_id.localeCompare(b.email_id, "en", { numeric: true });

function QueueTable({ rows, empty, showAtt = false, showFrom = false }: { rows: ReportRow[]; empty: string; showAtt?: boolean; showFrom?: boolean }) {
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th className="tight">email_id</th><th>{showFrom ? "From / subject" : "Subject"}</th><th>Why</th>{showAtt ? <th className="num tight">Att.</th> : null}<th /></tr></thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={5}><div className="empty"><Icon name="checkmark.circle" size={24} />{empty}</div></td></tr>
          ) : rows.map((r) => (
            <tr key={r.key}>
              <td className="mono tight"><Link className="row-link" href={`/emails/${r.email_id}`}>{r.email_id}</Link></td>
              <td>
                {showFrom ? <span className="meta">{r.from ?? "—"}</span> : null}
                <span className="cut" style={{ display: "block", maxWidth: "16ch", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.subject ?? ""}>{r.subject ?? "—"}</span>
              </td>
              <td className="wrap">
                {r.review_reason ? <Chip small status="NEEDS_REVIEW">{REASON_LABEL[r.review_reason]}</Chip> : null}
                {r.review_detail.length ? <span className="meta">{r.review_detail.join(" · ")}</span> : null}
              </td>
              {showAtt ? <td className="num tight">{r.attachments}</td> : null}
              <td><Link href={`/emails/${r.email_id}`} aria-label={`Open ${r.email_id}`}><Icon name="chevron.right" /></Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
