"use client";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Icon } from "./Icon";

/* Back to the inbox with the filters the reader came from (?from=<inbox query>). */
export function BackLink() {
  const from = useSearchParams().get("from");
  return (
    <Link href={from ? `/?${from}` : "/"} className="hint" style={{ width: "fit-content" }}>
      <Icon name="arrow.left" />Inbox
    </Link>
  );
}
