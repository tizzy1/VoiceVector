import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { ControlMsg, CaptionMsg, CoachMsg, EventMsg, SessionMsg, StateMsg, Report, PipelineTrace } from "../protocol";
import { Radar, type Airspace } from "../radar/Radar";
import { connect } from "./connection";
import { createAudioEngine } from "../audio/index";
import { RadioLink, isTyping, type RadioStatus } from "./radioLink";
import { FlightLists } from "../ui/FlightLists";
import { TracePanel } from "../ui/TracePanel";
import { updateTraces } from "../ui/traces";
import { AircraftDetails } from "../ui/AircraftDetails";
import { WeatherPanel } from "../ui/WeatherPanel";
import { CommunicationsPanel } from "../ui/CommunicationsPanel";
import { updateResponseSamples, type ResponseSample } from "../ui/communications";
import { CaptionStrip } from "../ui/CaptionStrip";
import { CoachCard, mergeCoach } from "../ui/CoachCard";
import { updateCaptions } from "../ui/captions";
import { SessionControls, type TrafficConfig } from "../ui/SessionControls";
import { PresenterConsole } from "../ui/PresenterConsole";
import { ReportDialog, ReportPage } from "../report/ReportView";
import { SnapshotPanel } from "../ui/SnapshotPanel";
import { AlertsEventsPanel } from "../ui/AlertsEventsPanel";
import "./controller.css";
import type { SceneAirspace } from "../viz/sceneData";
const AudienceView = lazy(() => import("../viz/AudienceView"));

interface Health { ready: boolean; simulation_ready: boolean; missing_modules: string[] }

// ---- 15 Nov additions (Sanju for Bhavin); see docs/HANDOVER_15NOV.md section 4
type ScenarioChoice = "demo" | "judge" | "medium" | "busy";
const SCENARIOS: [ScenarioChoice, string][] = [
  ["demo", "Demo (scripted)"], ["judge", "Judge: land one aircraft"],
  ["medium", "Random traffic"], ["busy", "Random traffic (busy)"],
];
// Contact frequencies (display; the simulator accepts any frequency on "contact")
const FREQUENCIES: [string, string][] = [["DIR", "126.825"], ["TWR", "124.225"], ["LON", "134.125"]];
function vizHref(code?: string) { return `/viz${code ? `?session=${encodeURIComponent(code)}` : ""}`; }
function readFlag(key: string): boolean { try { return localStorage.getItem(key) === "1"; } catch { return false; } }
function writeFlag(key: string, on: boolean) { try { localStorage.setItem(key, on ? "1" : "0"); } catch { /* private mode */ } }

export function App() {
  const reportPage = location.pathname === "/report";
  const role = location.pathname === "/viz" || reportPage ? "viz" : "radar";
  const [status, setStatus] = useState("Connecting");
  const [session, setSession] = useState<SessionMsg | null>(null);
  const [state, setState] = useState<StateMsg | null>(null);
  const [data, setData] = useState<Airspace | null>(null);
  const [traffic, setTraffic] = useState<TrafficConfig | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [captions, setCaptions] = useState<CaptionMsg[]>([]);
  const [responseSamples, setResponseSamples] = useState<ResponseSample[]>([]);
  const sessionCode = useRef<string | null>(null);
  const [events, setEvents] = useState<EventMsg[]>([]);
  const [text, setText] = useState("");
  const [selected, setSelected] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(true);
  // 15 Nov (Sanju for Bhavin): radar first. Side panel and communications bar start closed (one
  // click away); the controller works by voice, so the bottom history is rarely needed.
  const [contextOpen, setContextOpen] = useState(false);
  const [commsOpen, setCommsOpen] = useState(() => readFlag("sweatbox.commsOpen"));
  const [scenarioChoice, setScenarioChoice] = useState<ScenarioChoice>("demo");
  const clearanceInput = useRef<HTMLInputElement>(null);
  const [traces,setTraces] = useState<PipelineTrace[]>([]);
  const [coach, setCoach] = useState<CoachMsg | null>(null);   // phraseology coach (15 Nov, Sanju)
  const [pipelineOpen,setPipelineOpen] = useState(false);
  const pipelineToggle = useRef<HTMLButtonElement>(null);
  const sidebarToggle = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState("");
  const [judge, setJudge] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [reportPending, setReportPending] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const radio = useRef<RadioLink | null>(null);
  const [radioStatus, setRadioStatus] = useState<RadioStatus>({
    enabled: false, micReady: false, keyed: false, error: "",
  });
  const [audioStarted, setAudioStarted] = useState(false);
  const [playback, setPlayback] = useState(true);
  const [level, setLevel] = useState(0);
  const [volume, setVolume] = useState(1);
  const [speakers, setSpeakers] = useState<string[]>([]);
  const [captionClock, setCaptionClock] = useState<Record<string, number>>({});
  const connection = useRef<ReturnType<typeof connect> | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    async function load() {
      try {
        const response = await fetch("/api/data/egkk", { signal: abort.signal });
        if (!response.ok) throw new Error("Server data unavailable");
        setData(await response.json());
        const trafficResponse = await fetch("/api/data/traffic", { signal: abort.signal });
        if (!trafficResponse.ok) throw new Error("Traffic presets unavailable");
        setTraffic(await trafficResponse.json());
      } catch (reason) {
        if (!abort.signal.aborted) setError(String(reason));
      }
    }
    if (!reportPage) void load();
    const client = connect(role, message => {
      radio.current?.handle(message);
      if(message.type === "trace") setTraces(previous=>updateTraces(previous,message.trace));
      if(message.type === "coach") setCoach(previous=>mergeCoach(previous,message));
      if (message.type === "session") {
        if (sessionCode.current && sessionCode.current !== message.code) {
          setTraces([]);
          setCaptions([]);
          setResponseSamples([]);
          setReport(null);
          setReportOpen(false);
          setReportPending(false);
        }
        sessionCode.current = message.code;
        setSession(message);
      }
      if (message.type === "state") setState(message);
      if (message.type === "report") {
        radio.current?.up();
        setReport(message.report);
        setReportOpen(true);
        setReportPending(false);
      }
      if (message.type === "caption") {
        if (message.speaker === "SYSTEM") setReportPending(false);
        setCaptions(previous => updateCaptions(previous, message));
        setResponseSamples(previous => updateResponseSamples(previous, message));
      }
      if (message.type === "event") {
        if (message.event.type === "session_resume") {
          setReport(null);
          setReportOpen(false);
          setReportPending(false);
        }
        if (message.event.type === "session_start") {
          setTraces([]);
          setCaptions([]);
          setResponseSamples([]);
          setReport(null);
          setReportOpen(false);
          setReportPending(false);
        }
        setEvents(previous => [...previous, message].slice(-12));
      }
    }, nextStatus => {
      if (nextStatus !== "Connected") {
        setTraces([]);
        setState(null);
        setReportPending(false);
        radio.current?.dispose();
        radio.current = null;
        setAudioStarted(false);
        setRadioStatus({ enabled: false, micReady: false, keyed: false, error: "" });
        setSpeakers([]);
        setCaptionClock({});
      }
      setStatus(nextStatus);
    });
    connection.current = client;
    return () => {
      abort.abort();
      radio.current?.dispose();
      radio.current = null;
      client.close();
    };
  }, [role, reportPage]);

  useEffect(() => {
    if (status !== "Connected") return;
    const abort = new AbortController();
    async function refreshHealth() {
      try {
        const response = await fetch("/api/health", { signal: abort.signal });
        if (!response.ok) throw new Error("Integration status unavailable");
        setHealth(await response.json());
      } catch (reason) {
        if (!abort.signal.aborted) {
          setHealth(null);
          setError(String(reason));
        }
      }
    }
    void refreshHealth();
    return () => { abort.abort(); };
  }, [status]);

  useEffect(() => {
    if (!state?.aircraft.some(ac => ac.callsign === selected
      && ac.phase !== "LANDED" && ac.phase !== "HANDED_OFF")) setSelected("");
  }, [state, selected]);

  useEffect(() => {
    if (reportPage || reportOpen) return;
    const timer = setInterval(() => {
      setLevel(radio.current?.level() ?? 0);
      const clocks: Record<string, number> = {};
      for (const caption of captions.slice(-3)) {
        const time = radio.current?.clock(caption.tx_id);
        if (time != null) clocks[caption.tx_id] = time;
      }
      setCaptionClock(clocks);
    }, 100);
    return () => clearInterval(timer);
  }, [captions, reportPage, reportOpen]);

  useEffect(() => {
    function release() { radio.current?.up(); }
    function down(event: KeyboardEvent) {
      if (reportPage || reportOpen) return;
      if (event.code !== "Space" || event.repeat || isTyping(event.target)) return;
      if (status !== "Connected" || !health?.ready || !radioStatus.micReady || role !== "radar") return;
      event.preventDefault();
      radio.current?.down();
    }
    function up(event: KeyboardEvent) {
      if (event.code === "Space") {
        if (radioStatus.keyed) event.preventDefault();
        release();
      }
    }
    function visibility() { if (document.hidden) release(); }
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", release);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", release);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [status, health?.ready, radioStatus.micReady, radioStatus.keyed, role, reportPage, reportOpen]);

  useEffect(() => {
    radio.current?.playback(playback);
    radio.current?.volume(volume);
  }, [playback, volume, radioStatus.enabled]);

  function stopRadio() {
    radio.current?.dispose();
    radio.current = null;
    setAudioStarted(false);
    setRadioStatus({ enabled: false, micReady: false, keyed: false, error: "" });
    setSpeakers([]);
    setCaptionClock({});
  }

  async function enableRadio() {
    if (radio.current || !connection.current) return;
    setAudioStarted(true);
    const link = new RadioLink(createAudioEngine(), connection.current, role, setRadioStatus, activity => {
      setSpeakers(previous => activity.state === "start"
        ? [...new Set([...previous, activity.speaker])]
        : previous.filter(speaker => speaker !== activity.speaker));
    });
    radio.current = link;
    link.volume(volume);
    await link.enable(playback);

  }

  function selectAircraft(callsign: string) {
    setSelected(callsign);

  }

  useEffect(()=>{
    function key(event:KeyboardEvent){
      const editing = event.target instanceof HTMLElement && (event.target.isContentEditable || Boolean(event.target.closest('input,textarea,select')));
      if(reportPage || reportOpen || editing || event.defaultPrevented || event.repeat || event.ctrlKey || event.metaKey || event.altKey) return;
      if(event.key.toLowerCase()==='t'){event.preventDefault();setPipelineOpen(v=>!v);}
      if(event.key==='Escape' && pipelineOpen){event.preventDefault();setPipelineOpen(false);pipelineToggle.current?.focus();}
    }
    window.addEventListener('keydown',key);
    return ()=>window.removeEventListener('keydown',key);
  },[pipelineOpen,reportPage,reportOpen]);
  const pipeline = <><button ref={pipelineToggle} aria-expanded={pipelineOpen} onClick={()=>setPipelineOpen(v=>!v)}>PIPELINE · T</button>
    {pipelineOpen && <TracePanel traces={traces} audience={role==='viz'} onClose={()=>{setPipelineOpen(false);pipelineToggle.current?.focus();}}/>}</>;

  const connected = status === "Connected";
  function sendControl(message: ControlMsg) {
    if (!connection.current?.send(message)) {
      setError("Connection unavailable. Reconnect before changing the simulation.");
      return false;
    }
    setError("");
    return true;
  }

  function presenterBeat(key: 1 | 4 | 8) {
    radio.current?.up();
    if (sendControl({ type: "control", action: "beat", params: { key } }) && key === 8) {
      setReportPending(true);
    }
  }

  function downloadReport() {
    if (!report) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = `sweatbox-${report.session_id.replace(/[^a-zA-Z0-9_-]/g, "_")}-report.json`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function control(action: "pause" | "resume" | "load_scenario") {
    const params = action === "load_scenario" ? { scenario: scenarioChoice, paused: false } : {};
    if (!connection.current?.send({ type: "control", action, params })) {
      setError("Connection unavailable. Reconnect before changing the simulation.");
    }
  }
  if (reportPage) return <ReportPage report={report} connectionStatus={status} onDownload={downloadReport} />;
  const reportHref = `/report${session ? `?session=${encodeURIComponent(session.code)}` : ""}`;
  if (role === "viz") return <Suspense fallback={<div className="viz-loading">Loading audience view…</div>}>
    <AudienceView state={state} data={data as SceneAirspace | null} session={session} status={status}
      selected={selected} onSelect={selectAircraft} speakers={speakers} captions={captions} clocks={captionClock}
      pipeline={pipeline} events={events} judge={judge} audio={{ started: audioStarted, enabled: radioStatus.enabled,
        playback, volume, error: radioStatus.error, enable: enableRadio, disable: stopRadio,
        setPlayback, setVolume }}
      presenter={<PresenterConsole enabled={connected && Boolean(health?.simulation_ready) && !reportOpen}
        radioReady={Boolean(health?.ready)} judge={judge} onJudge={() => setJudge(value => !value)}
        reportPending={reportPending} reportReady={Boolean(report)} onBeat={presenterBeat} />}
      reportAction={reportPending ? <span>Generating debrief…</span> : report ? <button onClick={() => setReportOpen(true)}>Review debrief</button> : null}
      reportOverlay={report && reportOpen && <ReportDialog report={report} open={reportOpen} href={reportHref}
        onClose={() => setReportOpen(false)} onDownload={downloadReport} />} />
  </Suspense>;
  const contextPanels = <>
    {role === "radar" && <WeatherPanel metar={state?.metar} connected={connected} />}
    <AircraftDetails aircraft={state?.aircraft.find(ac => ac.callsign === selected)} alerts={state?.alerts ?? []} />
    <details className="operations-settings"><summary>Session controls</summary>
      <SessionControls session={session} state={state} traffic={traffic} selected={selected}
        enabled={connected && Boolean(health?.simulation_ready)} radioReady={Boolean(health?.ready)} send={sendControl} />
    </details>
    <SnapshotPanel state={state} enabled={connected && Boolean(health?.simulation_ready)} controls={role === "radar"} send={sendControl} />
    <details className="connection-details"><summary>Connection / second device</summary>
      <p className={health?.simulation_ready ? "success" : "muted"}>{health?.simulation_ready ? "Simulator ready" : "Awaiting simulator"}</p>
      <p className={health?.ready ? "success" : "muted"}>{health?.ready ? "Radio ready" : "Radio pipeline pending"}</p>
      {health?.missing_modules.map(module => <code key={module}>{module}</code>)}
      <p>Open on the same network:</p>{session && <a className="join" href={session.join_url}>{session.join_url}</a>}
    </details>
  </>;
  return (
    <div className={`shell ${role === "radar" ? "controller-shell" : "audience-shell"}${judge ? " judge-mode" : ""}`}
      >
      <header>
        <div><strong>SWEATBOX<span>AI</span></strong><small>GATWICK DIRECTOR</small></div>
        <nav aria-label="Views"><a href="/">Radar</a>
          <a href={vizHref(session?.code)} onClick={event => {
            // 15 Nov (Sanju for Bhavin): the second screen opens in its own window (drag it to the projector)
            if (window.open(vizHref(session?.code), "sweatbox-second-screen", "popup,width=1280,height=800")) event.preventDefault();
          }}>Second screen ↗</a><a href={reportHref}>Report</a></nav>
        <div className="connection"><i className={connected ? "online" : ""} />{status}</div>
      </header>
      <div className="session-bar">
        <span>EGKK · RWY {state?.runway ?? "26L"}</span>
        <span>{state?.aircraft.length ?? 0} aircraft</span>
        <span>{Math.floor((state?.t ?? 0) / 60).toString().padStart(2, "0")}:
          {Math.floor((state?.t ?? 0) % 60).toString().padStart(2, "0")}</span>
        <span>SESSION {session?.code ?? "—"}</span>
        <span>{state ? state.paused ? "PAUSED" : "RUNNING" : "WAITING"}</span>
        <button disabled={!connected || !health?.simulation_ready}
          onClick={() => control(state?.paused ? "resume" : "pause")}>
          {state?.paused ? "Resume" : "Pause"}
        </button>
        <label className="scenario-pick">Scenario <select aria-label="Scenario" value={scenarioChoice}
          onChange={event => setScenarioChoice(event.target.value as ScenarioChoice)}>
          {SCENARIOS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select></label>
        <button disabled={!connected || !health?.simulation_ready}
          onClick={() => control("load_scenario")}>{scenarioChoice === "demo" ? "Reset demo" : "Load scenario"}</button>
        <span className="frequencies" title="Frequencies for contact instructions">
          {FREQUENCIES.map(([unit, freq]) => <span key={unit}>{unit} <b>{freq}</b></span>)}
        </span>
      </div>
      <div className="panel-toolbar">
        <PresenterConsole enabled={connected && Boolean(health?.simulation_ready) && !reportOpen}
          radioReady={Boolean(health?.ready)} judge={judge} onJudge={() => setJudge(value => !value)}
          reportPending={reportPending} reportReady={Boolean(report)} onBeat={presenterBeat} />
        {role === "radar" && <div className="workspace-controls">
          <button aria-expanded={contextOpen} aria-controls="controller-context" onClick={() => setContextOpen(value => !value)}>
            Aircraft / weather</button>
        </div>}
        {pipeline}
        <button ref={sidebarToggle} aria-expanded={sidebarOpen} aria-controls="docked-flight-lists"
          onClick={() => setSidebarOpen(open => !open)}>
          {sidebarOpen ? "Hide arrivals / departures" : "Show arrivals / departures"}
        </button>
      </div>
      {sidebarOpen && <div id="docked-flight-lists"><FlightLists state={state} data={data} selected={selected} onSelect={selectAircraft}
        enabled={connected && Boolean(health?.simulation_ready)} send={sendControl}/></div>}
      {(reportPending || report) && <div className="report-notice" role="status">
        {reportPending ? <span>Generating session report…</span> : report && <>
          <span>Session ended · Score {report.score} / 100 · {report.findings.length} findings</span>
          <button onClick={() => { radio.current?.up(); setReportOpen(true); }}>View report</button>
          <button onClick={downloadReport}>Download report JSON</button>
        </>}
      </div>}
      <main className={`${sidebarOpen ? "sidebar-open" : "sidebar-closed"} ${contextOpen ? "context-open" : "context-closed"}`}>
        {role === "radar" && <aside id="controller-context" className="controller-context"
          aria-label="Aircraft and weather" aria-hidden={!contextOpen} inert={!contextOpen}>
          <div className="context-content">{contextPanels}</div>
        </aside>}
        <section className="scope">
          <Radar state={state} data={data} activeSpeakers={speakers} selected={selected} onSelect={selectAircraft}
            onHeadingBug={bug => {
              setText(`${bug.callsign} turn ${bug.turn === "L" ? "left" : "right"} heading ${String(bug.heading).padStart(3, "0")}`);
              clearanceInput.current?.focus();
            }} />
          {health && !health.simulation_ready && (
            <div className="waiting">
              <h1>Platform connected</h1>
              <p>Waiting for the simulator implementation.</p>
              <p>The scope will display live aircraft when the simulator is available.</p>
            </div>
          )}
          {role === "radar" && <CoachCard coach={coach} />}
        </section>
      </main>
      {role === "radar" && <div className="comms-toggle-row">
        <button aria-expanded={commsOpen} aria-controls="controller-bottom" onClick={() => setCommsOpen(open => {
          writeFlag("sweatbox.commsOpen", !open); return !open; })}>
          {commsOpen ? "▾ Hide communications / alerts" : `▸ Communications / alerts${state?.alerts.length ? ` · ${state.alerts.length} active` : ""}`}
        </button></div>}
      {role === "radar" && commsOpen && <section id="controller-bottom" className="controller-bottom" aria-label="Operational communications and alerts">
        <CommunicationsPanel messages={captions} samples={responseSamples} selected={selected} />
        <AlertsEventsPanel state={state} events={events} onSelect={selectAircraft} />
      </section>}
      <div className="radio-controls" aria-label="Radio controls">
        {!audioStarted
          ? <button disabled={!connected} onClick={enableRadio}>
            {role === "radar" ? "Enable microphone and pilot audio" : "Enable room audio"}
          </button>
          : <><span>{radioStatus.enabled ? "Audio enabled" : "Starting audio…"}</span>
            <button onClick={stopRadio}>Disable audio</button></>}
        <label><input type="checkbox" checked={playback} onChange={event => {
          setPlayback(event.target.checked); radio.current?.playback(event.target.checked);
        }} />Pilot playback</label>
        <label>Volume<input type="range" aria-label="Pilot volume" min="0" max="1"
          step="0.05" value={volume} onChange={event => setVolume(Number(event.target.value))} /></label>
        {role === "radar" && <>
          <button data-ptt disabled={!connected || !health?.ready || !radioStatus.micReady}
            aria-pressed={radioStatus.keyed}
            onPointerDown={event => {
              if (event.button !== 0) return;
              event.preventDefault(); event.currentTarget.focus();
              event.currentTarget.setPointerCapture(event.pointerId);
              radio.current?.down();
            }}
            onPointerUp={() => radio.current?.up()}
            onPointerCancel={() => radio.current?.up()}
            onLostPointerCapture={() => radio.current?.up()}
            onKeyDown={event => {
              if (event.key === " " || event.key === "Enter") {
                event.preventDefault();
                if (!event.repeat) radio.current?.down();
              }
            }}
            onKeyUp={event => {
              if (event.key === " " || event.key === "Enter") {
                event.preventDefault(); radio.current?.up();
              }
            }}
            onBlur={() => radio.current?.up()}>
            {radioStatus.keyed ? "Transmitting · release to send" : "Hold to talk · Space"}
          </button>
          <meter aria-label="Microphone level" min={0} max={1} value={level} />
        </>}
        {radioStatus.error && <span role="status">{radioStatus.error} · Typed input remains available.</span>}
        {role === "radar" && radioStatus.enabled && !radioStatus.micReady && !radioStatus.error
          && <span>Waiting for microphone permission…</span>}
      </div>
      <CaptionStrip messages={captions} clocks={captionClock} />
      {role === "radar" && (
        <>
        <form onSubmit={event => {
          event.preventDefault();
          if (connection.current?.send({ type: "typed", text })) {
            setText(""); setError("");
          } else setError("Connection unavailable. Your clearance has been kept.");
        }}>
          <label htmlFor="clearance">Typed clearance</label>
          <input id="clearance" ref={clearanceInput} value={text} maxLength={2000}
            placeholder="BAW117 turn left heading 240"
            onChange={event => setText(event.target.value)} />
          <button disabled={!connected || !health?.ready || !text.trim()}>Transmit</button>
        </form>
        {health && !health.ready && <p className="input-status">
          Typed and spoken clearances become available when the radio pipeline is loaded.
        </p>}
        </>
      )}
      {error && <p role="alert" className="error">{error}</p>}
      <footer>CONTROLLER WORKSTATION
        <span>{connected && health?.simulation_ready ? "LIVE SIMULATION" : "AWAITING CONNECTION"}</span></footer>
      {report && reportOpen && <ReportDialog report={report} open={reportOpen} href={reportHref}
        onClose={() => setReportOpen(false)} onDownload={downloadReport} />}
    </div>
  );
}
