"use client";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { FieldResult } from "@/lib/types";
import { OUTCOME_LABEL, fmtNorm, fmtRaw } from "@/lib/format";
import { Chip } from "./Chip";
import { Icon } from "./Icon";

/* The comparison board: seven fields, SI and BL side by side. Every value is a trigger that shows
   raw → normalised, the comparator's reason and its confidence. "Show normalisation" expands the same
   information inline for every row (projectors have no mouse). */

const STORE_KEY = "shipdoc.showNorm";

interface Pop { r: FieldResult; side: "SI" | "BL"; x: number; y: number; below: boolean }

export function Board({ fields }: { fields: FieldResult[] }) {
  const [showNorm, setShowNorm] = useState(false);
  const [pop, setPop] = useState<Pop | null>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const popId = useId();

  useEffect(() => {
    try { setShowNorm(localStorage.getItem(STORE_KEY) === "1"); } catch { /* private mode etc. */ }
  }, []);
  const toggle = (v: boolean) => { setShowNorm(v); try { localStorage.setItem(STORE_KEY, v ? "1" : "0"); } catch { /* ignore */ } };

  useEffect(() => {
    if (!pop) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent && e.key !== "Escape") return;
      if (e instanceof PointerEvent && boardRef.current?.contains(e.target as Node) && (e.target as HTMLElement).closest(".val")) return;
      setPop(null);
    };
    document.addEventListener("keydown", close);
    document.addEventListener("pointerdown", close);
    window.addEventListener("scroll", () => setPop(null), { once: true, passive: true });
    return () => { document.removeEventListener("keydown", close); document.removeEventListener("pointerdown", close); };
  }, [pop]);

  const open = (r: FieldResult, side: "SI" | "BL", el: HTMLElement) => {
    const b = el.getBoundingClientRect();
    const below = b.bottom + 220 < window.innerHeight;
    setPop({ r, side, x: Math.min(Math.max(16, b.left), window.innerWidth - 16 - Math.min(360, window.innerWidth - 32)),
             y: below ? b.bottom + 8 : b.top - 8, below });
  };

  const Value = ({ r, side }: { r: FieldResult; side: "SI" | "BL" }) => {
    const raw = side === "SI" ? r.si_raw : r.bl_raw;
    const norm = side === "SI" ? r.si_norm : r.bl_norm;
    const empty = raw === null || raw === "";
    const isOpen = pop?.r.field === r.field && pop?.side === side;
    return (
      <button type="button" className={`val ${empty ? "is-empty" : ""}`} aria-expanded={isOpen} aria-controls={isOpen ? popId : undefined}
        onMouseEnter={(e) => open(r, side, e.currentTarget)} onFocus={(e) => open(r, side, e.currentTarget)}
        onClick={(e) => (isOpen ? setPop(null) : open(r, side, e.currentTarget))}
        aria-label={`${side} ${r.label}: ${fmtRaw(raw)}, normalised ${fmtNorm(r.field, norm)}`}>
        <span className="raw">{fmtRaw(raw)}</span>
        {raw !== null && norm === raw
          ? <span className="norm same"><Icon name="equal.circle" />identical, compared as text</span>
          : <span className="norm"><Icon name="arrow.right" />{fmtNorm(r.field, norm)}</span>}
      </button>
    );
  };

  return (
    <div>
      <div ref={boardRef} className={`board ${showNorm ? "show-norm" : ""}`} onMouseLeave={() => { if (!document.activeElement?.classList.contains("val")) setPop(null); }}>
        <table>
          <thead>
            <tr><th>Field</th><th className="side">Shipping Instruction</th><th className="side">Draft Bill of Lading</th><th className="out">Verdict</th></tr>
          </thead>
          <tbody>
            {fields.map((r) => (
              <tr key={r.field} className={`f-${r.outcome}`}>
                <td className="lbl">{r.label}<span className="meta mono">{r.field}</span></td>
                <td><Value r={r} side="SI" /></td>
                <td><Value r={r} side="BL" /></td>
                <td>
                  <span className="chips">
                    <Chip small status={r.outcome}>{OUTCOME_LABEL[r.outcome]}</Chip>
                    {r.needs_review ? <Chip small status="NEEDS_REVIEW">review</Chip> : null}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="board-foot">
        <p className="hint"><Icon name="info.circle" /><span>Formatting-only differences are <b>normalised</b>, never a mismatch — hover any value to see what the comparator actually compared.</span></p>
        <label className="switch">
          <input type="checkbox" checked={showNorm} onChange={(e) => toggle(e.target.checked)} />
          <span className="track" />
          <span>Show normalisation</span>
        </label>
      </div>

      {pop ? createPortal(                                   /* body-level: no transformed ancestor shifts position:fixed */
        <div id={popId} role="dialog" aria-label={`${pop.side} ${pop.r.label}: raw and normalised value`} className="pop"
          style={{ left: pop.x, top: pop.y, transform: pop.below ? undefined : "translateY(-100%)" }}>
          <PopBody r={pop.r} side={pop.side} />
        </div>, document.body) : null}
    </div>
  );
}

function PopBody({ r, side }: { r: FieldResult; side: "SI" | "BL" }) {
  const raw = side === "SI" ? r.si_raw : r.bl_raw;
  const norm = side === "SI" ? r.si_norm : r.bl_norm;
  const otherSide = side === "SI" ? "BL" : "SI";
  const otherRaw = side === "SI" ? r.bl_raw : r.si_raw;
  const otherNorm = side === "SI" ? r.bl_norm : r.si_norm;
  return (
    <>
      <div className="meta" style={{ marginBottom: "var(--s-2)" }}>{side === "SI" ? "Shipping Instruction" : "Draft BL"} · {r.label}</div>
      <div className="pair">
        <span className="k">raw</span><span className="raw">{fmtRaw(raw)}</span>
        <span className="k">normalised</span><span className="norm">{raw !== null && norm === raw ? <span className="same">identical, compared as text</span> : fmtNorm(r.field, norm)}</span>
      </div>
      <div className="arrow"><Icon name="arrow.right" />compared with {otherSide}</div>
      <div className="pair">
        <span className="k">raw</span><span className="raw">{fmtRaw(otherRaw)}</span>
        <span className="k">normalised</span><span className="norm">{fmtNorm(r.field, otherNorm)}</span>
      </div>
      <div className="reason">{r.reason}</div>
      <div className="foot">
        <Chip small status={r.outcome}>{OUTCOME_LABEL[r.outcome]}</Chip>
        <Chip small>confidence {r.confidence.toFixed(2)}</Chip>
      </div>
    </>
  );
}
