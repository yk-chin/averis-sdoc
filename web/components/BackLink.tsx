"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Icon } from "./Icon";

/* Back to the inbox *with the filters the reader came from* (?from=<inbox query>). */
export function BackLink() {
  const sp = useSearchParams();
  const from = sp.get("from");
  const href = from ? `/?${from}` : "/";
  return (
    <Link href={href} className="hint" style={{ width: "fit-content" }}>
      <Icon name="arrow.left" />Inbox
    </Link>
  );
}
