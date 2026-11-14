import { useEffect, useRef, useState } from "react";
import type { CaptionMsg, EventMsg, SessionMsg, StateMsg } from "../protocol";
import { Radar, type Airspace } from "../radar/Radar";
import { connect } from "./connection";

interface Health { ready: boolean; simulation_ready: boolean; missing_modules: string[] }

export function App() {
  const role = location.pathname === "/viz" ? "viz" : "radar";
  const [status, setStatus] = useState("Connecting");
  const [session, setSession] = useState<SessionMsg | null>(null);
  const [state, setState] = useState<StateMsg | null>(null);
  const [data, setData] = useState<Airspace | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [captions, setCaptions] = useState<CaptionMsg[]>([]);
  const [events, setEvents] = useState<EventMsg[]>([]);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const connection = useRef<ReturnType<typeof connect> | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    async function load() {
      try {
        const response = await fetch("/api/data/egkk", { signal: abort.signal });
        if (!response.ok) throw new Error("Server data unavailable");
        setData(await response.json());
      } catch (reason) {
        if (!abort.signal.aborted) setError(String(reason));
      }
    }
    void load();
    const client = connect(role, message => {
      if (message.type === "session") setSession(message);
      if (message.type === "state") setState(message);
      if (message.type === "caption") {
        setCaptions(previous => [
          ...previous.filter(c => c.tx_id !== message.tx_id || c.speaker !== message.speaker),
          message,
        ].slice(-30));
      }
      if (message.type === "event") setEvents(previous => [...previous, message].slice(-12));
    }, nextStatus => {
      if (nextStatus !== "Connected") setState(null);
      setStatus(nextStatus);
    });
    connection.current = client;
    return () => { abort.abort(); client.close(); };
  }, [role]);

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

  const connected = status === "Connected";
  function control(action: "pause" | "resume" | "load_scenario") {
    const params = action === "load_scenario" ? { scenario: "demo", paused: false } : {};
    if (!connection.current?.send({ type: "control", action, params })) {
      setError("Connection unavailable. Reconnect before changing the simulation.");
    }
  }
  return (
    <div className="shell">
      <header>
        <div><strong>SWEATBOX<span>AI</span></strong><small>GATWICK DIRECTOR</small></div>
        <nav aria-label="Views"><a href="/">Radar</a><a href="/viz">Second screen</a></nav>
        <div className="connection"><i className={connected ? "online" : ""} />{status}</div>
      </header>
      <div className="session-bar">
        <span>EGKK · RWY {state?.runway ?? "26L"}</span>
        <span>{state?.aircraft.length ?? 0} aircraft</span>
        <span>{Math.floor((state?.t ?? 0) / 60).toString().padStart(2, "0")}:
          {Math.floor((state?.t ?? 0) % 60).toString().padStart(2, "0")}</span>
        <span>SESSION {session?.code ?? "—"}</span>
        <span>{state?.paused ? "PAUSED" : "RUNNING"}</span>
        <button disabled={!connected || !health?.simulation_ready}
          onClick={() => control(state?.paused ? "resume" : "pause")}>
          {state?.paused ? "Resume" : "Pause"}
        </button>
        <button disabled={!connected || !health?.simulation_ready}
          onClick={() => control("load_scenario")}>Reset demo</button>
      </div>
      <main>
        <section className="scope">
          <Radar state={state} data={data} />
          {health && !health.simulation_ready && (
            <div className="waiting">
              <h1>Platform connected</h1>
              <p>Waiting for the simulator implementation.</p>
              <p>The scope will display live aircraft when the simulator is available.</p>
            </div>
          )}
        </section>
        <aside>
          <h2>Integration status</h2>
          <p className={health?.simulation_ready ? "success" : "muted"}>
            {health?.simulation_ready ? "Simulator loaded" : "Awaiting simulator"}
          </p>
          <p className={health?.ready ? "success" : "muted"}>
            {health?.ready ? "Radio pipeline loaded" : "Radio pipeline pending"}</p>
          {health?.missing_modules.map(module => <code key={module}>{module}</code>)}
          <h2>Second device</h2>
          <p>Open this address on the same network.</p>
          {session && <a className="join" href={session.join_url}>{session.join_url}</a>}
          <h2>Event feed</h2>
          {events.length === 0 && <p className="muted">Session events will appear here.</p>}
          {events.map((event, index) => <p key={index}>{event.feed_text}</p>)}
        </aside>
      </main>
      <section className="radio" aria-label="Radio transcript" aria-live="polite">
        {captions.length === 0 && <p className="muted">Controller clearances and pilot readbacks appear here.</p>}
        {captions.slice(-3).map(caption => (
          <div key={caption.tx_id + caption.speaker} className="transmission">
            <b>{caption.speaker}</b><div>{caption.text}
              {caption.plain_english && <small>{caption.plain_english}</small>}
            </div>
          </div>
        ))}
      </section>
      {role === "radar" && (
        <>
        <form onSubmit={event => {
          event.preventDefault();
          if (connection.current?.send({ type: "typed", text })) {
            setText(""); setError("");
          } else setError("Connection unavailable. Your clearance has been kept.");
        }}>
          <label htmlFor="clearance">Typed clearance</label>
          <input id="clearance" value={text} maxLength={2000}
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
      <footer>{role === "viz" ? "SECOND SCREEN · FOUNDATION VIEW" : "CONTROLLER · TYPED INPUT"}
        <span>{health?.simulation_ready ? "LIVE SIMULATION" : "INTEGRATION PENDING"}</span></footer>
    </div>
  );
}
