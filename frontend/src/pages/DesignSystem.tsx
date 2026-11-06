import { useState } from "react";
import {
  Button, Card, EmptyState, Field, KeyValue, LiveDot, PageHeader, Progress, Stat, StatGrid, StatusChip, TierBadge,
  Timeline, colorTokens, contrastReport, themes, type ColorToken,
} from "../design-system";

/** Living styleguide: every token and component, rendered by the same code the Studio uses. */
export default function DesignSystem() {
  const [theme, setTheme] = useState<"dark" | "light">(
    (document.documentElement.dataset.theme as "dark" | "light") ?? "dark");
  const flip = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    setTheme(next);
  };
  const failing = contrastReport.filter((r) => !r.ok).length;
  return (
    <>
      <PageHeader title="Design system" actions={<Button onClick={flip}>Show {theme === "dark" ? "light" : "dark"} theme</Button>}>
        Tokens from <code>src/design-system/tokens.json</code>, components from <code>components.tsx</code>.
        Contrast is checked in CI: {contrastReport.length - failing}/{contrastReport.length} pairs pass WCAG AA.
      </PageHeader>

      <h2>Color tokens ({theme})</h2>
      <div className="ds-swatches">
        {colorTokens.map((t) => <Swatch key={t} token={t} hex={themes[theme][t]} />)}
      </div>

      <h2>Contrast checks</h2>
      <Card>
        <table className="t">
          <thead><tr><th>Theme</th><th>Foreground</th><th>Background</th><th>Ratio</th><th>Minimum</th><th /></tr></thead>
          <tbody>
            {contrastReport.map((r, i) => (
              <tr key={i}>
                <td>{r.theme}</td><td><code>{r.fg}</code></td><td><code>{r.bg}</code></td>
                <td className="mono">{r.ratio.toFixed(2)}:1</td><td className="mono">{r.min}:1</td>
                <td><StatusChip status={r.ok ? "pass" : "fail"} tone={r.ok ? "ok" : "bad"} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <h2>Buttons</h2>
      <div className="ds-row">
        <Button variant="primary">Primary</Button><Button>Secondary</Button><Button variant="ok">Approve</Button>
        <Button variant="danger">Reject</Button><Button variant="ghost">Ghost</Button><Button size="sm">Small</Button>
        <Button disabled>Disabled</Button><Button busy>Busy</Button>
      </div>

      <h2>Status and tiers</h2>
      <div className="ds-row">
        {["queued", "running", "waiting_human", "succeeded", "failed", "invalid", "firing", "acknowledged", "resolved"]
          .map((s) => <StatusChip key={s} status={s} />)}
      </div>
      <div className="ds-row" style={{ marginTop: 8 }}>
        {["cache", "qwen", "claude", "dom", "operator", null].map((t) => <TierBadge key={String(t)} tier={t} />)}
        <LiveDot on /><LiveDot on={false} />
      </div>

      <h2>Stats</h2>
      <StatGrid>
        <Stat label="Claims submitted" value="4" />
        <Stat label="Submit clicks / claims" value="4 / 4" hint="equal = no click was repeated" tone="ok" />
        <Stat label="Double-submit suspects" value="0" tone="ok" />
        <Stat label="Oldest open intervention" value="12 min" tone="warn" />
      </StatGrid>

      <h2>Progress, timeline, key-value</h2>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <Card>
          <Progress label="Batch progress" parts={[{ n: 5, tone: "ok", label: "done" }, { n: 2, tone: "accent", label: "running" },
            { n: 1, tone: "warn", label: "waiting" }, { n: 1, tone: "bad", label: "failed" }]} />
          <div style={{ height: 12 }} />
          <Progress label="Grounding tier share" parts={[{ n: 58, tone: "qwen", label: "qwen" }, { n: 34, tone: "cache", label: "cache" },
            { n: 7, tone: "dom", label: "dom" }]} />
        </Card>
        <Card>
          <Timeline items={[
            { at: "2026-11-06T10:00:00Z", tone: "bad", title: "fired · alertmanager", body: "PortalDegraded" },
            { at: "2026-11-06T10:02:10Z", tone: "accent", title: "acknowledged · operator:oncall" },
            { at: "2026-11-06T10:19:40Z", tone: "ok", title: "resolved · operator:oncall" },
          ]} />
        </Card>
        <Card>
          <KeyValue items={[["Ledger", <StatusChip status="confirmed" />], ["Clicks", "1"], ["Claim", <code>CLM-000004</code>]]} />
        </Card>
      </div>

      <h2>Cards and empty states</h2>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
        <Card>Default card</Card>
        <Card tone="warn">Needs attention: an approval is waiting</Card>
        <Card tone="bad">Incident: a page is firing</Card>
        <EmptyState title="Nothing is waiting on a human." />
      </div>

      <h2>Form fields</h2>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))" }}>
        <Field label="Approval threshold" hint="Claims above this amount wait for a person."><input defaultValue="5000" /></Field>
        <Field label="Concurrency"><select defaultValue="3"><option>1</option><option>3</option><option>5</option></select></Field>
      </div>
    </>
  );
}

function Swatch({ token, hex }: { token: ColorToken; hex: string }) {
  return (
    <div className="ds-swatch">
      <div style={{ background: `var(--color-${token})` }} />
      <div><code>{token}</code><span className="ds-muted mono">{hex}</span></div>
    </div>
  );
}
