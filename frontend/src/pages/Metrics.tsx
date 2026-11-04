import { useEffect, useState } from "react";
import { api, type Metrics as M } from "../api";
import { Grounder, money } from "../components/bits";

export default function Metrics() {
  const [m, setM] = useState<M | null>(null);
  useEffect(() => {
    const load = () => api.metrics().then(setM).catch(() => {});
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);
  if (!m) return <p className="muted">Loading…</p>;
  const total = m.grounders.reduce((a, g) => a + g.steps, 0) || 1;
  const cost = m.grounders.reduce((a, g) => a + Number(g.cost_usd || 0), 0);
  const succeeded = m.tasks.succeeded ?? 0;
  return (
    <>
      <h1>Metrics</h1>
      <div className="stats">
        <Stat label="Claims submitted" v={String(succeeded)} />
        <Stat label="Submit clicks / claims" v={`${m.ledger.clicks} / ${m.ledger.submitted}`}
              hint="equal = no click was ever repeated" />
        <Stat label="Unknown outcomes recovered" v={String(m.ledger.recovered_unknown_outcomes)}
              hint="submit timed out, reconcile found the claim" />
        <Stat label="Grounding spend" v={money(cost, 3)} hint={succeeded ? `${money(cost / succeeded, 4)} per claim` : ""} />
        <Stat label="Small-model rejections" v={String(m.escalations.qwen_rejected)} hint={`${m.escalations.claude_rescues} rescued by Claude`} />
      </div>
      <h2>Which tier grounded each action</h2>
      <div className="card">
        <table className="t">
          <thead><tr><th>Tier</th><th>Actions</th><th>Share</th><th title="whole step: screenshots, upload, grounding, action">Avg step ms</th><th>p95 step ms</th><th>Cost</th></tr></thead>
          <tbody>
            {m.grounders.map((g) => (
              <tr key={g.grounder}>
                <td><Grounder g={g.grounder === "none" ? null : g.grounder} /></td>
                <td>{g.steps}</td>
                <td>
                  <div className="progress" style={{ width: 160 }}><span style={{ width: `${(100 * g.steps) / total}%`, background: `var(--${g.grounder === "none" ? "line" : g.grounder})` }} /></div>
                </td>
                <td>{g.avg_ms}</td>
                <td>{Math.round(g.p95_ms ?? 0)}</td>
                <td className="mono">{money(g.cost_usd, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2>Tasks by status</h2>
      <div className="row">
        {Object.entries(m.tasks).map(([k, v]) => <span key={k} className={`chip s-${k}`}>{k.replace("_", " ")} {v}</span>)}
      </div>
    </>
  );
}

function Stat({ label, v, hint }: { label: string; v: string; hint?: string }) {
  return (
    <div className="card">
      <div className="muted" style={{ fontSize: 12 }}>{label}</div>
      <div className="stat">{v}</div>
      {hint && <div className="muted" style={{ fontSize: 12 }}>{hint}</div>}
    </div>
  );
}
