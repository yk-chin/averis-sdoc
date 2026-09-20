/* Wordmark: a rounded square (radius 32 % of the side, close to the iOS icon ratio) in brand blue
   carrying a document with a check — "a document, verified". Title-case name, tracking −0.02em. */
export function Wordmark() {
  return (
    <span className="brand" style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      <Mark />
      <span className="name">ShipDoc</span>
    </span>
  );
}

export function Mark({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden style={{ flex: "none" }}>
      <rect width="32" height="32" rx="10.24" fill="var(--accent)" />
      <path d="M11 8.5h7l4 4V23a1.5 1.5 0 0 1-1.5 1.5h-9.5A1.5 1.5 0 0 1 9.5 23V10A1.5 1.5 0 0 1 11 8.5z" stroke="#fff" strokeWidth="1.9" strokeLinejoin="round" />
      <path d="M18 8.5v4h4" stroke="#fff" strokeWidth="1.9" strokeLinejoin="round" />
      <path d="M12.6 17.6 15 20l4.6-5" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
