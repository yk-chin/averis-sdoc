"use client";
import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { CATEGORIES, STATUSES, type Category, type ReportRow, type Status } from "@/lib/types";
import { CATEGORY_LABEL, REASON_LABEL, STATUS_LABEL } from "@/lib/format";
import { Chip } from "./Chip";
import { Icon } from "./Icon";

export function Inbox({ rows }: { rows: ReportRow[] }) {
  const router = useRouter();
  const [cat, setCat] = useState<Category | null>(null);
  const [status, setStatus] = useState<Status | null>(null);
  const [q, setQ] = useState("");

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const r of rows) if (r.category) c[r.category] = (c[r.category] ?? 0) + 1;
    return c;
  }, [rows]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return rows
      .filter((r) => !cat || r.category === cat)
      .filter((r) => !status || r.status === status)
      .filter((r) => !needle || [r.email_id, r.subject, r.from].some((v) => (v ?? "").toLowerCase().includes(needle)))
      .sort((a, b) => a.email_id.localeCompare(b.email_id, "en", { numeric: true }));
  }, [rows, cat, status, q]);

  return (
    <section className="reveal" style={{ "--i": 1 } as React.CSSProperties}>
      <div className="filters">
        <label className="search">
          <Icon name="magnifyingglass" />
          <span className="sr">Search</span>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search email_id, subject, sender" spellCheck={false} />
        </label>
        <div className="seg" role="group" aria-label="Status">
          <button type="button" aria-pressed={status === null} onClick={() => setStatus(null)}>All</button>
          {STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setStatus(status === s ? null : s)}>{STATUS_LABEL[s]}</button>
          ))}
        </div>
      </div>
      <div className="chipbar" role="group" aria-label="Category" style={{ marginBottom: "var(--s-6)" }}>
        <button type="button" className="fchip" aria-pressed={cat === null} onClick={() => setCat(null)}>All <span className="n">{rows.length}</span></button>
        {CATEGORIES.map((c) => (
          <button key={c} type="button" className="fchip" aria-pressed={cat === c} onClick={() => setCat(cat === c ? null : c)}>
            {CATEGORY_LABEL[c]} <span className="n">{counts[c] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="table-wrap inbox">
        <table>
          <colgroup><col style={{ width: "11%" }} /><col style={{ width: "18%" }} /><col style={{ width: "29%" }} /><col style={{ width: "13%" }} /><col style={{ width: "17%" }} /><col style={{ width: "6%" }} /><col style={{ width: "6%" }} /></colgroup>
          <thead>
            <tr><th>email_id</th><th>From</th><th>Subject</th><th>Category</th><th>Status</th><th className="num">Att.</th><th>Decided by</th></tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={7}><div className="empty"><Icon name="tray" size={24} />No emails match these filters.</div></td></tr>
            ) : shown.map((r) => (
              <tr key={r.key} className="link" onClick={() => router.push(`/emails/${r.email_id}`)}>
                <td className="mono"><Link className="row-link" href={`/emails/${r.email_id}`} onClick={(e) => e.stopPropagation()}>{r.email_id}</Link></td>
                <td className="cut" title={r.from ?? ""}>{r.from ?? "—"}</td>
                <td className="cut" title={r.subject ?? ""}>{r.subject ?? "—"}</td>
                <td>{r.category ? <Chip small>{CATEGORY_LABEL[r.category]}</Chip> : <Chip small status={r.report_status}>{r.report_status}</Chip>}</td>
                <td>
                  <span className="chips">
                    {r.status ? <Chip small status={r.status}>{STATUS_LABEL[r.status]}</Chip> : null}
                    {r.review_reason ? <Chip small>{REASON_LABEL[r.review_reason]}</Chip> : null}
                    {r.defect_fields.length ? <Chip small status="MISMATCH">{r.defect_fields.join(", ")}</Chip> : null}
                  </span>
                </td>
                <td className="num">{r.attachments ? <><Icon name="paperclip" /> {r.attachments}</> : <span className="meta">—</span>}</td>
                <td className="muted">{r.decided_by ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="meta" style={{ marginTop: "var(--s-3)" }}>{shown.length} of {rows.length} emails · click a row for the full report</p>
    </section>
  );
}
