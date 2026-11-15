// Judge challenge (15 Nov, Sanju): live card on the radar, big leaderboard on the second screen.
import { useState } from "react";
import type { ChallengeMsg, ControlMsg } from "../protocol";
import { clock } from "./challengeFormat";
import "./challenge.css";

export function ChallengeCard({ challenge, send }: { challenge: ChallengeMsg | null; send: (m: ControlMsg) => boolean }) {
  const [name, setName] = useState("");
  const [saved, setSaved] = useState("");
  if (!challenge?.active) return null;
  const landed = challenge.status === "landed";
  return <aside className="challenge-card" aria-live="polite" aria-label="Judge challenge">
    <header><span>JUDGE CHALLENGE · LAND BAW117</span><b className="challenge-clock">{clock(challenge.elapsed_s)}</b></header>
    {challenge.status === "waiting" && <p className="challenge-hint">The clock starts with your first call. Vector BAW117 off the TIMBA hold and onto the ILS 26L.</p>}
    <dl>
      <div><dt>Calls</dt><dd>{challenge.calls}</dd></div>
      <div><dt>Phraseology</dt><dd>{challenge.phraseology ?? "—"}</dd></div>
      <div><dt>Go-arounds</dt><dd>{challenge.go_arounds}</dd></div>
      <div><dt>{landed ? "Score" : "Score so far"}</dt><dd className="challenge-score">{challenge.score ?? "—"}</dd></div>
    </dl>
    {landed && <form className="challenge-name" onSubmit={e => {
      e.preventDefault();
      if (name.trim() && send({ type: "control", action: "challenge_name", params: { name: name.trim() } })) setSaved(name.trim());
    }}>
      <p>Landed! Rank <b>{challenge.rank ?? "—"}</b> of {challenge.leaderboard.length}.</p>
      {saved ? <p>Saved as <b>{saved}</b>.</p> : <>
        <label htmlFor="challenge-name">Your name for the leaderboard</label>
        <div><input id="challenge-name" value={name} maxLength={24} onChange={e => setName(e.target.value)} /><button disabled={!name.trim()}>Save</button></div>
      </>}
    </form>}
  </aside>;
}

export function ChallengeBoard({ challenge }: { challenge: ChallengeMsg | null }) {
  if (!challenge?.active) return null;
  return <aside className="challenge-board" aria-label="Judge challenge leaderboard">
    <header><span>JUDGE CHALLENGE</span><b>{challenge.status === "landed" ? `Landed · ${challenge.score}` : clock(challenge.elapsed_s)}</b></header>
    <ol>{challenge.leaderboard.slice(0, 8).map((e, i) => <li key={e.id ?? i} className={e.id === challenge.entry_id ? "challenge-you" : ""}>
      <span className="challenge-rank">{i + 1}</span><span className="challenge-who">{e.name}</span>
      <span className="challenge-time">{clock(e.time_s)}</span><span className="challenge-pts">{e.score}</span></li>)}
      {!challenge.leaderboard.length && <li className="challenge-empty">First landing takes the top spot.</li>}
    </ol>
  </aside>;
}
