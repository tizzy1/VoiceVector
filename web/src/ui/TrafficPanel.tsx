import { useEffect, useId, useRef, useState } from "react";
import type { AircraftPublic, StateMsg } from "../protocol";
import { headingLabel, levelLabel } from "../radar/geometry";
import { groupTraffic, matchesCallsign, type TrafficSort } from "./trafficGroups";
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
      <div><dt title="Observed / assigned heading">HDG / CLR</dt><dd>{headingLabel(ac.hdg)} / {ac.tgt_hdg == null ? "—" : headingLabel(ac.tgt_hdg)}</dd></div>
      <div><dt title="Observed / assigned indicated airspeed in knots">IAS / CLR</dt><dd>{Math.round(ac.ias)} / {ac.tgt_ias == null ? "—" : Math.round(ac.tgt_ias)}</dd></div>
    </dl>
    <span className="flight-strip-route">Route · {ac.route_name ?? "—"}{ac.next_fix ? ` → ${ac.next_fix}` : ""}</span>
    <span className="flight-strip-status">
      {ac.phase.replaceAll("_", " ")}
      {ac.emergency && <strong> · Emergency</strong>}
      {ac.transmitting && <span> · Transmitting</span>}
      {ac.traffic_source && <span> · Imported simulation</span>}
    </span>
  </button>;
}

export function TrafficPanel({ state, selected, onSelect }: Props) {
  const panel = useRef<HTMLElement>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<TrafficSort>("session");
  const filterId = useId();
  const sortId = useId();
  const scrollSelection = useRef(false);
  // A newly selected radar track must be visible even if the old search excludes it.
  useEffect(() => {
    scrollSelection.current = Boolean(selected);
    if (selected) setQuery(current => matchesCallsign(selected, current) ? current : "");
  }, [selected]);
  useEffect(() => {
    if (!scrollSelection.current) return;
    const row = panel.current?.querySelector<HTMLElement>('[aria-pressed="true"]');
    if (!row) return;
    scrollSelection.current = false;
    const containers = [row.closest("ul"), row.closest(".sidebar-content")];
    for (const container of containers) {
      if (!(container instanceof HTMLElement)) continue;
      const bounds = container.getBoundingClientRect();
      const target = row.getBoundingClientRect();
      const detail = container.classList.contains("sidebar-content")
        ? container.querySelector(".aircraft-details") : null;
      const top = bounds.top + (detail?.getBoundingClientRect().height ?? 0);
      if (target.top < top) container.scrollTop += target.top - top;
      else if (target.bottom > bounds.bottom) container.scrollTop += target.bottom - bounds.bottom;
    }
  }, [selected, query, sort, state]);
  const allGroups = groupTraffic(state?.aircraft ?? []);
  const groups = groupTraffic(state?.aircraft ?? [], query, sort);
  const filtering = Boolean(query.trim());
  const selectedHidden = filtering && selected && !matchesCallsign(selected, query);
  const departureCount = groups.queued.length + groups.departing.length + groups.airborne.length;
  const totalDepartures = allGroups.queued.length + allGroups.departing.length + allGroups.airborne.length;
  const strips = (aircraft: AircraftPublic[]) => aircraft.map(ac => (
    <li key={ac.callsign}><FlightStrip aircraft={ac} selected={selected} onSelect={onSelect} /></li>
  ));
  return <section ref={panel} className="traffic-panel" aria-label="Traffic lists">
    <div className="traffic-list-controls">
      <label htmlFor={filterId}>Filter callsign</label>
      <div className="traffic-search">
        <input id={filterId} type="search" value={query} placeholder="e.g. BAW"
          maxLength={32} autoComplete="off" spellCheck={false}
          onChange={event => setQuery(event.target.value)} />
        <button type="button" disabled={!query} onClick={() => setQuery("")}>Clear</button>
      </div>
      <label htmlFor={sortId}>Sort flights</label>
      <select id={sortId} value={sort} onChange={event => {
        scrollSelection.current = Boolean(selected);
        setSort(event.target.value as TrafficSort);
      }}>
        <option value="session">Session order</option>
        <option value="callsign-asc">Callsign A–Z</option>
        <option value="callsign-desc">Callsign Z–A</option>
        <option value="altitude-asc">Altitude low–high</option>
        <option value="altitude-desc">Altitude high–low</option>
      </select>
      <p className="traffic-filter-note">Lists only · radar traffic stays visible</p>
      {selectedHidden && <div className="traffic-filter-selection">
        <p>Selected {selected} is hidden by this filter.</p>
        <button type="button" onClick={() => {
          scrollSelection.current = true;
          setQuery("");
        }}>Show selected</button>
      </div>}
    </div>
    <h2>Arrivals <span>{groups.arrivals.length}{filtering ? ` / ${allGroups.arrivals.length}` : ""}</span></h2>
    {!state ? <p className="muted">Waiting for aircraft state.</p>
      : groups.arrivals.length === 0 ? <p className="muted">{filtering && allGroups.arrivals.length
        ? "No arrivals match this callsign." : "No active arrivals."}</p>
      : <ul aria-label="Arrivals">{strips(groups.arrivals)}</ul>}
    <h2>Departures <span>{departureCount}{filtering ? ` / ${totalDepartures}` : ""}</span></h2>
    {!state ? <p className="muted">Waiting for aircraft state.</p>
      : departureCount === 0
        ? <p className="muted">{filtering && totalDepartures
          ? "No departures match this callsign." : "No departures in this session."}</p>
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
