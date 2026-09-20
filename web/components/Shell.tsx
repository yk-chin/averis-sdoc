import type { ReactNode } from "react";
import Link from "next/link";
import { fetchHealth } from "@/lib/api";
import { Icon } from "./Icon";
import { NavLinks } from "./NavLinks";
import { Wordmark } from "./Wordmark";

/* App shell: sticky frosted header — wordmark, hairline, descriptor, pill nav (a bottom tab bar on phones). */
export async function Shell({ children }: { children: ReactNode }) {
  const h = await fetchHealth();
  return (
    <>
      <header className="top">
        <div className="page">
          <Link href="/" aria-label="ShipDoc home" style={{ borderRadius: 8 }}><Wordmark /></Link>
          <span aria-hidden className="divider" />
          <span className="descriptor">Shipping-document intake</span>
          <NavLinks />
        </div>
      </header>
      <main className="main"><div className="page">{children}</div></main>
      <footer className="page footer meta">
        <span>ShipDoc · Averis × Monash Hackathon 2026{h ? <> · API v{h.version} · {h.llm_provider} · {h.store}{h.chaos_enabled ? " · CHAOS ON" : ""}</> : " · API offline"}</span>
        <span className="hint" style={{ fontSize: 13 }}><Icon name="info.circle" />Every verdict carries its raw value, normalised value and reason.</span>
      </footer>
    </>
  );
}
