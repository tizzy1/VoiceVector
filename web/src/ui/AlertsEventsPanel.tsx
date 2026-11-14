import type { EventMsg, StateMsg } from "../protocol";

export function AlertsEventsPanel({ state, events, onSelect }: {
  state: StateMsg | null; events: readonly EventMsg[]; onSelect: (callsign: string) => void;
}) {
  return <details className="alerts-events" open>
    <summary>Alerts / events <span>{state?.alerts.length ?? 0} active</span></summary>
    <div className="alerts-events-content" tabIndex={0} role="region" aria-label="Alerts and session events">
      {!state ? <p className="muted">Waiting for aircraft state.</p> : state.alerts.length === 0
        ? <p className="muted">No active alerts.</p> : <ul className="operational-alerts">
          {state.alerts.map((alert, index) => <li key={index} className={`alert-${alert.kind}`}>
            <b>{alert.kind.replaceAll("_", " ")}</b> · {alert.message}
            <div>{alert.callsigns.map(callsign => <button key={callsign} type="button"
              disabled={!state.aircraft.some(ac => ac.callsign === callsign)} onClick={() => onSelect(callsign)}>
              {callsign}</button>)}</div>
          </li>)}
        </ul>}
      <ol className="operational-events">{[...events].reverse().map((event, index) => <li key={index}>
        <time>{Math.floor(event.event.t / 60).toString().padStart(2, "0")}:
          {Math.floor(event.event.t % 60).toString().padStart(2, "0")}</time>
        <span>{event.feed_text}</span>
      </li>)}</ol>
    </div>
  </details>;
}
