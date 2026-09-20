import type { ReactNode } from "react";

/* Section heading: hairline above, generous space, uppercase eyebrow. Hierarchy comes from the rule,
   the whitespace and the tracking — it can never be mistaken for body text. */
export function SectionLabel({ children, trailing, id }: { children: ReactNode; trailing?: ReactNode; id?: string }) {
  return (
    <div className="section">
      <h2 id={id}>{children}</h2>
      {trailing ? <div className="trail">{trailing}</div> : null}
    </div>
  );
}
