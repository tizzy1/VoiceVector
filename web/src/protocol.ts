// SweatboxAI WebSocket protocol — mirror of server/contracts.py (client-facing parts).
// Change ONLY together with server/contracts.py, in the same commit. File owner: Bhavin.

export type Wake = "H" | "M" | "L";
export type Flow = "ARR" | "DEP";
export type Phase = "QUEUED" | "TAKEOFF" | "SID" | "STAR" | "VECTORS" | "APPROACH" | "FINAL"
  | "GO_AROUND" | "HOLD" | "LANDED" | "HANDED_OFF";
export type TurnDir = "L" | "R";
export type Role = "radar" | "viz";
export type Severity = "HIGH" | "MEDIUM" | "LOW" | "POSITIVE" | "INFO";
export type Actor = "CONTROLLER" | "PILOT" | "SYSTEM" | "INSTRUCTOR";
export type AlertKind = "STCA_LOS" | "STCA_PRED" | "WAKE" | "MIN_ALT" | "OUTSIDE_CAS" | "EMERGENCY";
export type ErrorRate = "off" | "low" | "med" | "high";

export interface Alert { kind: AlertKind; callsigns: string[]; severity: Severity; message: string; since: number }

export interface AircraftPublic {
  callsign: string; spoken_plain: string; actype: string; wake: Wake; flow: Flow; phase: Phase;
  x: number; y: number; lat: number; lon: number;
  alt: number; vs: number; hdg: number; track: number; ias: number; gs: number;
  cleared_alt: number; tgt_hdg?: number | null; turn_dir?: TurnDir | null; tgt_ias?: number | null;
  route_name?: string | null; next_fix?: string | null; approach_cleared?: string | null;
  established: boolean; squawk: string; on_frequency: boolean; in_jurisdiction: boolean;
  emergency: boolean; transmitting: boolean; plain_status: string;
  traffic_source?: string | null; source_time?: string | null; fetched_at?: string | null;
  source_attribution?: string | null;
  // Additive §3.2 fields: absent/null means the simulator has not supplied a value.
  flight_rules?: "I" | "V" | null; adep?: string | null; ades?: string | null;
  rfl?: number | null;                       // requested cruise FL, not feet
  assigned_squawk?: string | null;           // preserve leading zeroes
  selected_level?: number | null;            // Mode S selected level, feet
  strip?: Record<string, unknown>;           // controller record; editing never flies a clearance
}

export interface CloudLayer { cover: string; base_ft: number }
/** Always SIMULATED (built from the sim's wind/QNH); label it as simulated, never as live. */
export interface Metar {
  source: "SIMULATED"; icao: string; observed: string; wind_dir_deg: number | null;
  wind_kt: number; calm: boolean; visibility_m: number; cloud: CloudLayer[]; cavok: boolean;
  temp_c: number; dewpoint_c: number; qnh_hpa: number; raw: string;
}

export interface LiveImportedTarget {
  callsign: string; flow: Flow; phase: Phase; actype: string; type_exact: boolean;
}
export interface LiveImportSkip { callsign: string; reason: string }
export interface LiveImportCounts { imported: number; skipped: number; skipped_by_reason: Record<string, number> }
export interface LiveImportStatus {
  source: "demo" | "vatsim"; mode: "snapshot"; phase: "fetching" | "complete";
  ok: boolean; stale: boolean; error?: string | null; fetched_at?: string | null;
  source_time?: string | null; attribution: string; sim_t: number;
  imported: LiveImportedTarget[]; skipped: LiveImportSkip[]; counts: LiveImportCounts;
}

export interface StateMsg {
  type: "state"; t: number; paused: boolean; sim_speed: number; runway: string;
  aircraft: AircraftPublic[]; alerts: Alert[]; backlog: Record<string, number>;
  metar?: Metar | null;
  live_import_available?: boolean; live_import?: LiveImportStatus | null;
}

export interface ParsedChip { label: string; span?: [number, number] | null }
export interface CaptionWord { w: string; t0: number; t1: number }
export interface CaptionMsg {
  type: "caption"; tx_id: string; speaker: string;                    // "CTRL" or callsign
  stage: "partial" | "final" | "pilot_text" | "pilot_words";
  text: string; chips: ParsedChip[]; words: CaptionWord[]; plain_english: string;
  latency: Record<string, number>; error_reveal?: string | null;
}

export type TraceCategory = "capture" | "stt" | "language" | "sim" | "pilot" | "voice" | "playback";
export interface TraceStage {
  name: string; category: TraceCategory; t0_ms: number; t1_ms: number;
  status: "ok" | "fallback" | "error" | "skipped"; detail: Record<string, unknown>;
}
export interface PipelineTrace {
  tx_id: string; speaker: string; reply_callsign?: string | null;
  stages: TraceStage[]; total_ms: number; final: boolean;
}
export interface TraceMsg { type: "trace"; trace: PipelineTrace }

export interface AudioStartMsg {
  type: "audio_start"; tx_id: string; callsign: string; format: "pcm_s16le" | "mp3"; sample_rate: number;
  blocked: boolean; overlap_group?: string | null; emotion: "normal" | "stressed";
}
export interface AudioChunkMsg { type: "audio_chunk"; tx_id: string; seq: number; data_b64: string }
export interface AudioEndMsg { type: "audio_end"; tx_id: string }

export interface SimEvent {
  t: number; type: string; actor: Actor; callsign?: string | null; tx_id?: string | null;
  data: Record<string, unknown>;
}
export interface EventMsg { type: "event"; event: SimEvent; feed_text: string }

export interface Finding {
  id: string; rule_id: string; category: string; severity: Severity; t: number; actor: Actor;
  callsigns: string[]; title: string; detail: string; correct_practice: string; evidence: Record<string, unknown>;
}
export interface ErrorRecord {
  id: string; callsign: string; kind: string; mode: string; element_type: string;
  intended: unknown; readback_value: unknown; flown_value: unknown;
  t_planned: number; t_spoken?: number | null; t_caught?: number | null;
  outcome: "pending" | "caught" | "missed" | "self_resolved"; caught_by?: string | null;
}
export interface Report {
  session_id: string; duration_s: number; score: number; score_breakdown: Record<string, number>;
  findings: Finding[]; errors: ErrorRecord[]; stats: Record<string, unknown>; timeline: SimEvent[];
  narrative?: string | null; coaching: string[];
}
export interface ReportMsg { type: "report"; report: Report }

export interface SessionSettings {
  scenario: "demo" | "ramp" | "easy" | "medium" | "busy" | "custom"; seed: number; runway: "26L" | "08R";
  arr_per_hr: number; dep_per_hr: number; error_rate: ErrorRate; sim_speed: number; paused: boolean;
}
export interface SessionMsg {
  type: "session"; code: string; settings: SessionSettings; clients: Record<string, number>; join_url: string;
}

export type ServerMsg = StateMsg | CaptionMsg | AudioStartMsg | AudioChunkMsg | AudioEndMsg
  | EventMsg | ReportMsg | SessionMsg | TraceMsg;

// ---- client -> server ----
export type ControlAction = "new_session" | "pause" | "resume" | "set_speed" | "set_traffic"
  | "set_error_rate" | "set_runway" | "load_scenario" | "inject" | "force_error" | "beat"
  | "end_session" | "reveal_errors" | "import_live" | "edit_strip";
export interface ControlMsg { type: "control"; action: ControlAction; params: Record<string, unknown> }
export interface PttMsg { type: "ptt"; state: "down" | "up" }
export interface TypedMsg { type: "typed"; text: string }
export type ClientMsg = ControlMsg | PttMsg | TypedMsg;
// Binary frames (radar -> server only): mic PCM16 mono 16 kHz little-endian, ~100 ms each.

// ---- front-end audio module API (owner: Sanju, web/src/audio/index.ts) ----
export interface AudioActivity { tx_id: string; speaker: string; state: "start" | "end" }
export interface AudioEngine {
  init(opts: { sendBinary: (pcm: ArrayBuffer) => void; sendJson: (m: ClientMsg) => void;
               role: Role; playback: boolean }): Promise<void>;
  pttDown(): void;                         // radar only: sends {type:"ptt",state:"down"} then streams mic
  pttUp(): void;                           // stops streaming, sends {type:"ptt",state:"up"}
  handleServerMsg(m: ServerMsg): void;     // feed audio_start / audio_chunk / audio_end (ignores others)
  setPlayback(on: boolean): void;
  setVolume(v: number): void;              // 0..1
  onActivity(cb: (a: AudioActivity) => void): () => void;   // returns unsubscribe
  micLevel(): number;                      // 0..1 for the PTT VU meter
  pilotWordClock(tx_id: string): number | null;  // seconds since that tx's audio started (caption sync)
}
