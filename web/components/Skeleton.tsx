/* Skeletons shaped like the real layouts, so content lands without a jump. */
const S = ({ w, h = 14, r }: { w: number | string; h?: number; r?: number }) => (
  <div className="shimmer" style={{ width: w, height: h, borderRadius: r }} />
);

export function PageHeadSkeleton({ stats = 4 }: { stats?: number }) {
  return (
    <div className="phead" role="status" aria-label="Loading">
      <div className="copy">
        <S w={220} h={40} r={12} />
        <S w="80%" h={18} />
        <S w="55%" h={18} />
      </div>
      <div className="stats">
        {Array.from({ length: stats }).map((_, i) => (
          <div key={i} className="stat"><S w={64} h={30} r={8} /><S w={48} h={10} /></div>
        ))}
      </div>
    </div>
  );
}

export function TableSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="sk-card" role="status" aria-label="Loading">
      <div className="sk-row"><S w={260} h={40} r={20} /><S w={220} h={36} r={18} /></div>
      <div className="sk-row">{[80, 120, 96, 110, 70, 64].map((w, i) => <S key={i} w={w} h={32} r={16} />)}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: 16, marginTop: 8 }}>
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="sk-row">
            <S w={88} /><S w={150 - (i % 3) * 20} /><S w={`${34 - (i % 4) * 4}%`} /><S w={96} h={22} r={11} /><S w={60} h={22} r={11} />
          </div>
        ))}
      </div>
    </div>
  );
}

export function DetailSkeleton() {
  return (
    <div className="sections" role="status" aria-label="Loading">
      <div className="stack-s">
        <S w={64} h={12} />
        <S w="70%" h={36} r={10} />
        <S w={320} h={14} />
        <div className="sk-row">{[120, 96, 140, 110].map((w, i) => <S key={i} w={w} h={28} r={14} />)}</div>
      </div>
      <div className="sk-card">
        <S w={120} h={10} />
        {Array.from({ length: 7 }).map((_, i) => (
          <div key={i} className="sk-row"><S w="22%" /><S w="31%" /><S w="31%" /><S w={80} h={22} r={11} /></div>
        ))}
      </div>
    </div>
  );
}
