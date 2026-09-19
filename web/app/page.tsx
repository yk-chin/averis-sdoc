import type { Metadata } from "next";
import { fetchReports } from "@/lib/api";
import { Inbox } from "@/components/Inbox";

export const metadata: Metadata = { title: "Inbox" };
export const revalidate = 15;

export default async function InboxPage() {
  const { items } = await fetchReports();
  const n = (s: string) => items.filter((r) => r.status === s).length;
  return (
    <>
      <div className="phead reveal">
        <div className="copy">
          <h1>Inbox</h1>
          <p className="lead">Every email the pipeline has seen: classified, compared where an SI and a draft BL were attached, escalated only when a person must look.</p>
        </div>
        <div className="stats" aria-label="Totals">
          <div className="stat"><span className="v">{items.length}</span><span className="k">emails</span></div>
          <div className="stat ok"><span className="v">{n("OK")}</span><span className="k">OK</span></div>
          <div className="stat bad"><span className="v">{n("MISMATCH")}</span><span className="k">mismatch</span></div>
          <div className="stat warn"><span className="v">{n("NEEDS_REVIEW")}</span><span className="k">needs review</span></div>
        </div>
      </div>
      <Inbox rows={items} />
    </>
  );
}
