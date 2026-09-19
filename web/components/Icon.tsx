// Inline icons drawn on a 24-unit box to SF Symbols geometry (1.75 stroke at 16 px, 1.5 at 20/24 px, round caps and joins).
// The same set as api/index.html plus the few the console needs.
const PATHS: Record<string, string> = {
  paperplane: "M21 3 3 10.5l7.5 3 3 7.5L21 3zM10.5 13.5 21 3",
  "arrow.clockwise": "M21 12a9 9 0 1 1-2.64-6.36M18.36 2.5v3.14h3.14",
  "arrow.right": "M4 12h16M14 6l6 6-6 6",
  "doc.text": "M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8zM14 3v5h5M9 13h6M9 17h6",
  paperclip: "M20.5 11.5 12 20a5.5 5.5 0 0 1-7.8-7.8l9.2-9.2a3.5 3.5 0 0 1 5 5l-9.2 9.2a1.5 1.5 0 0 1-2.1-2.1L15.5 7",
  "checkmark.circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM8.5 12.5l2.5 2.5 4.5-5",
  "exclamationmark.triangle": "M12 3.5 2.5 19.5h19zM12 9.5v4.5M12 17h.01",
  "xmark.circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM9 9l6 6m0-6-6 6",
  "info.circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM12 11v5M12 8h.01",
  "question.circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.3-1 .8-1 1.5V14M12 17h.01",
  tray: "M3 13h5l1.5 3h5l1.5-3h5v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM5 13V6a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v7",
  "tray.2": "M3 13h5l1.5 3h5l1.5-3h5v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 8h5l1.5 3h5L16 8h5M6 4h12",
  envelope: "M3 7a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM3 7l9 6 9-6",
  "chart.bar": "M4 20V10M10 20V4M16 20v-8M22 20H2",
  magnifyingglass: "M15.5 15.5 21 21M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0z",
  "chevron.right": "m9 5 7 7-7 7",
  "chevron.left": "m15 5-7 7 7 7",
  "arrow.left": "M20 12H4M10 6l-6 6 6 6",
  "person.crop.circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM15.5 10.5a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0zM5.6 18.5A7 7 0 0 1 12 15a7 7 0 0 1 6.4 3.5",
  waveform: "M3 12h3l2.5-6 3.5 12 3-9 1.5 3H21",
  "equal.circle": "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0zM8.5 10h7M8.5 14h7",
  "scale.3d": "M12 3v18M5 8l7-3 7 3M5 8v4a3.5 3.5 0 0 0 7 0V8M12 12a3.5 3.5 0 0 0 7 0V8",
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className = "", title }: { name: IconName; size?: 16 | 20 | 24; className?: string; title?: string }) {
  const cls = size === 16 ? "ic" : size === 20 ? "ic ic-lg" : "ic ic-xl";
  return (
    <svg className={`${cls} ${className}`.trim()} viewBox="0 0 24 24" aria-hidden={title ? undefined : true} role={title ? "img" : undefined}>
      {title ? <title>{title}</title> : null}
      <path d={PATHS[name]} />
    </svg>
  );
}
