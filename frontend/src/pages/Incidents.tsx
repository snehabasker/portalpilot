import { useCallback, useEffect, useState } from "react";
import { api, type Incident, type IncidentEvent } from "../api";
import { useLive } from "../useLive";
import { ago } from "../components/bits";
import {
  Button, Card, EmptyState, Field, KeyValue, PageHeader, StatusChip, Timeline, type Tone,
} from "../design-system";

const RUNBOOKS = "https://github.com/snehabasker/portalpilot/blob/main/";

/** Alerts from Alertmanager become incidents here. Built only from design-system components. */
export default function Incidents({ id }: { id?: string }) {
  return id ? <IncidentDetail id={id} /> : <IncidentList />;
}

function IncidentList() {
  const [items, setItems] = useState<Incident[] | null>(null);
  const load = useCallback(() => api.incidents().then(setItems).catch(() => setItems([])), []);
  useEffect(() => { load(); }, [load]);
  useLive({}, (e) => { if (e.type.startsWith("incident.")) load(); });

  const open = (items ?? []).filter((i) => i.status !== "resolved");
  const closed = (items ?? []).filter((i) => i.status === "resolved");
  return (
    <>
      <PageHeader title="Incidents">
        Alerts from Prometheus. Pages need a response now; tickets by the next business hour.
      </PageHeader>
      {items && !open.length && <EmptyState title="Nothing is firing.">Every SLO is inside its budget.</EmptyState>}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))" }}>
        {open.map((i) => <IncidentCard key={i.id} i={i} />)}
      </div>
      {!!closed.length && (
        <>
          <h2>Resolved</h2>
          <Card>
            <table className="t">
              <thead><tr><th>Alert</th><th>Severity</th><th>Started</th><th>Time to resolve</th></tr></thead>
              <tbody>
                {closed.map((i) => (
                  <tr key={i.id} className="click" onClick={() => (location.hash = `#/incidents/${i.id}`)}>
                    <td><b>{i.alertname}</b><div className="ds-muted">{i.summary}</div></td>
                    <td><StatusChip status={i.severity} /></td>
                    <td className="ds-muted">{ago(i.started_at)}</td>
                    <td className="mono">{duration(i.started_at, i.resolved_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </>
      )}
    </>
  );
}

function IncidentCard({ i }: { i: Incident }) {
  return (
    <Card as="a" href={`#/incidents/${i.id}`} tone={i.severity === "page" ? "bad" : "warn"}>
      <div className="ds-row">
        <b>{i.alertname}</b><StatusChip status={i.severity} /><StatusChip status={i.status} />
        <span className="grow" /><span className="ds-muted">{ago(i.started_at)}</span>
      </div>
      <p style={{ margin: "8px 0 0" }}>{i.summary}</p>
    </Card>
  );
}

const EVENT_TONE: Record<string, Tone> = {
  fired: "bad", refired: "bad", acknowledged: "accent", note: "neutral", resolved: "ok", auto_resolved: "ok",
};

function IncidentDetail({ id }: { id: string }) {
  const [data, setData] = useState<{ incident: Incident; events: IncidentEvent[] } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => api.incident(id).then(setData).catch((e) => setErr(String(e))), [id]);
  useEffect(() => { load(); }, [load]);
  useLive({}, (e) => { if (e.type.startsWith("incident.")) load(); });

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await fn(); setNote(""); await load(); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };
  if (!data) return <p className="ds-muted">{err || "Loading…"}</p>;
  const { incident: i, events } = data;
  return (
    <>
      <PageHeader title={i.alertname} actions={
        <>
          {i.status === "firing" && <Button variant="primary" busy={busy} onClick={() => act(() => api.ackIncident(id))}>Acknowledge</Button>}
          {i.status !== "resolved" && <Button variant="ok" busy={busy} onClick={() => act(() => api.resolveIncident(id, note))}>Resolve</Button>}
        </>
      }>{i.summary}</PageHeader>
      {err && <Card tone="bad">{err}</Card>}
      <div className="grid" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)", alignItems: "start" }}>
        <Card as="section">
          <h2 style={{ marginTop: 0 }}>Timeline</h2>
          <Timeline items={events.map((e) => ({
            at: e.created_at, tone: EVENT_TONE[e.kind] ?? "neutral",
            title: `${e.kind.replace("_", " ")} · ${e.actor}`, body: e.body ?? undefined,
          }))} />
          {i.status !== "resolved" && (
            <div style={{ marginTop: 14, display: "grid", gap: 8 }}>
              <Field label="Add to the timeline" hint="What you checked, what you changed, what you saw. Postmortems are written from this.">
                <textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} />
              </Field>
              <div><Button disabled={!note.trim()} busy={busy} onClick={() => act(() => api.noteIncident(id, note))}>Add note</Button></div>
            </div>
          )}
        </Card>
        <Card as="section">
          <KeyValue items={[
            ["Status", <StatusChip status={i.status} />],
            ["Severity", <StatusChip status={i.severity} />],
            ["Started", new Date(i.started_at).toLocaleString()],
            ["Acknowledged", i.acknowledged_at ? `${i.acknowledged_by} · ${duration(i.started_at, i.acknowledged_at)} after start` : null],
            ["Resolved", i.resolved_at ? `${duration(i.started_at, i.resolved_at)} after start` : null],
            ["Runbook", i.runbook_url ? <a href={RUNBOOKS + i.runbook_url} target="_blank" rel="noreferrer">{i.runbook_url.split("/").pop()}</a> : null],
          ]} />
          <h2>Labels</h2>
          <KeyValue items={Object.entries(i.labels).filter(([k]) => !["alertname", "severity"].includes(k))
            .map(([k, v]) => [k, <code>{v}</code>])} />
        </Card>
      </div>
    </>
  );
}

function duration(from: string, to: string | null): string {
  if (!to) return "—";
  const s = Math.max(0, (new Date(to).getTime() - new Date(from).getTime()) / 1000);
  return s < 120 ? `${Math.round(s)} s` : s < 7200 ? `${Math.round(s / 60)} min` : `${(s / 3600).toFixed(1)} h`;
}
