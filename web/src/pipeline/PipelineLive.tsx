// Live pipeline screen: open /pipeline on any device on the same network (e.g. a laptop next to the
// demo machine). Every time the controller keys the mic, an API is called or a pilot declares a
// MAYDAY, the stage lights up and a packet runs along the wire. Read-only (role "viz"). 15 Nov, Sanju.
import { useEffect, useReducer, useRef, useState } from "react";
import { connect } from "../app/connection";
import type { ActivityNode, ServerMsg, SessionMsg } from "../protocol";
import {
  API_NODES, EDGES, GEO, HEIGHT, LANES, NODES, NODE_IDS, PACKET_MS, applyMessage, edgePath, fmtMs,
  initialState, laneY, nodeBox, nodeLook, pointOn, type PipelineState,
} from "./pipelineModel";
import "./pipeline.css";

type Action = { msg: ServerMsg; now: number } | { reset: true };
function reducer(s: PipelineState, a: Action): PipelineState {
  return "reset" in a ? initialState() : applyMessage(s, a.msg, a.now);
}

const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour12: false });

export function PipelineLive() {
  const [s, dispatch] = useReducer(reducer, undefined, initialState);
  const [status, setStatus] = useState("Connecting");
  const [session, setSession] = useState<SessionMsg | null>(null);
  const [now, setNow] = useState(() => performance.now());
  const code = useRef<string | null>(null);

  useEffect(() => {
    document.title = "VoiceVector · Live pipeline";
    const client = connect("viz", msg => {
      if (msg.type === "session") {
        if (code.current && code.current !== msg.code) dispatch({ reset: true });
        code.current = msg.code;
        setSession(msg);
        return;
      }
      dispatch({ msg, now: performance.now() });
    }, setStatus);
    return () => client.close();
  }, []);

  // Animate only while something is moving (packets, running calls, glows, mayday banner).
  const busy = s.packets.length > 0 || NODE_IDS.some(id => s.nodes[id].inFlight.length || now < s.nodes[id].flashUntil)
    || (s.mayday !== null && now < s.mayday.until);
  useEffect(() => {
    let raf = 0;
    const loop = () => { setNow(performance.now()); raf = requestAnimationFrame(loop); };
    if (busy) raf = requestAnimationFrame(loop);
    else { const t = setTimeout(() => setNow(performance.now()), 250); return () => clearTimeout(t); }
    return () => cancelAnimationFrame(raf);
  }, [busy, s]);

  const mayday = s.mayday && now < s.mayday.until ? s.mayday : null;
  const micLive = s.nodes.mic.inFlight.length > 0;
  const pipelineUrl = session?.join_url.replace("/viz", "/pipeline");
  const services = Object.values(s.services).sort((a, b) => b.calls - a.calls);

  return (
    <div className={`pl-shell${mayday ? " pl-mayday-on" : ""}`}>
      <header className="pl-head">
        <div className="pl-brand"><strong>VOICE<span>VECTOR</span></strong><small>LIVE PIPELINE</small></div>
        <div className={`pl-ptt${micLive ? " live" : ""}`} aria-live="polite">
          <i />{micLive ? `TRANSMITTING · ${fmtMs(now - s.nodes.mic.startedAt)}` : "Waiting for push-to-talk"}
        </div>
        <div className="pl-kpis">
          <div><b>{s.apiCalls}</b><span>API calls</span></div>
          <div><b>{s.lastResponseMs != null ? fmtMs(s.lastResponseMs) : "—"}</b><span>release → pilot on air</span></div>
        </div>
        <div className="pl-conn"><i className={status === "Connected" ? "on" : ""} />{status}{session ? ` · ${session.code}` : ""}</div>
      </header>

      {mayday && <div className="pl-banner" role="alert">
        <b>{mayday.kind}</b> {mayday.callsign} · emotional voice via fal Chatterbox, ElevenLabs as backup
      </div>}

      <main className="pl-main">
        <section className="pl-diagram" aria-label="Pipeline diagram">
          <svg viewBox={`0 0 ${GEO.width} ${HEIGHT}`} role="img" aria-label="Live signal path">
            {LANES.map(l => <g key={l.id} className={`pl-lane lane-${l.id}`}>
              <rect x={4} y={laneY(l.id)} width={GEO.width - 8} height={GEO.laneH - 6} rx={10} />
              <text x={16} y={laneY(l.id) + 22}>{l.label}</text>
            </g>)}
            {EDGES.map(([a, b]) => {
              const hot = s.packets.some(p => p.from === a && p.to === b);
              return <path key={`${a}-${b}`} d={edgePath(a, b)} className={`pl-edge${hot ? " hot" : ""}${a === "parse" && b === "sim" ? " skip" : ""}`} />;
            })}
            {NODE_IDS.map(id => <NodeBox key={id} id={id} s={s} now={now} />)}
            {s.packets.map(p => {
              const t = Math.min(1, (now - p.born) / PACKET_MS);
              if (t >= 1) return null;
              const [x, y] = pointOn(p.from, p.to, t);
              return <circle key={p.id} cx={x} cy={y} r={7} className={`pl-packet${p.emergency ? " emergency" : ""}`} />;
            })}
          </svg>
          <div className="pl-said">
            <p><span>Controller</span>{s.ctrlText || <em>—</em>}</p>
            <p><span>{s.pilotCallsign || "Pilot"}</span>{s.pilotText || <em>—</em>}</p>
          </div>
        </section>

        <aside className="pl-side">
          <h2>API calls</h2>
          <table className="pl-services"><tbody>
            {services.length ? services.map(v => <tr key={v.service}>
              <td>{v.service}</td><td>{v.calls}×</td><td>{fmtMs(v.calls ? v.totalMs / v.calls : null)} avg</td>
              <td className={v.errors ? "err" : ""}>{v.errors ? `${v.errors} failed` : ""}</td>
            </tr>) : <tr><td className="muted">No calls yet</td></tr>}
          </tbody></table>
          <h2>Activity</h2>
          <ol className="pl-log">
            {s.log.map(r => <li key={r.key} className={`node-${r.node} st-${r.status}`}>
              <time>{clock(r.at)}</time>
              <span className="what"><b>{r.service}</b> {r.op}{r.callsign ? ` · ${r.callsign}` : ""}</span>
              <span className="ms">{r.phase === "start" ? "calling…" : r.status === "error" ? "failed" : fmtMs(r.ms)}</span>
            </li>)}
          </ol>
          {pipelineUrl && <p className="pl-url">Open on another device: <code>{pipelineUrl}</code></p>}
        </aside>
      </main>
    </div>
  );
}

function NodeBox({ id, s, now }: { id: ActivityNode; s: PipelineState; now: number }) {
  const n = s.nodes[id];
  const b = nodeBox(id);
  const look = nodeLook(n, now, s.mayday, id);
  const def = NODES[id];
  const api = API_NODES.includes(id);
  const running = n.inFlight.length > 0;
  const tech = n.lastService && look !== "idle" ? n.lastService : def.tech;
  const line = running ? `${n.lastOp} · ${fmtMs(now - n.startedAt)}`
    : look !== "idle" ? [n.lastOp, n.lastStatus === "error" ? "failed" : fmtMs(n.lastMs)].filter(Boolean).join(" · ")
    : n.lastMs != null ? fmtMs(n.lastMs) : "";
  return <g className={`pl-node lane-${def.lane} look-${look}${api ? " api" : ""}`}>
    {running && <rect className="pl-ring" x={b.x - 6} y={b.y - 6} width={b.w + 12} height={b.h + 12} rx={14} />}
    <rect x={b.x} y={b.y} width={b.w} height={b.h} rx={9} />
    {api && <text className="pl-api" x={b.x + b.w - 6} y={b.y + 13} textAnchor="end">API</text>}
    <text className="pl-title" x={b.x + 9} y={b.y + 20}>{def.title}</text>
    <text className="pl-tech" x={b.x + 9} y={b.y + 36}>{clip(tech, 18)}</text>
    <text className="pl-line" x={b.x + 9} y={b.y + 53}>{clip(line, 18)}</text>
  </g>;
}

function clip(text: string, max: number) { return text.length > max ? `${text.slice(0, max - 1)}…` : text; }
