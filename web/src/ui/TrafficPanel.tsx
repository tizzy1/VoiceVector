import type { AircraftPublic, StateMsg } from "../protocol";
import { levelLabel } from "../radar/geometry";
import { groupTraffic } from "./trafficGroups";
import "./traffic.css";

interface Props {
  state: StateMsg | null;
  selected: string;
  onSelect: (callsign: string) => void;
}

function FlightStrip({ aircraft: ac, selected, onSelect }: {
  aircraft: AircraftPublic; selected: string; onSelect: Props["onSelect"];
}) {
  return <button type="button" className="flight-strip"
    aria-pressed={selected === ac.callsign} onClick={() => onSelect(ac.callsign)}>
    <span className="flight-strip-heading">
      <strong>{ac.callsign}</strong><span>{ac.actype} · {ac.wake}</span>
    </span>
    <span className="flight-strip-name">{ac.spoken_plain}</span>
    <dl>
      <div><dt>Level</dt><dd>{levelLabel(ac.alt)}</dd></div>
      <div><dt>Cleared</dt><dd>{levelLabel(ac.cleared_alt)}</dd></div>
      <div><dt>GS</dt><dd>{Math.round(ac.gs)} kt</dd></div>
      <div><dt>Route</dt><dd>{ac.route_name ?? "—"}</dd></div>
    </dl>
    <span className="flight-strip-status">
      {ac.phase.replaceAll("_", " ")}
      {ac.next_fix && <span> · Next {ac.next_fix}</span>}
      {ac.emergency && <strong> · Emergency</strong>}
      {ac.transmitting && <span> · Transmitting</span>}
    </span>
  </button>;
}

export function TrafficPanel({ state, selected, onSelect }: Props) {
  const groups = groupTraffic(state?.aircraft ?? []);
  const departureCount = groups.queued.length + groups.departing.length + groups.airborne.length;
  const strips = (aircraft: AircraftPublic[]) => aircraft.map(ac => (
    <li key={ac.callsign}><FlightStrip aircraft={ac} selected={selected} onSelect={onSelect} /></li>
  ));
  return <section className="traffic-panel" aria-label="Traffic lists">
    <h2>Arrivals <span>{groups.arrivals.length}</span></h2>
    {!state ? <p className="muted">Waiting for aircraft state.</p>
      : groups.arrivals.length === 0 ? <p className="muted">No active arrivals.</p>
      : <ul aria-label="Arrivals">{strips(groups.arrivals)}</ul>}
    <h2>Departures <span>{departureCount}</span></h2>
    {!state ? <p className="muted">Waiting for aircraft state.</p>
      : departureCount === 0
        ? <p className="muted">No departures in this session.</p>
        : <>
          <h3>Queued · {groups.queued.length}</h3>
          {groups.queued.length > 0
            ? <ul aria-label="Queued departures">{strips(groups.queued)}</ul>
            : <p className="muted">No queued departures.</p>}
          {groups.departing.length > 0 && <>
            <h3>Taking off · {groups.departing.length}</h3>
            <ul aria-label="Departures taking off">{strips(groups.departing)}</ul>
          </>}
          <h3>Airborne · {groups.airborne.length}</h3>
          {groups.airborne.length > 0
            ? <ul aria-label="Airborne departures">{strips(groups.airborne)}</ul>
            : <p className="muted">No airborne departures.</p>}
        </>}
  </section>;
}
