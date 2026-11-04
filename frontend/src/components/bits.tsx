export function Status({ s }: { s: string }) {
  return <span className={`chip s-${s}`}>{s.replace("_", " ")}</span>;
}

export function Grounder({ g }: { g: string | null }) {
  return <span className={`g g-${g ?? "none"}`}>{g ?? "—"}</span>;
}

export const money = (v: number | null | undefined, digits = 4) => `$${Number(v ?? 0).toFixed(digits)}`;

export const ago = (iso: string | null | undefined) => {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
};

export function Progress({ parts }: { parts: { n: number; color: string }[] }) {
  const total = parts.reduce((a, p) => a + p.n, 0) || 1;
  return (
    <div className="progress">
      {parts.map((p, i) => <span key={i} style={{ width: `${(100 * p.n) / total}%`, background: p.color }} />)}
    </div>
  );
}
