import type { AircraftPublic, Alert } from "../protocol";
import { headingLabel, levelLabel } from "../radar/geometry";
import "./aircraft-details.css";

export function AircraftDetails({ aircraft: ac, alerts }: {
  aircraft?: AircraftPublic; alerts: readonly Alert[];
}) {
  if (!ac) return <section className="aircraft-details" aria-label="Selected aircraft">
    <h2>Selected aircraft</h2><p className="muted">Select a radar track or flight-list row.</p>
  </section>;
  const relevant = alerts.filter(alert => alert.callsigns.includes(ac.callsign));
  return <section className="aircraft-details" aria-label="Selected aircraft">
    <h2>Selected aircraft <span>{ac.callsign}</span></h2>
    <p className="aircraft-name">{ac.spoken_plain} · {ac.actype} · Wake {ac.wake}</p>
    {ac.traffic_source && <div className="aircraft-provenance">
      Imported simulation · {ac.traffic_source === "demo" ? "SYNTHETIC DEMO" : ac.traffic_source.toUpperCase()}
      <details><summary>Snapshot provenance</summary><p>{ac.source_attribution}</p>
        <p>Source time · {ac.source_time ?? "Unavailable"}</p><p>Fetched · {ac.fetched_at ?? "Unavailable"}</p>
      </details>
    </div>}
    <table>
      <caption>Observed state and assigned clearance</caption>
      <thead><tr><th scope="col">Parameter</th><th scope="col">Observed</th>
        <th scope="col">Assigned</th></tr></thead>
      <tbody>
        <tr><th scope="row">Level</th><td>{levelLabel(ac.alt)}</td>
          <td>{levelLabel(ac.cleared_alt)}</td></tr>
        <tr><th scope="row">Heading</th><td>{headingLabel(ac.hdg)}°</td>
          <td>{ac.tgt_hdg == null ? "—" : headingLabel(ac.tgt_hdg) + "°"}</td></tr>
        <tr><th scope="row">IAS</th><td>{Math.round(ac.ias)} kt</td>
          <td>{ac.tgt_ias == null ? "—" : Math.round(ac.tgt_ias) + " kt"}</td></tr>
      </tbody>
    </table>
    <dl>
      <div><dt>Route</dt><dd>{ac.route_name ?? "—"}</dd></div>
      <div><dt>Next fix</dt><dd>{ac.next_fix ?? "—"}</dd></div>
      <div><dt>Ground speed</dt><dd>{Math.round(ac.gs)} kt</dd></div>
      <div><dt>Vertical speed</dt><dd>{Math.round(ac.vs)} ft/min</dd></div>
      <div><dt>Phase</dt><dd>{ac.phase.replaceAll("_", " ")}</dd></div>
      <div><dt>Squawk</dt><dd>{ac.squawk}</dd></div>
      <div><dt>Approach</dt><dd>{ac.approach_cleared ?? "—"}</dd></div>
      <div><dt>Radio</dt><dd>{!ac.on_frequency ? "Off frequency"
        : ac.transmitting ? "Transmitting" : "On frequency"}</dd></div>
    </dl>
    {ac.emergency && <p className="aircraft-caution">Emergency declared</p>}
    {relevant.length > 0 && <ul className="aircraft-alerts" aria-label="Aircraft alerts">
      {relevant.map((alert, index) => <li key={index}>{alert.severity} · {alert.message}</li>)}
    </ul>}
  </section>;
}
