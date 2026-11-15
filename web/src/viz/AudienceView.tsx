import { useEffect, useRef, useState, type ReactNode } from "react";
import type { CaptionMsg, EventMsg, SessionMsg, StateMsg } from "../protocol";
import { CaptionStrip } from "../ui/CaptionStrip";
import { Radar } from "../radar/Radar";
import { headingLabel, levelLabel } from "../radar/geometry";
import Scene from "./Scene";
import { activeAircraft, type SceneAirspace } from "./sceneData";
import type { CameraMode } from "./camera";
import { ForecastPanel } from "./ForecastPanel";
import { ForecastCrossings, type ForecastNotice } from "./forecasts";
import "./viz.css";

interface AudioControls {
  started: boolean; enabled: boolean; playback: boolean; volume: number; error: string;
  enable: () => void; disable: () => void; setPlayback: (value: boolean) => void; setVolume: (value: number) => void;
}
interface Props {
  state: StateMsg | null; data: SceneAirspace | null; session: SessionMsg | null; status: string;
  selected: string; onSelect: (callsign: string) => void; speakers: readonly string[];
  captions: readonly CaptionMsg[]; clocks: Readonly<Record<string, number>>; events: readonly EventMsg[];
  pipeline: ReactNode; audio: AudioControls; presenter: ReactNode; reportAction: ReactNode; reportOverlay: ReactNode; judge: boolean;
}

export default function AudienceView(props: Props) {
  const { state, data, selected, onSelect, audio } = props;
  const [mode, setMode] = useState<CameraMode>("overview"), [revision, setRevision] = useState(0);
  const [labels, setLabels] = useState(true), [trails, setTrails] = useState(true), [context, setContext] = useState(true);
  const [fallback, setFallback] = useState(false), [renderer, setRenderer] = useState(0);
  const [flightsOpen, setFlightsOpen] = useState(() => matchMedia("(min-width: 1050px)").matches);
  const [detailsOpen, setDetailsOpen] = useState(() => matchMedia("(min-width: 701px)").matches);
  const [forecastOn, setForecastOn] = useState(true);
  const [forecastNotices, setForecastNotices] = useState<ForecastNotice[]>([]);
  const crossings = useRef(new ForecastCrossings());
  const lastForecastTime = useRef(-1);
  useEffect(() => {
    const added = crossings.current.update(state);
    if (!state || state.t < lastForecastTime.current) setForecastNotices([]);
    lastForecastTime.current = state?.t ?? -1;
    if (added.length) setForecastNotices(previous => [...previous, ...added].slice(-5));
  }, [state]);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      const target = event.target;
      if (event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey
        || document.querySelector('[role="dialog"]')
        || (target instanceof HTMLElement && (target.isContentEditable || target.closest('input,textarea,select')))) return;
      if (event.key.toLowerCase() === 'p') { event.preventDefault(); setForecastOn(value => !value); }
    };
    window.addEventListener('keydown', key);
    return () => window.removeEventListener('keydown', key);
  }, []);
  const [cameraTarget, setCameraTarget] = useState("");
  const aircraft = activeAircraft(state?.aircraft ?? []);
  const target = aircraft.find(ac => ac.callsign === cameraTarget);
  const inspectionCallsign = (mode === "radio" || mode === "departure") && target ? target.callsign : selected;
  const focused = aircraft.find(ac => ac.callsign === inspectionCallsign);
  const onAir = aircraft.filter(ac => props.speakers.includes(ac.callsign) || ac.transmitting);
  const lastCaption = [...props.captions].reverse().find(message => onAir.some(ac => ac.callsign === message.speaker));
  const clock = lastCaption ? props.clocks[lastCaption.tx_id] : null;
  const spoken = lastCaption && clock != null && lastCaption.words.length
    ? lastCaption.words.filter(word => word.t0 <= clock).map(word => word.w).join(" ") : lastCaption?.text;
  const hasDepartures = aircraft.some(ac => ac.flow === "DEP");
  function camera(next: CameraMode) {
    if ((next === "lock" || next === "chase") && focused && focused.callsign !== selected) onSelect(focused.callsign);
    setMode(next); setRevision(value => value + 1);
  }
  function lock(callsign: string) {
    onSelect(callsign); setMode("lock"); setRevision(value => value + 1);
    if (matchMedia("(max-width: 700px)").matches) setFlightsOpen(false);
  }
  useEffect(() => {
    if ((mode === "lock" || mode === "chase") && !focused) camera("overview");
  }, [mode, focused?.callsign]);
  useEffect(() => {
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !event.defaultPrevented) camera("free");
    }
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);
  const cameraTitle = mode === "overview" ? "Airspace overview" : mode === "free" ? "Free camera"
    : mode === "radio" ? target ? `Following ${target.callsign}` : "Waiting for pilot radio"
      : mode === "departure" ? target ? `Departure · ${target.callsign}` : "Waiting for departure"
        : `${mode === "chase" ? "Chasing" : "Locked on"} ${focused?.callsign ?? "aircraft"}`;
  return <div className={`viz-page${flightsOpen ? " flights-open" : ""}${detailsOpen ? " details-open" : " details-closed"}${props.judge ? " judge-mode" : ""}${fallback ? " is-fallback" : ""}`}>
    <header className="viz-header">
      <a className="viz-brand" href="/">VOICE<span>VECTOR</span><small>AIRSPACE / LIVE</small></a>
      <div className="viz-airport"><b>GATWICK</b><span>EGKK / RWY {state?.runway ?? "—"}</span></div>
      <div className="viz-session"><span className={props.status === "Connected" ? "viz-connected" : ""}>● {props.status}</span>
        <span>{Math.floor((state?.t ?? 0) / 60).toString().padStart(2, "0")}:{Math.floor((state?.t ?? 0) % 60).toString().padStart(2, "0")}</span>
        <span>{state ? state.paused ? "PAUSED" : "LIVE SIMULATION" : "WAITING"}</span></div>
    </header>
    <div className="viz-tools">
      {props.presenter}{props.pipeline}<button disabled={fallback} aria-expanded={flightsOpen && !fallback} aria-controls="viz-flight-rail" onClick={() => setFlightsOpen(value => !value)}>Flights · {aircraft.length}</button>
      <button disabled={fallback || !focused} aria-expanded={detailsOpen && Boolean(focused)} aria-controls="viz-inspector"
        onClick={() => setDetailsOpen(value => !value)}>Flight details</button>
      <button aria-pressed={forecastOn} aria-controls="conflict-forecast" onClick={() => setForecastOn(value => !value)}>Forecast · P</button>
      <div className="viz-audio">
        {!audio.started ? <button className="viz-audio-enable" onClick={audio.enable} disabled={props.status !== "Connected"}>Enable room audio</button>
          : <><button onClick={audio.disable}>Disable audio</button><label><input type="checkbox" checked={audio.playback}
            onChange={event => audio.setPlayback(event.target.checked)} />Room audio</label>
            <label>Volume<input type="range" min="0" max="1" step=".05" value={audio.volume}
              onChange={event => audio.setVolume(Number(event.target.value))} aria-label="Room audio volume" /></label></>}
      </div>
      {!fallback && <button onClick={() => setFallback(true)} aria-label="Switch to 2D fallback">2D view</button>}
      {props.reportAction}<a href="/">Controller ↗</a>
    </div>
    {audio.error && <p className="viz-audio-error" role="status">{audio.error}</p>}
    <main className="viz-stage">
      {data && !fallback && <Scene key={renderer} state={state} data={data} selected={inspectionCallsign}
        mode={mode} revision={revision} speakers={props.speakers} labels={labels} trails={trails} context={context} forecasts={forecastOn}
        speech={lastCaption && spoken ? { callsign: lastCaption.speaker, text: spoken } : undefined}
        onSelect={lock} onManual={() => setMode("free")} onFailure={() => setFallback(true)} onTarget={setCameraTarget} />}
      {fallback && <div className="viz-fallback"><Radar state={state} data={data} selected={selected} onSelect={onSelect} activeSpeakers={props.speakers} /></div>}
      {!data && <div className="viz-loading" role="status">Loading Gatwick airspace…</div>}
      <div className="viz-camera-heading"><span>{fallback ? "2D FALLBACK" : "3D AIRSPACE"}</span><h1>{fallback ? "Radar view" : cameraTitle}</h1>
        <p>{fallback ? "Select aircraft on the radar or use its dropdown." : mode === "free" ? "Drag to pan · Right-drag to orbit · Scroll to zoom"
          : focused && (mode === "lock" || mode === "chase") ? focused.plain_status : "Click any aircraft to lock on · Drag to release camera"}</p>
        {fallback && <button onClick={() => { setFallback(false); setRenderer(value => value + 1); }}>Retry 3D</button>}
      </div>
      <aside id="viz-flight-rail" className={`viz-flight-rail${flightsOpen ? " is-open" : ""}`} aria-hidden={!flightsOpen} inert={!flightsOpen}>
        <h2>Live aircraft <span>{aircraft.length}</span></h2>
        {!aircraft.length && <p>{state ? "No airborne aircraft in this session." : "Waiting for aircraft state."}</p>}
        {aircraft.map(ac => <button key={ac.callsign} className={`viz-flight ${ac.flow === "DEP" ? "is-departure" : ""}`}
          aria-pressed={inspectionCallsign === ac.callsign} onClick={() => lock(ac.callsign)}>
          <span><b>{ac.callsign}</b><small>{ac.actype} / {ac.flow}</small></span>
          <span className="viz-flight-values">{levelLabel(ac.alt)} <i>→</i> {levelLabel(ac.cleared_alt)}<small>{Math.round(ac.gs)} kt</small></span>
          <span className="viz-flight-status">{ac.plain_status}</span>
          {(props.speakers.includes(ac.callsign) || ac.transmitting) && <em>● ON AIR</em>}
        </button>)}
      </aside>
      <aside id="viz-inspector" className={`viz-inspector${forecastOn ? " has-forecast" : ""}`}>
        {forecastOn && <ForecastPanel state={state} />}
        {detailsOpen && <>
        {focused ? <section className="viz-target" aria-label="Locked aircraft details">
          <div className="viz-target-heading"><span>{mode === "radio" || mode === "departure" ? "CAMERA TARGET" : "SELECTED AIRCRAFT"}</span><button aria-label="Clear aircraft selection" onClick={() => { onSelect(""); camera("overview"); }}>×</button></div>
          <h2>{focused.callsign}</h2><p>{focused.spoken_plain}</p>
          <p className="viz-target-status">{focused.plain_status}</p>
          <table><caption>Current / assigned</caption><thead><tr><th>Parameter</th><th>Current</th><th>Assigned</th></tr></thead><tbody>
            <tr><th>Level</th><td>{levelLabel(focused.alt)}</td><td>{levelLabel(focused.cleared_alt)}</td></tr>
            <tr><th>Heading</th><td>{headingLabel(focused.hdg)}°</td><td>{focused.tgt_hdg == null ? "—" : `${headingLabel(focused.tgt_hdg)}°`}</td></tr>
            <tr><th>IAS</th><td>{Math.round(focused.ias)} kt</td><td>{focused.tgt_ias == null ? "—" : `${Math.round(focused.tgt_ias)} kt`}</td></tr>
          </tbody></table>
          <dl><div><dt>Phase</dt><dd>{focused.phase.replaceAll("_", " ")}</dd></div>
            <div><dt>Vertical speed</dt><dd>{Math.round(focused.vs)} ft/min</dd></div>
            <div><dt>Route</dt><dd>{focused.route_name ?? "Vectors"}</dd></div>
            <div><dt>Next fix</dt><dd>{focused.next_fix ?? "—"}</dd></div></dl>
          {focused.traffic_source && <p className="viz-import-source">Imported {focused.traffic_source} snapshot · simulated</p>}
          <div className="viz-target-actions"><button onClick={() => camera("lock")}>Lock / orbit</button><button onClick={() => camera("chase")}>Chase aircraft</button></div>
        </section> : <section className="viz-select-hint"><span>FLIGHT INSPECTION</span><h2>Pick an aircraft.</h2><p>Click a 3D model or a flight strip to lock the camera and inspect its instructions.</p></section>}
        <section className="viz-event-feed" aria-label="Audience event feed"><h2>Session activity</h2>
          {forecastNotices.slice().reverse().map((notice, index) => <p key={`forecast:${index}`}><time>{Math.floor(notice.t / 60).toString().padStart(2, '0')}:{Math.floor(notice.t % 60).toString().padStart(2, '0')}</time>{notice.text}</p>)}
          {props.events.length === 0 && forecastNotices.length === 0 ? <p>Session events appear here.</p> : [...props.events].reverse().slice(0, 5).map((event, index) =>
            <p key={index}><time>{Math.floor(event.event.t / 60).toString().padStart(2, "0")}:{Math.floor(event.event.t % 60).toString().padStart(2, "0")}</time>{event.feed_text}</p>)}
        </section>
        </>}
      </aside>
      {lastCaption && <div className="viz-radio-bubble" role="status"><span>● {lastCaption.speaker} / RADIO</span><p>{spoken}</p></div>}
      <div className="viz-camera-bar" aria-label="Audience camera controls">
        {([['overview', 'Overview'], ['radio', 'Follow radio'], ['lock', 'Lock selected'], ['chase', 'Chase'], ['departure', 'Departure'], ['free', 'Free orbit']] as const).map(([value, label]) =>
          <button key={value} aria-pressed={mode === value} disabled={fallback || ((value === "lock" || value === "chase") && !focused) || (value === "departure" && !hasDepartures)} onClick={() => camera(value)}>{label}</button>)}
      </div>
      <div className="viz-layer-controls" aria-label="Scene layers">
        <label><input type="checkbox" checked={labels} onChange={event => setLabels(event.target.checked)} />Labels</label>
        <label><input type="checkbox" checked={trails} onChange={event => setTrails(event.target.checked)} />Trails</label>
        <label><input type="checkbox" checked={context} onChange={event => setContext(event.target.checked)} />Airspace</label>
      </div>
      <div className="viz-scene-note">Altitude: true scale · Aircraft symbols enlarged · Simulation only</div>
    </main>
    <CaptionStrip messages={props.captions} clocks={props.clocks} />
    <footer className="viz-footer"><span>EGKK / AIRSPACE OBSERVATORY</span><span>SESSION {props.session?.code ?? "—"} · {aircraft.length} AIRBORNE</span></footer>
    {props.reportOverlay}
  </div>;
}
