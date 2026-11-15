// Live pipeline screen (/pipeline) — pure model, no React (15 Nov, Sanju).
// Folds ActivityMsg / EventMsg / CaptionMsg / TraceMsg from the server into what the diagram shows.
import type { ActivityMsg, ActivityNode, ServerMsg } from "../protocol";

export type Lane = "browser" | "server" | "ai" | "sim";
export const LANES: { id: Lane; label: string }[] = [
  { id: "browser", label: "Controller's browser" },
  { id: "server", label: "VoiceVector server · Python" },
  { id: "ai", label: "AI services (APIs)" },
  { id: "sim", label: "Flight simulator" },
];

export interface NodeDef { lane: Lane; col: number; title: string; tech: string }
export const NODES: Record<ActivityNode, NodeDef> = {
  mic: { lane: "browser", col: 0, title: "Push-to-talk", tech: "Browser mic · 16 kHz" },
  stt: { lane: "ai", col: 1, title: "Speech to text", tech: "ElevenLabs Scribe" },
  parse: { lane: "server", col: 2, title: "Understand", tech: "Python grammar" },
  claude: { lane: "ai", col: 3, title: "Claude", tech: "Claude Haiku 5.5" },
  sim: { lane: "sim", col: 4, title: "Sim check", tech: "Can it comply?" },
  pilot: { lane: "server", col: 5, title: "Pilot decision", tech: "Persona · errors" },
  readback: { lane: "server", col: 6, title: "Readback", tech: "Composer + re-parse" },
  voice: { lane: "ai", col: 7, title: "Pilot voice", tech: "ElevenLabs Flash v2.5" },
  radio: { lane: "browser", col: 8, title: "Radio", tech: "VHF filter · hiss" },
  apply: { lane: "sim", col: 8, title: "Aircraft responds", tech: "On the radar" },
};
export const NODE_IDS = Object.keys(NODES) as ActivityNode[];
export const API_NODES: ActivityNode[] = ["stt", "claude", "voice"];

/** Data-flow edges, in diagram order. A packet runs along an edge when its target lights up. */
export const EDGES: [ActivityNode, ActivityNode][] = [
  ["mic", "stt"], ["stt", "parse"], ["parse", "claude"], ["claude", "sim"], ["parse", "sim"],
  ["sim", "pilot"], ["pilot", "readback"], ["readback", "voice"], ["voice", "radio"], ["radio", "apply"],
];

// ---- geometry (SVG user units) ----
export const GEO = { width: 1240, laneTop: 8, laneH: 116, col0: 128, colW: 122, nodeW: 108, nodeH: 64 };
export function laneY(lane: Lane): number {
  return GEO.laneTop + LANES.findIndex(l => l.id === lane) * GEO.laneH;
}
export function nodeBox(id: ActivityNode) {
  const n = NODES[id];
  const x = GEO.col0 + n.col * GEO.colW;
  const y = laneY(n.lane) + (GEO.laneH - GEO.nodeH) / 2;
  return { x, y, w: GEO.nodeW, h: GEO.nodeH, cx: x + GEO.nodeW / 2, cy: y + GEO.nodeH / 2 };
}
export const HEIGHT = GEO.laneTop + LANES.length * GEO.laneH + 8;

type Pt = [number, number];
/** Cubic Bézier control points for an edge: right side -> left side, or straight down in one column. */
export function edgeCurve(a: ActivityNode, b: ActivityNode): [Pt, Pt, Pt, Pt] {
  const A = nodeBox(a), B = nodeBox(b);
  if (NODES[a].col === NODES[b].col) {
    const p0: Pt = [A.cx, A.y + A.h], p3: Pt = [B.cx, B.y];
    return [p0, [p0[0], p0[1] + 40], [p3[0], p3[1] - 40], p3];
  }
  const p0: Pt = [A.x + A.w, A.cy], p3: Pt = [B.x, B.cy];
  const dx = Math.max(24, (p3[0] - p0[0]) * 0.5);
  return [p0, [p0[0] + dx, p0[1]], [p3[0] - dx, p3[1]], p3];
}
export function edgePath(a: ActivityNode, b: ActivityNode): string {
  const [p0, c1, c2, p3] = edgeCurve(a, b);
  return `M${p0[0]},${p0[1]} C${c1[0]},${c1[1]} ${c2[0]},${c2[1]} ${p3[0]},${p3[1]}`;
}
export function pointOn(a: ActivityNode, b: ActivityNode, t: number): Pt {
  const [p0, c1, c2, p3] = edgeCurve(a, b);
  const u = 1 - t;
  const f = (i: 0 | 1) => u * u * u * p0[i] + 3 * u * u * t * c1[i] + 3 * u * t * t * c2[i] + t * t * t * p3[i];
  return [f(0), f(1)];
}

// ---- state ----
export interface NodeState {
  inFlight: string[];            // activity ids currently running here
  startedAt: number;             // client ms of the oldest running call (for the live timer)
  lastService: string;
  lastOp: string;
  lastMs: number | null;
  lastStatus: ActivityMsg["status"] | "";
  callsign: string | null;
  flashUntil: number;            // client ms: show the "just finished" glow until then
}
export interface LogRow {
  key: string; at: number; node: ActivityNode; service: string; op: string;
  ms: number | null; status: ActivityMsg["status"]; callsign: string | null; phase: ActivityMsg["phase"];
}
export interface ServiceStat { service: string; calls: number; errors: number; totalMs: number; lastMs: number | null }
export interface Packet { id: number; from: ActivityNode; to: ActivityNode; born: number; emergency: boolean }
export interface PipelineState {
  nodes: Record<ActivityNode, NodeState>;
  log: LogRow[];
  services: Record<string, ServiceStat>;
  packets: Packet[];
  lastNode: { id: ActivityNode; at: number } | null;
  mayday: { callsign: string; kind: string; until: number } | null;
  ctrlText: string; pilotText: string; pilotCallsign: string;
  lastResponseMs: number | null;
  apiCalls: number;
}

const FLASH_MS = 1400;
const PACKET_WINDOW_MS = 8000;
export const PACKET_MS = 650;
const LOG_MAX = 60;
const MAYDAY_MS = 25000;               // long enough to cover a Chatterbox mayday (~10 s) going on air
let packetSeq = 0;

function emptyNode(): NodeState {
  return { inFlight: [], startedAt: 0, lastService: "", lastOp: "", lastMs: null, lastStatus: "", callsign: null, flashUntil: 0 };
}
export function initialState(): PipelineState {
  const nodes = Object.fromEntries(NODE_IDS.map(id => [id, emptyNode()])) as Record<ActivityNode, NodeState>;
  return { nodes, log: [], services: {}, packets: [], lastNode: null, mayday: null, ctrlText: "", pilotText: "",
    pilotCallsign: "", lastResponseMs: null, apiCalls: 0 };
}

/** Packets into `to` from the node that lit up just before it (if that is an edge of the flow). */
function packetsInto(s: PipelineState, to: ActivityNode, now: number, emergency: boolean): Packet[] {
  const prev = s.lastNode;
  if (!prev || prev.id === to || now - prev.at > PACKET_WINDOW_MS) return [];
  const edge = EDGES.find(([a, b]) => a === prev.id && b === to);
  return edge ? [{ id: ++packetSeq, from: edge[0], to: edge[1], born: now, emergency }] : [];
}

function touch(s: PipelineState, node: ActivityNode, now: number, emergency = false): PipelineState {
  const packets = [...s.packets.filter(p => now - p.born < PACKET_MS * 2), ...packetsInto(s, node, now, emergency)];
  return { ...s, packets, lastNode: { id: node, at: now } };
}

const isApi = (m: ActivityMsg) => API_NODES.includes(m.node);

export function applyActivity(prev: PipelineState, m: ActivityMsg, now: number): PipelineState {
  const warm = m.op === "warm-up";
  let s = prev;
  const node = { ...s.nodes[m.node] };
  if (m.phase === "start") {
    if (!node.inFlight.length) node.startedAt = now;
    node.inFlight = [...node.inFlight.filter(id => id !== m.id), m.id];
  } else {
    node.inFlight = node.inFlight.filter(id => id !== m.id);
    node.lastMs = m.ms;
    node.flashUntil = now + FLASH_MS;
  }
  node.lastService = m.service;
  node.lastOp = m.op;
  node.lastStatus = m.status;
  node.callsign = m.callsign ?? node.callsign;
  s = { ...s, nodes: { ...s.nodes, [m.node]: node } };
  if (m.status === "emergency") {
    s = { ...s, mayday: { callsign: m.callsign ?? "", kind: m.op, until: now + MAYDAY_MS } };
  } else if (!warm && m.phase !== "end") {
    // the declaration itself does not move the packet chain (readback -> voice stays connected)
    const red = s.mayday !== null && now < s.mayday.until && ["voice", "radio", "apply"].includes(m.node);
    s = touch(s, m.node, now, red);
  }
  if (isApi(m) && m.phase === "start") s = { ...s, apiCalls: s.apiCalls + 1 };
  if (isApi(m) && m.phase !== "start") {
    const st = s.services[m.service] ?? { service: m.service, calls: 0, errors: 0, totalMs: 0, lastMs: null };
    s = { ...s, services: { ...s.services, [m.service]: {
      ...st, calls: st.calls + 1, errors: st.errors + (m.status === "error" ? 1 : 0),
      totalMs: st.totalMs + (m.ms ?? 0), lastMs: m.ms,
    } } };
  }
  // Log: API starts and ends, local stages, mayday. Skip the mic/radio "start" rows (the node shows them live).
  if (!(m.phase === "start" && !isApi(m) && m.status !== "emergency")) {
    const row: LogRow = { key: `${m.id}-${m.phase}`, at: m.at_ms, node: m.node, service: m.service, op: m.op,
      ms: m.ms, status: m.status, callsign: m.callsign, phase: m.phase };
    // an ended call replaces its "calling…" row
    const rest = m.phase === "end" ? s.log.filter(r => r.key !== `${m.id}-start`) : s.log;
    s = { ...s, log: [row, ...rest].slice(0, LOG_MAX) };
  }
  return s;
}

/** Everything else the page listens to: captions (what was said), apply events, traces (totals). */
export function applyMessage(s: PipelineState, msg: ServerMsg, now: number): PipelineState {
  switch (msg.type) {
    case "activity":
      return applyActivity(s, msg, now);
    case "caption":
      if (msg.speaker === "CTRL" && msg.stage === "final") return { ...s, ctrlText: msg.text };
      if (msg.stage === "pilot_text" && msg.speaker !== "ATIS") return { ...s, pilotText: msg.text, pilotCallsign: msg.speaker };
      return s;
    case "event":
      if (msg.event.type === "instruction_applied") {
        const node = { ...s.nodes.apply, flashUntil: now + FLASH_MS, callsign: msg.event.callsign ?? null,
          lastOp: "instruction applied", lastService: "Flight simulator", lastStatus: "ok" as const, lastMs: null };
        return touch({ ...s, nodes: { ...s.nodes, apply: node } }, "apply", now);
      }
      return s;
    case "trace":
      // A controller trace copied per reply: total = PTT up -> that pilot's audio released
      // (pilot-initiated calls such as a mayday are not replies, so they do not count)
      if (msg.trace.speaker === "CTRL" && msg.trace.final && msg.trace.reply_callsign && msg.trace.total_ms > 0) {
        return { ...s, lastResponseMs: msg.trace.total_ms };
      }
      return s;
    default:
      return s;
  }
}

export type NodeLook = "idle" | "running" | "done" | "error" | "emergency";
export function nodeLook(n: NodeState, now: number, mayday: PipelineState["mayday"], id: ActivityNode): NodeLook {
  const emergencyNow = mayday && now < mayday.until && (id === "pilot" || id === "voice" || id === "radio");
  if (n.inFlight.length) return emergencyNow ? "emergency" : "running";
  if (now < n.flashUntil) {
    if (n.lastStatus === "error") return "error";
    if (emergencyNow || n.lastStatus === "emergency") return "emergency";
    return "done";
  }
  return "idle";
}

export function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "";
  if (ms < 1) return `${ms.toFixed(1)} ms`;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`;
}
