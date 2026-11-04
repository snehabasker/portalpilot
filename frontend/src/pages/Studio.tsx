import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, shot, type Intervention, type Step, type TaskDetail } from "../api";
import { useLive } from "../useLive";
import ScreenshotStage from "../components/ScreenshotStage";
import { ago, Grounder, money, Status } from "../components/bits";

const STAGES = ["login", "dedupe_check", "fill_policy", "fill_claimant", "fill_incident", "review", "submit"];
const STATUS_DOT: Record<string, string> = { succeeded: "var(--ok)", failed: "var(--bad)", started: "var(--accent)", skipped: "var(--muted)" };

export default function Studio({ id }: { id: string }) {
  const [d, setD] = useState<TaskDetail | null>(null);
  const [sel, setSel] = useState<number | null>(null);   // step id, null = follow live
  const [view, setView] = useState<"before" | "after">("before");
  const [err, setErr] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(() => api.task(id).then(setD).catch((e) => setErr(String(e))), [id]);
  useEffect(() => { load(); }, [load]);
  const connected = useLive({ task_id: id }, () => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(load, 250);
  });
  // while the workflow is alive, also poll the Temporal query (takeover state, last screenshot)
  useEffect(() => {
    const t = window.setInterval(() => { if (d && ["running", "waiting_human", "queued"].includes(d.task.status)) load(); }, 4000);
    return () => window.clearInterval(t);
  }, [d, load]);

  const steps = d?.steps ?? [];
  const selected: Step | null = useMemo(
    () => (sel == null ? steps[steps.length - 1] ?? null : steps.find((s) => s.id === sel) ?? null), [sel, steps]);
  const openIv = d?.interventions.find((i) => i.status === "open");
  const live = d?.live;
  const takeover = !!live?.takeover;

  if (!d) return <p className="muted">{err || "Loading…"}</p>;
  const t = d.task;
  const stageIdx = STAGES.indexOf(live?.stage ?? t.current_stage ?? "");
  const done = t.status === "succeeded";

  const act = async (fn: () => Promise<unknown>) => {
    setErr("");
    try { await fn(); await load(); } catch (e) { setErr(String(e)); }
  };

  // what the big viewer shows: in takeover, the live browser; otherwise the selected step
  const stageSha = takeover ? live?.last_screenshot
    : (view === "after" ? selected?.screenshot_after : selected?.screenshot_before) ?? live?.last_screenshot;
  const banner = takeover ? `You have the wheel${live?.takeover_by ? ` (${live.takeover_by})` : ""}: click the screenshot to click the page`
    : selected ? `#${selected.seq} ${selected.action} · ${view}` : undefined;

  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <a href={`#/batches/${t.batch_id}`}>← Batch</a>
        <span className="grow" />
        <span className={`live ${connected ? "on" : ""}`}><i />{connected ? "streaming" : "reconnecting"}</span>
      </div>
      <div className="row" style={{ marginBottom: 10 }}>
        <h1 style={{ margin: 0 }}>Row {t.row_number} <span className="mono muted">{t.idempotency_key}</span></h1>
        <Status s={t.status} />
        {t.confirmation_no && <span className="chip s-succeeded mono">{t.confirmation_no}</span>}
        <span className="grow" />
        <span className="muted">cost <b className="mono">{money(t.cost_usd)}</b></span>
        {live?.session_worker && <span className="muted">browser on <span className="mono">{live.session_worker}</span></span>}
      </div>
      <div className="stagebar" style={{ marginBottom: 12 }}>
        {STAGES.map((s, i) => (
          <div key={s} className={done || i < stageIdx ? "done" : i === stageIdx ? "cur" : ""}>{s.replace("_", " ")}</div>
        ))}
      </div>
      {err && <div className="card alert bad mono" style={{ marginBottom: 10 }}>{err}</div>}

      <div className="studio">
        <div className="card timeline">
          <div className="row">
            <b className="grow">Steps</b>
            <button onClick={() => setSel(null)} disabled={sel == null}>Follow live</button>
          </div>
          {steps.map((s, i) => (
            <div key={s.id}>
              {(i === 0 || steps[i - 1].stage !== s.stage) && <div className="stage">{s.stage}</div>}
              <div className={`step ${selected?.id === s.id ? "sel" : ""}`} onClick={() => setSel(s.id)}>
                <span className="dot" style={{ background: STATUS_DOT[s.status] }} />
                <span className="txt" title={s.target ?? ""}>
                  {s.action}{s.target ? `: ${s.target.replace(/^Click the /, "").replace(/\.$/, "")}` : ""}
                </span>
                <Grounder g={s.grounder} />
              </div>
            </div>
          ))}
          {!steps.length && <p className="muted">Waiting for the first step…</p>}
        </div>

        <div className="viewer">
          <ScreenshotStage sha={stageSha} step={takeover ? null : selected} takeover={takeover} banner={banner}
                           onClick={(x, y) => act(() => api.operatorAction(id, { kind: "click", x, y }))} />
          {!takeover && (
            <div className="row">
              <button onClick={() => setView("before")} className={view === "before" ? "primary" : ""}>Before</button>
              <button onClick={() => setView("after")} className={view === "after" ? "primary" : ""}>After</button>
              <span className="muted" style={{ fontSize: 12 }}>✕ = a tier's point the verifier rejected · box = element the click was verified against</span>
            </div>
          )}
          <div className="film">
            {steps.filter((s) => s.screenshot_before).map((s) => (
              <img key={s.id} loading="lazy" src={shot(s.screenshot_before)} className={selected?.id === s.id ? "sel" : ""}
                   onClick={() => setSel(s.id)} title={`#${s.seq} ${s.action}`} />
            ))}
          </div>
        </div>

        <div className="side">
          <Controls d={d} openIv={openIv} takeover={takeover} act={act} id={id} />
          <Ledger d={d} />
          {selected && <Inspector s={selected} />}
          {!!live?.history.length && (
            <div className="card">
              <b>Workflow history</b>
              {live.history.slice(-12).reverse().map((h, i) => (
                <div key={i} style={{ fontSize: 12, marginTop: 4 }}>
                  <span className="muted mono">{h.t.slice(11, 19)}</span> <b>{h.stage}</b> {h.event}
                  {h.detail && <div className="muted" style={{ overflowWrap: "anywhere" }}>{h.detail}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}

function Controls({ d, openIv, takeover, act, id }: {
  d: TaskDetail; openIv?: Intervention; takeover: boolean; act: (f: () => Promise<unknown>) => void; id: string;
}) {
  const [note, setNote] = useState("");
  const [code, setCode] = useState("");
  const [text, setText] = useState("");
  const alive = ["running", "waiting_human", "queued"].includes(d.task.status);

  return (
    <div className={`card grid ${openIv ? "alert" : ""}`} style={{ gap: 10 }}>
      <b>Control</b>
      {openIv && (
        <div>
          <div className="row"><Status s="open" /><b>{openIv.kind.replace("_", " ")}</b><span className="muted">{ago(openIv.requested_at)}</span></div>
          <p style={{ margin: "6px 0" }}>{openIv.prompt}</p>
          {openIv.screenshot && <img src={shot(openIv.screenshot)} style={{ width: "100%", borderRadius: 6 }} />}
        </div>
      )}
      {openIv?.kind === "approval" && (
        <>
          <input placeholder="note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <div className="row">
            <button className="ok" onClick={() => act(() => api.resolve(openIv.id, { approved: true, note }))}>Approve &amp; submit</button>
            <button className="danger" onClick={() => act(() => api.resolve(openIv.id, { approved: false, note }))}>Reject</button>
          </div>
        </>
      )}
      {openIv?.kind === "mfa_code" && (
        <div className="row">
          <input placeholder="6-digit code" value={code} onChange={(e) => setCode(e.target.value)} style={{ width: 130 }} />
          <button className="primary" disabled={!code} onClick={() => act(() => api.resolve(openIv.id, { value: code }).then(() => setCode("")))}>Send code</button>
          <span className="muted" style={{ fontSize: 12 }}>stored sealed; never in workflow history</span>
        </div>
      )}
      {takeover ? (
        <>
          <div className="row">
            <input className="grow" placeholder="type into the focused field" value={text} onChange={(e) => setText(e.target.value)} />
            <button disabled={!text} onClick={() => act(() => api.operatorAction(id, { kind: "type", text }).then(() => setText("")))}>Type</button>
          </div>
          <div className="row">
            {["Enter", "Tab", "Escape"].map((k) => <button key={k} onClick={() => act(() => api.operatorAction(id, { kind: "key", text: k }))}>{k}</button>)}
            <button onClick={() => act(() => api.operatorAction(id, { kind: "refresh" }))}>Refresh</button>
          </div>
          <div className="row">
            <button className="primary" onClick={() => act(() => api.release(id, "retry"))}>Hand back: retry stage</button>
            <button onClick={() => act(() => api.release(id, "skip"))}>Hand back: I finished this stage</button>
          </div>
          <span className="muted" style={{ fontSize: 12 }}>After any takeover the agent re-checks the portal for this claim before it can submit.</span>
        </>
      ) : (
        <button disabled={!alive} onClick={() => act(() => api.takeover(id))}>Take over browser</button>
      )}
      {d.task.error && <div className="mono" style={{ color: "var(--bad)", fontSize: 12 }}>{d.task.error}</div>}
    </div>
  );
}

function Ledger({ d }: { d: TaskDetail }) {
  const l = d.ledger;
  return (
    <div className="card">
      <b>Submit ledger</b>
      {l ? (
        <dl className="kv" style={{ marginTop: 8 }}>
          <dt>state</dt><dd><span className="mono">{l.state}</span></dd>
          <dt>clicks</dt><dd>{l.clicks}</dd>
          <dt>confirmation</dt><dd className="mono">{l.confirmation_no ?? "—"}</dd>
        </dl>
      ) : <p className="muted" style={{ margin: "6px 0 0" }}>Nothing submitted yet.</p>}
    </div>
  );
}

function Inspector({ s }: { s: Step }) {
  return (
    <div className="card">
      <b>Step #{s.seq}</b>
      <dl className="kv" style={{ marginTop: 8 }}>
        <dt>action</dt><dd>{s.action} <span className="muted">by {s.actor}</span></dd>
        {s.target && <><dt>target</dt><dd>{s.target}</dd></>}
        {s.value && <><dt>value</dt><dd className="mono">{s.value}</dd></>}
        <dt>status</dt><dd><Status s={s.status} /></dd>
        <dt>latency</dt><dd>{s.latency_ms ?? "—"} ms</dd>
        <dt>cost</dt><dd className="mono">{money(s.cost_usd, 5)}</dd>
        {s.error && <><dt>error</dt><dd style={{ color: "var(--bad)" }}>{s.error}</dd></>}
      </dl>
      {!!s.grounder_trace?.length && (
        <table className="t" style={{ marginTop: 8, fontSize: 12 }}>
          <thead><tr><th>tier</th><th>point</th><th>ok</th><th>ms</th></tr></thead>
          <tbody>
            {s.grounder_trace.map((a, i) => (
              <tr key={i} title={a.reason}>
                <td><Grounder g={a.tier} /></td>
                <td className="mono">{a.found ? `${a.x},${a.y}` : "—"}</td>
                <td>{a.verified ? "✓" : a.found ? "✕" : ""} <span className="muted">{a.verified ? "" : a.reason.slice(0, 60)}</span></td>
                <td>{a.latency_ms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
