import { useCallback, useEffect, useState } from "react";
import { api, auth } from "./api";
import { useLive } from "./useLive";
import Batches from "./pages/Batches";
import BatchDetail from "./pages/BatchDetail";
import Studio from "./pages/Studio";
import Inbox from "./pages/Inbox";
import Metrics from "./pages/Metrics";

function useHashRoute(): string[] {
  const [hash, setHash] = useState(location.hash || "#/batches");
  useEffect(() => {
    const on = () => setHash(location.hash || "#/batches");
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  return hash.replace(/^#\/?/, "").split("/");
}

export default function App() {
  const route = useHashRoute();
  const [open, setOpen] = useState(0);
  const refreshInbox = useCallback(() => api.interventions().then((x) => setOpen(x.length)).catch(() => {}), []);
  useEffect(() => { refreshInbox(); }, [refreshInbox]);
  const connected = useLive({}, (e) => { if (e.type.startsWith("intervention.")) refreshInbox(); });

  let page;
  if (route[0] === "batches" && route[1]) page = <BatchDetail id={route[1]} />;
  else if (route[0] === "tasks" && route[1]) page = <Studio id={route[1]} />;
  else if (route[0] === "inbox") page = <Inbox />;
  else if (route[0] === "metrics") page = <Metrics />;
  else page = <Batches />;

  const link = (to: string, label: string, extra?: React.ReactNode) => (
    <a href={`#/${to}`} className={route[0] === to ? "active" : ""}>{label}{extra}</a>
  );
  return (
    <div className="shell">
      <nav className="nav">
        <div className="brand">PortalPilot<small>Operator Control &amp; Replay Studio</small></div>
        {link("batches", "Batches")}
        {link("inbox", "Needs attention", open ? <span className="badge">{open}</span> : null)}
        {link("metrics", "Metrics")}
        <div className="foot">
          <span className={`live ${connected ? "on" : ""}`}><i />{connected ? "live" : "offline"}</span>
          <div style={{ marginTop: 8 }}>
            operator&nbsp;
            <input style={{ width: 110, padding: "2px 6px" }} defaultValue={auth.operator}
                   onBlur={(e) => { auth.operator = e.target.value || "operator"; }} />
          </div>
          <div style={{ marginTop: 6 }}>
            token&nbsp;
            <input type="password" style={{ width: 110, padding: "2px 6px" }} defaultValue={auth.token}
                   onBlur={(e) => { if (e.target.value && e.target.value !== auth.token) { auth.token = e.target.value; location.reload(); } }} />
          </div>
        </div>
      </nav>
      <main className="main">{page}</main>
    </div>
  );
}
