import { useId, useState } from "react";
import type { ControlMsg, StateMsg } from "../protocol";
import "./snapshot.css";

export function snapshotTime(value?: string | null): string {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? date.toISOString().replace("T", " ").replace(/\.\d+Z$/, "Z") : "Unavailable";
}

export function SnapshotPanel({ state, enabled, controls, send }: {
  state: StateMsg | null; enabled: boolean; controls: boolean; send: (message: ControlMsg) => void;
}) {
  const [source, setSource] = useState<"demo" | "vatsim">("demo");
  const id = useId();
  const status = state?.live_import;
  const busy = status?.phase === "fetching";
  return <details className="snapshot-panel">
    <summary>Traffic snapshots</summary>
    <p>Add a one-time snapshot to this session. Imported aircraft then fly in the simulation.</p>
    {controls && <>
      <label htmlFor={id}>Snapshot source</label>
      <select id={id} value={source} onChange={event => setSource(event.target.value as "demo" | "vatsim")}>
        <option value="demo">Synthetic demo · offline</option><option value="vatsim">VATSIM · read-only</option>
      </select>
      <button type="button" disabled={!enabled || !state?.live_import_available || busy}
        onClick={() => send({ type: "control", action: "import_live", params: { source } })}>
        {busy ? "Importing snapshot…" : "Import snapshot"}
      </button>
      {!state?.live_import_available && <p>Snapshot import is unavailable until the simulator is ready.</p>}
    </>}
    {status && <div className="snapshot-result" role="status">
      <strong>{status.source === "demo" ? "SYNTHETIC DEMO" : "VATSIM SNAPSHOT"} · SIMULATED</strong>
      {busy ? <p>Loading snapshot…</p> : status.error ? <p className="snapshot-warning">
        {status.error.replaceAll("_", " ")}</p> : <>
        <p>{status.counts.imported} imported · {status.counts.skipped} skipped</p>
        {status.ok && status.counts.imported === 0 && <p>No new matching Gatwick aircraft were imported.</p>}
      </>}
      {status.stale && <p className="snapshot-warning">Stale source snapshot</p>}
      <dl><div><dt>Source time</dt><dd>{snapshotTime(status.source_time)}</dd></div>
        <div><dt>Fetched</dt><dd>{snapshotTime(status.fetched_at)}</dd></div></dl>
      <p>{status.attribution}</p>
      {Object.keys(status.counts.skipped_by_reason).length > 0 && <details>
        <summary>Skipped aircraft</summary><ul>{Object.entries(status.counts.skipped_by_reason).map(([reason, count]) =>
          <li key={reason}>{reason.replaceAll("_", " ")} · {count}</li>)}</ul>
      </details>}
    </div>}
    <p className="snapshot-note">SNAP on a radar tag means imported snapshot. Subsequent motion is simulated.</p>
  </details>;
}
