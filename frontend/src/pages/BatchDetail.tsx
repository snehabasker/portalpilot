import { useCallback, useEffect, useState } from "react";
import { api, type Batch, type TaskRow } from "../api";
import { useLive } from "../useLive";
import { money, Status } from "../components/bits";

export default function BatchDetail({ id }: { id: string }) {
  const [data, setData] = useState<{ batch: Batch & Record<string, unknown>; tasks: TaskRow[] } | null>(null);
  const load = useCallback(() => api.batch(id).then(setData).catch(() => {}), [id]);
  useEffect(() => { load(); }, [load]);
  // task events carry task_id, not batch_id, so listen to everything and refetch (cheap at this scale)
  useLive({}, (e) => { if (e.type === "task.updated" || e.type.startsWith("intervention.") || e.type.startsWith("batch.")) load(); });

  if (!data) return <p className="muted">Loading…</p>;
  const { batch, tasks } = data;
  return (
    <>
      <div className="row"><a href="#/batches">← Batches</a></div>
      <h1>{batch.name} <Status s={batch.status} /></h1>
      <div className="card">
        <table className="t">
          <thead><tr><th>Row</th><th>Status</th><th>Stage</th><th>Idempotency key</th><th>Claim no.</th><th>Amount</th><th>Cost</th><th>Detail</th></tr></thead>
          <tbody>
            {tasks.map((t) => (
              <tr key={t.id} className="click" onClick={() => (location.hash = `#/tasks/${t.id}`)}>
                <td>{t.row_number}</td>
                <td><Status s={t.status} /></td>
                <td className="muted">{t.current_stage ?? ""}</td>
                <td className="mono">{t.idempotency_key}</td>
                <td className="mono">{t.confirmation_no ?? ""}</td>
                <td>{t.payload.amount != null ? Number(t.payload.amount).toLocaleString(undefined, { minimumFractionDigits: 2 }) : ""}</td>
                <td className="mono">{money(t.cost_usd)}</td>
                <td style={{ maxWidth: 360 }}>
                  {t.validation_errors.map((e, i) => <div key={i} style={{ color: "var(--bad)" }}>{e.field}: {e.message}</div>)}
                  {t.error && <span style={{ color: "var(--bad)" }}>{t.error}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
