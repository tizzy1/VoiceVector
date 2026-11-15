// Phraseology coach card (15 Nov, Sanju): floats over the radar, shows the latest controller
// transmission's score, the rule notes and Claude's one-line tip when it arrives.
import { useState } from "react";
import type { CoachMsg } from "../protocol";
export { mergeCoach } from "./coach";
import "./coach.css";

export function CoachCard({ coach }: { coach: CoachMsg | null }) {
  const [hidden, setHidden] = useState(false);
  if (!coach) return null;
  if (hidden) return <button className="coach-reopen" onClick={() => setHidden(false)}>Coach · {coach.score}</button>;
  const band = coach.score >= 90 ? "good" : coach.score >= 70 ? "ok" : "low";
  return <aside className="coach-card" aria-live="polite" aria-label="Phraseology coach">
    <header>
      <span className="coach-title">PHRASEOLOGY COACH{coach.callsign ? ` · ${coach.callsign}` : ""}</span>
      <span className={`coach-score coach-${band}`}>{coach.score}</span>
      <button aria-label="Hide coach" onClick={() => setHidden(true)}>×</button>
    </header>
    <ul>{coach.notes.map((n, i) => <li key={i} className={`coach-note coach-${n.level}`}>{n.text}</li>)}</ul>
    <p className="coach-tip">{coach.tip ? <><b>Tip</b> {coach.tip}</> : <span className="coach-wait">Reviewing…</span>}</p>
  </aside>;
}
