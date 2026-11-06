import { useCallback, useEffect, useState } from "react";
import { api, type Batch, type Mapping, type PreviewRow } from "../api";
import { useLive } from "../useLive";
import { ago, money, Status } from "../components/bits";
import { Progress } from "../design-system";

export default function Batches() {
  const [batches, setBatches] = useState<Batch[]>([]);
  const load = useCallback(() => api.batches().then(setBatches).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  useLive({}, (e) => { if (e.type.startsWith("task.") || e.type.startsWith("batch.")) load(); });

  return (
    <>
      <h1>Batches</h1>
      <NewBatch onCreated={load} />
      <h2>History</h2>
      <div className="card">
        <table className="t">
          <thead><tr><th>Name</th><th>Status</th><th style={{ width: "30%" }}>Progress</th><th>Rows</th><th>Cost</th><th>Created</th></tr></thead>
          <tbody>
            {batches.map((b) => (
              <tr key={b.id} className="click" onClick={() => (location.hash = `#/batches/${b.id}`)}>
                <td>{b.name}<div className="muted mono">{b.source_filename}</div></td>
                <td><Status s={b.status} /></td>
                <td>
                  <Progress label="Batch progress" parts={[
                    { n: b.succeeded, tone: "ok", label: "done" }, { n: b.waiting, tone: "warn", label: "waiting" },
                    { n: b.active, tone: "accent", label: "running" }, { n: b.failed + b.invalid, tone: "bad", label: "failed or invalid" },
                  ]} />
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    {b.succeeded} done · {b.active} running · {b.waiting} waiting · {b.failed} failed · {b.invalid} invalid
                  </div>
                </td>
                <td>{b.total}</td>
                <td className="mono">{money(b.cost_usd)}</td>
                <td className="muted">{ago(b.created_at)}</td>
              </tr>
            ))}
            {!batches.length && <tr><td colSpan={6} className="muted">No batches yet. Upload a CSV above.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function NewBatch({ onCreated }: { onCreated: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<{ mapping: Mapping[]; rows: PreviewRow[]; summary: { total: number; valid: number } } | null>(null);
  const [creds, setCreds] = useState<{ id: string; label: string; username: string }[]>([]);
  const [cred, setCred] = useState("");
  const [threshold, setThreshold] = useState("5000");
  const [conc, setConc] = useState("3");
  const [dayFirst, setDayFirst] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [newCred, setNewCred] = useState({ label: "acme-demo", username: "demo", password: "" });

  const loadCreds = useCallback(() => api.credentials().then((c) => { setCreds(c); if (c[0]) setCred((x) => x || c[0].id); }), []);
  useEffect(() => { loadCreds().catch(() => {}); }, [loadCreds]);

  const form = () => {
    const fd = new FormData();
    fd.append("file", file!);
    fd.append("day_first", String(dayFirst));
    return fd;
  };
  const doPreview = async (f: File | null = file) => {
    if (!f) return;
    setErr("");
    const fd = new FormData();
    fd.append("file", f);
    fd.append("day_first", String(dayFirst));
    try { setPreview(await api.preview(fd)); } catch (e) { setErr(String(e)); }
  };
  const launch = async () => {
    if (!file || !cred) return;
    setBusy(true);
    setErr("");
    const fd = form();
    fd.append("name", file.name.replace(/\.csv$/i, ""));
    fd.append("credential_id", cred);
    if (threshold) fd.append("approval_threshold", threshold);
    fd.append("max_concurrency", conc);
    try {
      const r = await api.createBatch(fd);
      setFile(null);
      setPreview(null);
      onCreated();
      location.hash = `#/batches/${r.id}`;
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  };

  return (
    <div className="card grid">
      <div className="row">
        <label className="drop grow" style={{ cursor: "pointer" }}>
          <input type="file" accept=".csv,text/csv" hidden
                 onChange={(e) => { const f = e.target.files?.[0] ?? null; setFile(f); doPreview(f); }} />
          {file ? <b>{file.name}</b> : "Drop a client CSV here, or click to choose. Messy headers welcome."}
        </label>
      </div>
      <div className="row">
        <label>Portal login&nbsp;
          <select value={cred} onChange={(e) => setCred(e.target.value)}>
            {creds.map((c) => <option key={c.id} value={c.id}>{c.label} ({c.username})</option>)}
          </select>
        </label>
        <label>Approval above&nbsp;<input style={{ width: 90 }} value={threshold} onChange={(e) => setThreshold(e.target.value)} /></label>
        <label>Concurrency&nbsp;<input style={{ width: 50 }} value={conc} onChange={(e) => setConc(e.target.value)} /></label>
        <label><input type="checkbox" checked={dayFirst} onChange={(e) => { setDayFirst(e.target.checked); }} /> dates are day-first</label>
        <span className="grow" />
        <button onClick={() => doPreview()} disabled={!file}>Re-check</button>
        <button className="primary" onClick={launch} disabled={!file || !cred || busy || !preview?.summary.valid}>
          {busy ? "Starting…" : `Run ${preview?.summary.valid ?? 0} claims`}
        </button>
      </div>
      {!creds.length && (
        <div className="row card alert">
          <span>No portal login stored yet. Stored with envelope encryption; the password never leaves the vault in plain text.</span>
          <input placeholder="label" value={newCred.label} onChange={(e) => setNewCred({ ...newCred, label: e.target.value })} />
          <input placeholder="username" value={newCred.username} onChange={(e) => setNewCred({ ...newCred, username: e.target.value })} />
          <input placeholder="password" type="password" value={newCred.password} onChange={(e) => setNewCred({ ...newCred, password: e.target.value })} />
          <button onClick={() => api.createCredential(newCred).then(loadCreds)}>Save login</button>
        </div>
      )}
      {err && <div className="card alert bad mono">{err}</div>}
      {preview && <PreviewTable p={preview} />}
    </div>
  );
}

function PreviewTable({ p }: { p: { mapping: Mapping[]; rows: PreviewRow[]; summary: { total: number; valid: number } } }) {
  return (
    <div className="grid">
      <div>
        <h2>Column mapping</h2>
        <div className="row">
          {p.mapping.map((m) => (
            <span key={m.source} className="card" style={{ padding: "6px 10px" }}>
              <span className="mono">{m.source}</span> → {m.target ? <b>{m.target}</b> : <span className="muted">ignored</span>}
              <span className="muted" style={{ fontSize: 11 }}> {m.method}{m.method !== "unmapped" ? ` ${Math.round(m.confidence * 100)}%` : ""}</span>
            </span>
          ))}
        </div>
      </div>
      <div>
        <h2>Rows · {p.summary.valid} of {p.summary.total} ready</h2>
        <table className="t">
          <thead><tr><th>#</th><th>Key</th><th>Policy</th><th>Claimant</th><th>Date</th><th>Type</th><th>Amount</th><th>Issues</th></tr></thead>
          <tbody>
            {p.rows.map((r) => (
              <tr key={r.row_number}>
                <td>{r.row_number}</td>
                <td className="mono">{r.idempotency_key}</td>
                <td>{String(r.payload.policy_number ?? "")}</td>
                <td>{String(r.payload.claimant_name ?? "")}</td>
                <td>{String(r.payload.loss_date ?? "")}</td>
                <td>{String(r.payload.loss_type ?? "")}</td>
                <td>{r.payload.amount != null ? Number(r.payload.amount).toLocaleString(undefined, { minimumFractionDigits: 2 }) : ""}</td>
                <td>
                  {r.errors.map((e, i) => <div key={i} style={{ color: "var(--bad)" }}>{e.field}: {e.message}</div>)}
                  {r.warnings.map((w, i) => <div key={i} style={{ color: "var(--warn)" }}>{w}</div>)}
                  {r.valid && !r.warnings.length && <span className="muted">ok</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
