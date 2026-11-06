import { useCallback, useEffect, useState } from "react";
import { api, shot, type Intervention } from "../api";
import { useLive } from "../useLive";
import { ago } from "../components/bits";
import { Card, EmptyState, PageHeader } from "../design-system";

export default function Inbox() {
  const [items, setItems] = useState<Intervention[]>([]);
  const load = useCallback(() => api.interventions().then(setItems).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  useLive({}, (e) => { if (e.type.startsWith("intervention.")) load(); });

  return (
    <>
      <PageHeader title="Needs attention">Approvals, MFA codes and stuck tasks, oldest first.</PageHeader>
      {!items.length && <EmptyState title="Nothing is waiting on a human." />}
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))" }}>
        {items.map((i) => (
          <Card as="a" key={i.id} href={`#/tasks/${i.task_id}`} tone="warn">
            <div className="row"><b>{i.kind.replace("_", " ")}</b><span className="muted">row {i.row_number} · {i.stage}</span>
              <span className="grow" /><span className="muted">{ago(i.requested_at)}</span></div>
            <p style={{ margin: "8px 0" }}>{i.prompt}</p>
            {i.screenshot && <img src={shot(i.screenshot)} style={{ width: "100%", borderRadius: 6 }} loading="lazy" />}
            <div className="mono muted" style={{ marginTop: 6 }}>{i.idempotency_key}</div>
          </Card>
        ))}
      </div>
    </>
  );
}
