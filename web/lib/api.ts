import type { Health, Report, ReportRow } from "./types";

// Server components talk to Cloud Run directly; browser code uses the /api/* rewrite (next.config.ts).
export const API_BASE = (process.env.API_BASE || "https://shipdoc-api-705106212012.asia-southeast1.run.app").replace(/\/$/, "");

/** Only the organiser's inbox (email_001 … email_520); demo-page rows (demo-001, healthy-…) stay out. */
export const INBOX_PREFIX = "email_";

async function get<T>(path: string, revalidate = 15): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { next: { revalidate } });
  if (!r.ok) throw new Error(`${r.status} on ${path}`);
  return r.json() as Promise<T>;
}

export const fetchReports = () =>
  get<{ count: number; items: ReportRow[] }>(`/reports?limit=1000&prefix=${INBOX_PREFIX}`);

export async function fetchReport(id: string): Promise<Report | null> {
  const r = await fetch(`${API_BASE}/report/${encodeURIComponent(id)}`, { next: { revalidate: 15 } });
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`${r.status} on /report/${id}`);
  return r.json() as Promise<Report>;
}

export const fetchHealth = () => get<Health>("/health", 60).catch(() => null);
