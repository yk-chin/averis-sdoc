// Wire shapes of the ShipDoc API (api/main.py). Values are exactly what the backend emits.

export type Category = "BL_COMPARISON" | "SI_REQUEST" | "INVOICE_QUERY" | "GENERAL" | "SPAM";
export type Status = "OK" | "MISMATCH" | "NEEDS_REVIEW";
export type ReviewReason = "wrong_doc_type" | "missing_attachment" | "unreadable" | "missing_value";
export type Outcome = "exact" | "normalized" | "mismatch" | "undetermined" | "missing_si" | "missing_bl";

export const CATEGORIES: Category[] = ["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"];
export const STATUSES: Status[] = ["OK", "MISMATCH", "NEEDS_REVIEW"];

/** One slim row from GET /reports. */
export interface ReportRow {
  key: string;
  email_id: string;
  from: string | null;
  subject: string | null;
  attachments: number;
  report_status: "QUEUED" | "PROCESSING" | "RETRYING" | "DONE" | "FAILED";
  category: Category | null;
  status: Status | null;
  review_reason: ReviewReason | null;
  review_detail: string[];
  has_defect: boolean;
  defect_fields: string[];
  decided_by: "rule" | "llm" | null;
  updated: number;
}

export interface Decision {
  category: Category;
  status: Status;
  review_reason: ReviewReason | null;
  has_defect: boolean;
  defect_fields: string[];
  decided_by: "rule" | "llm";
}

export interface FieldResult {
  field: string;
  label: string;
  outcome: Outcome;
  severity: string;
  si_raw: string | null;
  bl_raw: string | null;
  si_norm: string | null;
  bl_norm: string | null;
  reason: string;
  confidence: number;
  needs_review: boolean;
}

export interface AttachmentEvidence {
  source: string;            // text | pdf | docx | xlsx | unreadable | <ext>
  doc_type: string | null;   // SI | BL | INVOICE | PACKING | COO | UNKNOWN
  readable: boolean;
  fields: Record<string, string | null>;
}

export interface Evidence {
  classification: { rule_category: Category; rule_confidence: number; confidence?: number;
                    llm: { category: Category; confidence: number; reason: string } | null };
  attachments?: { SI: AttachmentEvidence | null; BL: AttachmentEvidence | null };
  fields: FieldResult[];
  report_text?: string;
  decision_confidence?: number;
  review_reasons?: string[];
  review_detail?: string[];
}

export interface ProcessResult {
  email_id: string;
  decision: Decision;
  review_detail: string[];
  evidence: Evidence;
  elapsed_ms: number;
  version: string;
}

/** GET /report/{id} */
export interface Report {
  key: string;
  email_id: string;
  status: ReportRow["report_status"];
  attempts: number;
  error: string | null;
  result: ProcessResult | null;
  email?: { from: string | null; subject: string | null; body: string | null; attachments: string[] };
  review?: { original_ai_decision: Decision; human_decision: "confirmed" | "corrected"; corrections: Partial<Decision>;
             reviewer_note: string; reviewer: string; reviewed_at: number };
  effective_decision: Decision | null;
  updated: number;
}

export interface Health {
  status: string; version: string; uptime_s: number; llm_provider: string;
  tasks_mode: string; store: string; chaos_enabled: boolean;
}
