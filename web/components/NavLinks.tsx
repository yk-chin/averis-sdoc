"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Icon, type IconName } from "./Icon";

const LINKS: { href: string; label: string; icon: IconName }[] = [
  { href: "/", label: "Inbox", icon: "envelope" },
  { href: "/queues", label: "Queues", icon: "tray.2" },
  { href: "/eval", label: "Eval", icon: "chart.bar" },
];

export function NavLinks() {
  const path = usePathname();
  const current = (href: string) => (href === "/" ? path === "/" || path.startsWith("/emails") : path.startsWith(href));
  return (
    <nav className="nav" aria-label="Console">
      {LINKS.map((l) => (
        <Link key={l.href} href={l.href} aria-current={current(l.href) ? "page" : undefined}>
          <Icon name={l.icon} />{l.label}
        </Link>
      ))}
    </nav>
  );
}
