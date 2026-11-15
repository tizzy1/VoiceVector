import { useEffect, useId, useRef, useState } from "react";
import { presenterKey } from "./presenter";
import "./presenter.css";

interface Props {
  enabled: boolean; radioReady: boolean; reportPending: boolean; reportReady: boolean;
  judge: boolean; onJudge: () => void; onBeat: (key: 1 | 2 | 4 | 8) => void;
}

export function PresenterConsole({ enabled, radioReady, reportPending, reportReady,
  judge, onJudge, onBeat }: Props) {
  const [open, setOpen] = useState(false);
  const toggle = useRef<HTMLButtonElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const id = useId();
  function close() { setOpen(false); toggle.current?.focus(); }
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape" && open && box.current?.contains(event.target as Node)) {
        event.preventDefault(); event.stopPropagation(); close(); return;
      }
      const editing = event.target instanceof HTMLElement && (event.target.isContentEditable
        || Boolean(event.target.closest("input, textarea, select")));
      const key = presenterKey(event, editing);
      if (!key) return;
      if (key === "d") { event.preventDefault(); if (open) close(); else setOpen(true); }
      else if (key === "j") { event.preventDefault(); onJudge(); }
      else if (enabled && (key !== "4" || radioReady)
        && (key !== "8" || (!reportPending && !reportReady))) {
        event.preventDefault(); onBeat(Number(key) as 1 | 2 | 4 | 8);
      }
    }
    window.addEventListener("keydown", keydown);
    return () => window.removeEventListener("keydown", keydown);
  }, [enabled, radioReady, reportPending, reportReady, onBeat, onJudge, open]);
  return <div className="presenter-console" ref={box}>
    <button ref={toggle} type="button" aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(value => !value)}>Demo shortcuts · D{judge ? " · Judge" : ""}</button>
    {open && <section id={id} className="presenter-box" aria-label="Demo shortcuts">
      <div className="presenter-heading"><strong>Demo shortcuts</strong>
        <button type="button" onClick={close}>Close</button></div>
      <p>Use outside text fields. Hold Space for radio.</p>
      <button type="button" disabled={!enabled} onClick={() => onBeat(1)}>
        <kbd>1</kbd><span>Reset seeded demo</span></button>
      <button type="button" disabled={!enabled} onClick={() => onBeat(2)}>
        <kbd>2</kbd><span>Spawn conflict pair</span></button>
      <button type="button" disabled={!enabled || !radioReady} onClick={() => onBeat(4)}>
        <kbd>4</kbd><span>Arm next readback error</span></button>
      <button type="button" disabled={!enabled || reportPending || reportReady} onClick={() => onBeat(8)}>
        <kbd>8</kbd><span>{reportPending ? "Generating report…" : reportReady ? "Report ready" : "End session / report"}</span></button>
      <button type="button" aria-pressed={judge} onClick={onJudge}>
        <kbd>J</kbd><span>Judge mode · {judge ? "on" : "off"}</span></button>
      <p><kbd>D</kbd> show/hide this box · Reset restarts the scenario. Error applies to the next eligible numeric clearance.</p>
    </section>}
  </div>;
}
