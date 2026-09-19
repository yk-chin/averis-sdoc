import type { Category, Outcome, ReviewReason, Status } from "./types";

export const CATEGORY_LABEL: Record<Category, string> = {
  BL_COMPARISON: "BL comparison", SI_REQUEST: "SI request", INVOICE_QUERY: "Invoice query", GENERAL: "General", SPAM: "Spam",
};
export const STATUS_LABEL: Record<Status, string> = { OK: "OK", MISMATCH: "Mismatch", NEEDS_REVIEW: "Needs review" };
export const REASON_LABEL: Record<ReviewReason, string> = {
  missing_attachment: "missing attachment", unreadable: "unreadable", wrong_doc_type: "wrong document type", missing_value: "missing value",
};
export const OUTCOME_LABEL: Record<Outcome, string> = {
  exact: "exact", normalized: "normalised", mismatch: "mismatch", undetermined: "undetermined", missing_si: "missing on SI", missing_bl: "missing on BL",
};

/** Chip tone for the API's status vocabularies (same classes as the demo page). */
export function tone(s: string | null | undefined): "" | "ok" | "warn" | "bad" | "blue" {
  switch (s) {
    case "OK": case "DONE": case "exact": return "ok";
    case "MISMATCH": case "FAILED": case "mismatch": return "bad";
    case "NEEDS_REVIEW": case "undetermined": case "missing_si": case "missing_bl": return "warn";
    case "QUEUED": case "PROCESSING": case "RETRYING": case "normalized": return "blue";
    default: return "";
  }
}

/** Exception queue = something arrived but cannot be read / recognised / decided; Incomplete = nothing usable attached. */
export const isIncomplete = (r: ReviewReason | null) => r === "missing_attachment";
export const isException = (r: ReviewReason | null) => r !== null && r !== "missing_attachment";

/** Render a normalised value the way a person reads it. Weights come back as "22000.0" (kg), counts as "3". */
export function fmtNorm(field: string, v: string | null): string {
  if (v === null || v === undefined || v === "") return "—";
  if (field === "gross_weight_kg") {
    const n = Number(v);
    return Number.isFinite(n) ? `${n.toLocaleString("en-US", { maximumFractionDigits: 2 })} kg` : v;
  }
  if (field === "container_count") {
    const n = Number(v);
    return Number.isFinite(n) ? n.toLocaleString("en-US") : v;
  }
  return v;
}

export const fmtRaw = (v: string | null) => (v === null || v === undefined || v === "" ? "—" : v);

export const fmtPct = (x: number, digits = 1) => `${(x * 100).toFixed(digits)}%`;
export const fmtScore = (x: number) => x.toFixed(4);
export const fmtTime = (unix: number) => new Date(unix * 1000).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short" });
