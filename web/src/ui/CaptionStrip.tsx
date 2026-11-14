import type { CaptionMsg } from "../protocol";
import { latencyMetrics } from "./captions";
import "./captions.css";

export function CaptionStrip({ messages }: { messages: readonly CaptionMsg[] }) {
  return <section className="caption-strip" aria-label="Radio transcript">
    {messages.length === 0
      ? <p className="muted">Controller clearances and pilot readbacks will appear here.</p>
      : messages.slice(-3).map(message => (
        <article key={JSON.stringify([message.tx_id, message.speaker])}
          className={`caption-row ${message.stage === "partial" ? "caption-partial" : ""}`}
          aria-live={message.stage === "partial" ? "off" : "polite"} aria-atomic="true">
          <b className="caption-speaker">{message.speaker === "CTRL" ? "Controller" : message.speaker}</b>
          <div className="caption-content">
            <p>{message.text}</p>
            {message.chips.length > 0 && <ul className="caption-chips" aria-label="Parsed clearance">
              {message.chips.map((chip, index) => <li key={index}>{chip.label}</li>)}
            </ul>}
            {message.plain_english && <p className="caption-plain">{message.plain_english}</p>}
            <ul className="caption-metrics" aria-label="Transmission latency">
              {latencyMetrics(message.latency).map(metric => <li key={metric.key}>{metric.text}</li>)}
            </ul>
            {message.error_reveal && <p className="caption-reveal">{message.error_reveal}</p>}
          </div>
        </article>
      ))}
  </section>;
}
