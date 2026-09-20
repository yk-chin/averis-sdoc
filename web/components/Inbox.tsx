"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { CATEGORIES, STATUSES, type Category, type ReportRow, type Status } from "@/lib/types";
import { CATEGORY_LABEL, REASON_LABEL, STATUS_LABEL } from "@/lib/format";
import { Chip } from "./Chip";
import { Icon } from "./Icon";

/* Filters live in the URL (?category=&status=&q=) so Back, reload and shared links all keep them.
   Rows carry the current query along (?from=…) so the detail page can return to the same view. */

const isCat = (v: string | null): v is Category => !!v && (CATEGORIES as string[]).includes(v);
const isStatus = (v: string | null): v is Status => !!v && (STATUSES as string[]).includes(v);

export function Inbox({ rows }: { rows: ReportRow[] }) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();
  const cat = isCat(sp.get("category")) ? (sp.get("category") as Category) : null;
  const status = isStatus(sp.get("status")) ? (sp.get("status") as Status) : null;
  const q = sp.get("q") ?? "";
  const [draft, setDraft] = useState(q);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const setParams = (patch: Record<string, string | null>) => {
    const next = new URLSearchParams(sp.toString());
    for (const [k, v] of Object.entries(patch)) (v ? next.set(k, v) : next.delete(k));
    const s = next.toString();
    router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false });
  };
  useEffect(() => setDraft(q), [q]);
  const onSearch = (v: string) => {
    setDraft(v);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setParams({ q: v.trim() || null }), 150);
  };
  const from = sp.toString();
  const detail = (id: string) => `/emails/${id}${from ? `?from=${encodeURIComponent(from)}` : ""}`;

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
          <input value={draft} onChange={(e) => onSearch(e.target.value)} placeholder="Search email_id, subject, sender" spellCheck={false} />
        </label>
        <div className="seg" role="group" aria-label="Status">
          <button type="button" aria-pressed={status === null} onClick={() => setParams({ status: null })}>All</button>
          {STATUSES.map((s) => (
            <button key={s} type="button" aria-pressed={status === s} onClick={() => setParams({ status: status === s ? null : s })}>{STATUS_LABEL[s]}</button>
          ))}
        </div>
      </div>
      <div className="chipbar" role="group" aria-label="Category" style={{ marginBottom: "var(--s-6)" }}>
        <button type="button" className="fchip" aria-pressed={cat === null} onClick={() => setParams({ category: null })}>All <span className="n">{rows.length}</span></button>
        {CATEGORIES.map((c) => (
          <button key={c} type="button" className="fchip" aria-pressed={cat === c} onClick={() => setParams({ category: cat === c ? null : c })}>
            {CATEGORY_LABEL[c]} <span className="n">{counts[c] ?? 0}</span>
          </button>
        ))}
      </div>

      <div className="table-wrap inbox">
        <table>
          <thead>
            <tr><th className="id">email_id</th><th>From</th><th>Subject</th><th className="tight">Category</th><th>Status</th><th className="num tight">Att.</th><th className="decided tight">Decided by</th></tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={7}><div className="empty"><Icon name="tray" size={24} />No emails match these filters.</div></td></tr>
            ) : shown.map((r) => (
              <tr key={r.key} className="link" onClick={() => router.push(detail(r.email_id))}>
                <td className="id mono tight"><Link className="row-link" href={detail(r.email_id)} onClick={(e) => e.stopPropagation()}>{r.email_id}</Link></td>
                <td><span className="cut" title={r.from ?? ""}>{r.from ?? "—"}</span></td>
                <td><span className="cut wide" title={r.subject ?? ""}>{r.subject ?? "—"}</span></td>
                <td className="tight">{r.category ? <Chip small>{CATEGORY_LABEL[r.category]}</Chip> : <Chip small status={r.report_status}>{r.report_status}</Chip>}</td>
                <td>
                  <span className="chips" style={{ gap: 4 }}>
                    {r.status ? <Chip small status={r.status}>{STATUS_LABEL[r.status]}</Chip> : null}
                    {r.review_reason ? <Chip small>{REASON_LABEL[r.review_reason]}</Chip> : null}
                    {r.defect_fields.length ? <Chip small status="MISMATCH">{r.defect_fields.join(", ")}</Chip> : null}
                    {r.decided_by ? <Chip small className="decided-chip">by {r.decided_by}</Chip> : null}
                  </span>
                </td>
                <td className="num tight">{r.attachments ? <span className="att-n"><Icon name="paperclip" />{r.attachments}</span> : <span className="meta">—</span>}</td>
                <td className="decided tight muted">{r.decided_by ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="meta" style={{ marginTop: "var(--s-3)" }}>{shown.length} of {rows.length} emails · click a row for the full report</p>
    </section>
  );
}
