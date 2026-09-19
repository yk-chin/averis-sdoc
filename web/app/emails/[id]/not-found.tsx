import Link from "next/link";
import { Icon } from "@/components/Icon";

export default function NotFound() {
  return (
    <div className="card empty">
      <Icon name="tray" size={24} />
      No report with that email_id.
      <div style={{ marginTop: "var(--s-3)" }}><Link href="/">Back to the inbox</Link></div>
    </div>
  );
}
