import { useEffect, useState } from "react";
import { api, type Metrics as M } from "../api";
import { Grounder, money } from "../components/bits";
import { Card, PageHeader, Progress, Stat, StatGrid, StatusChip, type Tier } from "../design-system";

export default function Metrics() {
  const [m, setM] = useState<M | null>(null);
  useEffect(() => {
    const load = () => api.metrics().then(setM).catch(() => {});
    load();
    const t = window.setInterval(load, 5000);
    return () => window.clearInterval(t);
  }, []);
  if (!m) return <p className="ds-muted">Loading…</p>;
  const total = m.grounders.reduce((a, g) => a + g.steps, 0) || 1;
  const cost = m.grounders.reduce((a, g) => a + Number(g.cost_usd || 0), 0);
  const succeeded = m.tasks.succeeded ?? 0;
  return (
    <>
      <PageHeader title="Metrics">Live from Postgres. Prometheus and Grafana carry the same numbers over time.</PageHeader>
      <StatGrid>
        <Stat label="Claims submitted" value={String(succeeded)} />
        <Stat label="Submit clicks / claims" value={`${m.ledger.clicks} / ${m.ledger.submitted}`}
              hint="equal = no click was ever repeated" />
        <Stat label="Unknown outcomes recovered" value={String(m.ledger.recovered_unknown_outcomes)}
              hint="submit timed out, reconcile found the claim" />
        <Stat label="Grounding spend" value={money(cost, 3)} hint={succeeded ? `${money(cost / succeeded, 4)} per claim` : ""} />
        <Stat label="Small-model rejections" value={String(m.escalations.qwen_rejected)} hint={`${m.escalations.claude_rescues} rescued by Claude`} />
      </StatGrid>
      <h2>Which tier grounded each action</h2>
      <Card>
        <table className="t">
          <thead><tr><th>Tier</th><th>Actions</th><th>Share</th><th title="whole step: screenshots, upload, grounding, action">Avg step ms</th><th>p95 step ms</th><th>Cost</th></tr></thead>
          <tbody>
            {m.grounders.map((g) => (
              <tr key={g.grounder}>
                <td><Grounder g={g.grounder === "none" ? null : g.grounder} /></td>
                <td>{g.steps}</td>
                <td style={{ width: 160 }}>
                  <Progress label={`${g.grounder} share`} parts={[
                    { n: g.steps, tone: g.grounder === "none" ? "neutral" : (g.grounder as Tier), label: g.grounder },
                    { n: total - g.steps, tone: "neutral", label: "other tiers" }]} />
                </td>
                <td>{g.avg_ms}</td>
                <td>{Math.round(g.p95_ms ?? 0)}</td>
                <td className="mono">{money(g.cost_usd, 4)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      <h2>Tasks by status</h2>
      <div className="ds-row">
        {Object.entries(m.tasks).map(([k, v]) => <span key={k}><StatusChip status={k} /> {v}</span>)}
      </div>
    </>
  );
}

