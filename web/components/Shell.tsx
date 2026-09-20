import type { ReactNode } from "react";
import Link from "next/link";
import { fetchHealth } from "@/lib/api";
import { Icon } from "./Icon";
import { NavLinks } from "./NavLinks";

export async function Shell({ children }: { children: ReactNode }) {
  const h = await fetchHealth();
  return (
    <>
      <header className="top">
        <div className="page">
          <Link href="/" className="brand" aria-label="ShipDoc home">
            <span className="name">ShipDoc</span>
            <span className="ver">{h ? `v${h.version} · ${h.llm_provider} · ${h.store}${h.chaos_enabled ? " · CHAOS ON" : ""}` : "api offline"}</span>
          </Link>
          <NavLinks />
        </div>
      </header>
      <main className="main"><div className="page">{children}</div></main>
      <footer className="page footer meta">
        <span>ShipDoc · Averis × Monash Hackathon 2026</span>
        <span className="hint" style={{ fontSize: 12 }}><Icon name="info.circle" />Every verdict carries its raw value, normalised value and reason.</span>
      </footer>
    </>
  );
}
