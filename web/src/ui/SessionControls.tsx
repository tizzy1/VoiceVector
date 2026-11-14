import type { ControlMsg, SessionMsg, StateMsg } from "../protocol";
import "./session-controls.css";

export interface TrafficConfig {
  difficulty_presets: Record<"easy" | "medium" | "busy",
    { arr_per_hr: number; dep_per_hr: number }>;
}
interface Props {
  session: SessionMsg | null;
  state: StateMsg | null;
  traffic: TrafficConfig | null;
  selected: string;
  enabled: boolean;
  radioReady: boolean;
  send: (message: ControlMsg) => void;
}

export function SessionControls({ session, state, traffic, selected,
  enabled, radioReady, send }: Props) {
  const settings = session?.settings;
  const aircraft = state?.aircraft.find(ac => ac.callsign === selected
    && ac.phase !== "LANDED" && ac.phase !== "HANDED_OFF" && ac.phase !== "QUEUED");
  const presetSupported = settings?.scenario === "demo" || settings?.scenario === "custom";
  return <section className="session-controls" aria-label="Session controls">
    <h2>Session controls</h2>
    <fieldset disabled={!enabled || !settings}>
      <legend>Traffic intensity</legend>
      <div className="preset-buttons">
        {(["easy", "medium", "busy"] as const).map(name => {
          const preset = traffic?.difficulty_presets[name];
          return <button key={name} type="button" disabled={!preset || !presetSupported}
            aria-pressed={Boolean(preset && settings?.arr_per_hr === preset.arr_per_hr
              && settings?.dep_per_hr === preset.dep_per_hr)}
            onClick={() => preset && send({ type: "control", action: "set_traffic", params: preset })}>
            {name.charAt(0).toUpperCase() + name.slice(1)}
          </button>;
        })}
      </div>
      <p>{settings ? `Targets · ${settings.arr_per_hr} arrivals/hr · ${settings.dep_per_hr} departures/hr`
        : "Waiting for session settings."}</p>
      {!presetSupported && settings && <p>The active scenario manages its own traffic rates.</p>}
      <label>Simulation speed
        <select value={settings?.sim_speed ?? 1} onChange={e => send({
          type: "control", action: "set_speed", params: { sim_speed: Number(e.target.value) },
        })}><option value={1}>1×</option><option value={2}>2×</option></select>
      </label>
      <label>Pilot-error rate
        <select value={settings?.error_rate ?? "off"} onChange={e => send({
          type: "control", action: "set_error_rate", params: { error_rate: e.target.value },
        })}>
          <option value="off">Off</option><option value="low">Low</option>
          <option value="med">Medium</option><option value="high">High</option>
        </select>
      </label>
      {!radioReady && <p>Pilot speech and spoken readback errors await the radio pipeline.</p>}
    </fieldset>
    <fieldset disabled={!enabled || !aircraft}>
      <legend>Training events</legend>
      <p>{aircraft ? `Target · ${aircraft.callsign}` : "Select an active aircraft on the radar or list."}</p>
      <div className="event-buttons">
        <button type="button" onClick={() => send({ type: "control", action: "inject",
          params: { kind: "mayday", callsign: selected, params: { reason: "engine failure" } } })}>
          Emergency</button>
        <button type="button" onClick={() => send({ type: "control", action: "inject",
          params: { kind: "go_around", callsign: selected } })}>Go around</button>
        <button type="button" onClick={() => send({ type: "control", action: "inject",
          params: { kind: "radio_failure", callsign: selected } })}>Radio failure</button>
      </div>
    </fieldset>
  </section>;
}
