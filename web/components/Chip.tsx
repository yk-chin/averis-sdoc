import type { ReactNode } from "react";
import { tone } from "@/lib/format";

/** Status chip: the same classes as the demo page (ok / warn / bad / blue), dot + text. */
export function Chip({ status, children, small = false, className = "" }:
  { status?: string | null; children: ReactNode; small?: boolean; className?: string }) {
  const t = status !== undefined ? tone(status) : "";
  return <span className={`chip ${t} ${small ? "sm" : ""} ${className}`.trim()}>{children}</span>;
}
