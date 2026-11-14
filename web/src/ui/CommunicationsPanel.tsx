import { useEffect, useRef, useState } from "react";
import type { CaptionMsg } from "../protocol";
import { latencyMetrics } from "./captions";
import { responseMedian, type ResponseSample } from "./communications";
import "./communications.css";

export function CommunicationsPanel({ messages, samples, selected = "" }: {
  messages: readonly CaptionMsg[]; samples: readonly ResponseSample[]; selected?: string;
}) {
  const transcript = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [following, setFollowing] = useState(true);
  const median = responseMedian(samples);
  function latest() {
    follow.current = true;
    setFollowing(true);
    if (transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }
  useEffect(() => {
    if (!messages.length) latest();
    else if (follow.current && transcript.current) {
      transcript.current.scrollTop = transcript.current.scrollHeight;
    }
  }, [messages]);

  return <details className="communications-panel" open onToggle={() => {
    if (follow.current && transcript.current) transcript.current.scrollTop = transcript.current.scrollHeight;
  }}>
    <summary>Communications <span>{messages.length} / 200</span></summary>
    <div className="communications-latency">
      <span>Pilot response p50</span><strong>{median == null ? "—" : `${median.toFixed(2)} s`}</strong>
    </div>
    <p className="communications-note">{samples.length} / 10 measured pilot transmissions · server timing</p>
    <div ref={transcript} className="communications-history" role="region"
      aria-label="Communications history" tabIndex={0} onScroll={event => {
        const node = event.currentTarget;
        follow.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 24;
        setFollowing(follow.current);
      }}>
      {!messages.length && <p className="muted">No radio traffic yet.</p>}
      {messages.map(message => <article key={JSON.stringify([message.tx_id, message.speaker])}
        className={[message.stage === "partial" ? "communications-partial" : "",
          selected && (message.speaker === selected || message.chips.some(chip => chip.label === selected))
            ? "communications-selected" : ""].join(" ")}>
        <div className="communications-speaker">
          <b>{message.speaker === "CTRL" ? "Controller" : message.speaker}</b>
          {message.stage === "partial" && <span>Listening…</span>}
        </div>
        <p>{message.text}</p>
        {message.chips.length > 0 && <ul className="communications-chips" aria-label="Parsed clearance">
          {message.chips.map((chip, index) => <li key={index}>{chip.label}</li>)}
        </ul>}
        {message.plain_english && <p className="communications-plain">{message.plain_english}</p>}
        {latencyMetrics(message.latency).length > 0 && <p className="communications-note">
          {latencyMetrics(message.latency).map(metric => metric.text).join(" · ")}
        </p>}
        {message.error_reveal && <p className="communications-reveal">{message.error_reveal}</p>}
      </article>)}
    </div>
    {!following && <button className="communications-latest" onClick={latest}>Jump to latest</button>}
  </details>;
}
