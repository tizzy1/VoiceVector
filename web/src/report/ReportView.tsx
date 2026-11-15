import { useEffect, useId, useRef, useState } from "react";
import type { Report } from "../protocol";
import { groupFindings, record, seconds, simTime, valueText } from "./reportData";
import "./report.css";

interface Props { report: Report; onDownload: () => void }

function Timeline({ report }: { report: Report }) {
  const [index, setIndex] = useState(0);
  useEffect(() => setIndex(0), [report]);
  const event = report.timeline[index];
  const id = useId();
  return <section className="report-section" aria-label="Event timeline">
    <h2>Event timeline <span>Simulation time</span></h2>
    {!report.timeline.length ? <p className="muted">No timeline events recorded.</p> : <div className="report-timeline">
      <ol aria-label="Timeline markers">{report.timeline.map((item, i) => <li key={i}>
        <button type="button" aria-pressed={index === i} aria-controls={id} onClick={() => setIndex(i)}>
          <time>{simTime(item.t)}</time><span>{item.type.replaceAll("_", " ")}
            {item.callsign ? ` · ${item.callsign}` : ""}</span>
        </button>
      </li>)}</ol>
      <div id={id} className="report-event" aria-live="polite">
        {event && <><h3>{simTime(event.t)} · {event.type.replaceAll("_", " ")}</h3>
          <p>{event.actor}{event.callsign ? ` · ${event.callsign}` : ""}{event.tx_id ? ` · ${event.tx_id}` : ""}</p>
          {Object.keys(event.data).length > 0 ? <pre>{JSON.stringify(event.data, null, 2)}</pre>
            : <p className="muted">No additional event details.</p>}
        </>}
      </div>
    </div>}
  </section>;
}

export function ReportView({ report, onDownload }: Props) {
  const latency = record(report.stats.latency);
  return <div className="report-view">
    <div className="report-title"><div><h1>Session debrief</h1>
      <p>Session {report.session_id} · {simTime(report.duration_s)} simulated</p></div>
      <button type="button" onClick={onDownload}>Download JSON</button>
    </div>
    <section className="report-overview" aria-label="Score and breakdown">
      <div className="report-score"><strong>{report.score}</strong><span> / 100</span><p>Session score</p></div>
      <div><h2>Score breakdown</h2><dl className="report-breakdown">
        {Object.entries(report.score_breakdown).map(([key, value]) => <div key={key}>
          <dt>{key.replaceAll("_", " ")}</dt><dd>{value > 0 && key !== "base" ? "+" : ""}{value}</dd>
        </div>)}
      </dl><p className="report-note">Final score is bounded to 0–100; the breakdown shows the supplied contributions.</p></div>
    </section>
    <dl className="report-stats" aria-label="Session statistics">
      {[["transmissions", "Controller transmissions"], ["pilot_transmissions", "Pilot transmissions"],
        ["landings", "Landings"], ["departures", "Departures"], ["go_arounds", "Go-arounds"]].map(([key, label]) =>
        <div key={key}><dt>{label}</dt><dd>{valueText(report.stats[key])}</dd></div>)}
    </dl>
    {Object.keys(record(report.stats.efficiency)).length > 0 && <details className="report-evidence">
      <summary>Recorded efficiency measurements</summary><pre>{JSON.stringify(report.stats.efficiency, null, 2)}</pre>
    </details>}
    <section className="report-section" aria-label="Narrative and coaching">
      <h2>Debrief and coaching</h2>
      {report.narrative ? <p className="report-narrative">{report.narrative}</p>
        : <p className="muted">No narrative supplied. The recorded findings and score remain available below.</p>}
      {report.coaching.length ? <ol className="report-coaching">{report.coaching.map((point, index) =>
        <li key={index}>{point}</li>)}</ol> : <p className="muted">No coaching points supplied.</p>}
    </section>
    <section className="report-section" aria-label="Findings by category">
      <h2>Findings <span>{report.findings.length} recorded</span></h2>
      {groupFindings(report.findings).map(group => <details key={group.key} className="report-category" open={group.findings.length > 0}>
        <summary>{group.label} <span>{group.findings.length}</span></summary>
        {!group.findings.length ? <p className="muted">No findings recorded in this category.</p> : group.findings.map(finding =>
          <article key={finding.id} className="report-finding">
            <div className="report-finding-meta"><span className={`report-severity severity-${finding.severity.toLowerCase()}`}>
              {finding.severity}</span><span>{finding.id} · {finding.rule_id} · {simTime(finding.t)} · {finding.actor}</span></div>
            <h3>{finding.title}</h3>
            {finding.callsigns.length > 0 && <p className="report-note">{finding.callsigns.join(" · ")}</p>}
            <p>{finding.detail}</p>
            {typeof finding.evidence.transcript === "string" && finding.evidence.transcript &&
              <blockquote>{finding.evidence.transcript}</blockquote>}
            {finding.correct_practice && <p className="report-practice"><b>Correct practice</b> · {finding.correct_practice}</p>}
            <MissedReplay report={report} errorId={finding.evidence.error_id} />
            {Object.keys(finding.evidence).length > 0 && <details className="report-evidence"><summary>Recorded evidence</summary>
              <pre>{JSON.stringify(finding.evidence, null, 2)}</pre></details>}
          </article>)}
      </details>)}
    </section>
    <section className="report-section" aria-label="Pilot error catch table">
      <h2>Pilot errors and catches</h2>
      {!report.errors.length ? <p className="muted">No pilot errors recorded.</p> : <div className="report-table-scroll" tabIndex={0}>
        <table><caption>Intended, read back and flown values from recorded pilot errors</caption>
          <thead><tr>{["Aircraft / error", "Intended", "Readback", "Flown", "Outcome", "Caught by / time"].map(label =>
            <th key={label} scope="col">{label}</th>)}</tr></thead>
          <tbody>{report.errors.map(error => <tr key={error.id}>
            <th scope="row">{error.callsign}<small>{error.id} · {error.kind.replaceAll("_", " ")}</small>
              <small>{error.mode.replaceAll("_", " ")} · {error.element_type.replaceAll("_", " ")}</small></th>
            <td>{valueText(error.intended)}</td><td>{valueText(error.readback_value)}</td><td>{valueText(error.flown_value)}</td>
            <td>{error.outcome.replaceAll("_", " ")}</td><td>{error.caught_by ?? "—"}
              {error.t_caught != null && <small>{simTime(error.t_caught)} sim</small>}</td>
          </tr>)}</tbody>
        </table>
      </div>}
    </section>
    <Timeline report={report} />
    <section className="report-section" aria-label="Report latency statistics">
      <h2>Latency statistics</h2>
      <p className="report-note">Full-session server measurements in seconds; missing measurements are shown as —.</p>
      <div className="report-table-scroll"><table><caption>Latency sample counts and supplied percentiles</caption>
        <thead><tr><th scope="col">Metric</th><th scope="col">Samples</th><th scope="col">p50</th><th scope="col">p90</th></tr></thead>
        <tbody>{[["pilot_response_s", "Pilot response · simulation time"], ["machine_s", "Machine"],
          ["perceived_s", "Perceived response"]].map(([key, label]) => {
            const metric = record(latency[key]);
            return <tr key={key}><th scope="row">{label}</th><td>{valueText(metric.n)}</td>
              <td>{seconds(metric.p50)}</td><td>{seconds(metric.p90)}</td></tr>;
          })}</tbody>
      </table></div>
    </section>
  </div>;
}

export function ReportDialog({ report, open, onClose, onDownload, href }: Props & {
  open: boolean; onClose: () => void; href: string;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = dialog.current;
    if (open && !node?.open) node?.showModal();
    else if (!open && node?.open) node.close();
    return () => { if (node?.open) node.close(); };
  }, [open]);
  return <dialog ref={dialog} className="report-dialog" aria-label="Session debrief"
    onCancel={event => { event.preventDefault(); onClose(); }}>
    <div className="report-dialog-controls"><a href={href}>Open report page</a>
      <button type="button" autoFocus onClick={onClose}>Close report</button></div>
    <ReportView report={report} onDownload={onDownload} />
  </dialog>;
}

export function ReportPage({ report, connectionStatus, onDownload }: {
  report: Report | null; connectionStatus: string; onDownload: () => void;
}) {
  return <div className="report-page"><header><strong>VOICE<span>VECTOR</span></strong>
    <nav aria-label="Views"><a href="/">Radar</a><a href="/viz">Second screen</a></nav>
    <span className="muted">{connectionStatus}</span></header>
    {report ? <ReportView report={report} onDownload={onDownload} /> : <section className="report-empty">
      <h1>Session report</h1><p>No completed report is available for this session.</p>
      <p>End the session from the controller or presenter controls to generate its debrief.</p>
    </section>}
  </div>;
}


// "Hear what you missed" (15 Nov, Sanju): the pilot readback that carried this planted error,
// played through the same radio sound. Served by GET /api/replay/<error_id> while the session lives.
function MissedReplay({ report, errorId }: { report: Report; errorId: unknown }) {
  if (typeof errorId !== "string") return null;
  const error = report.errors.find(e => e.id === errorId);
  if (!error || error.mode === "execution_only") return null;
  const fmt = (v: unknown): string => {
    if (v && typeof v === "object") {                      // {value: 240, turn_dir: "L"} etc.
      const o = v as Record<string, unknown>;
      return [fmt(o.value ?? o.fix ?? o.runway ?? o.frequency ?? ""), typeof o.turn_dir === "string" ? o.turn_dir : ""]
        .filter(Boolean).join(" ");
    }
    return typeof v === "number" ? Math.round(v).toLocaleString("en-GB") : String(v ?? "");
  };
  return <div className="report-replay">
    <p><b>Hear what the pilot said</b> · read back {fmt(error.readback_value)}, you cleared {fmt(error.intended)}</p>
    <audio controls preload="none" src={`/api/replay/${encodeURIComponent(errorId)}`}>Audio not supported.</audio>
  </div>;
}
