import type { CaptionMsg } from "../protocol";

export interface ResponseSample { tx_id: string; speaker: string; seconds: number }

export function updateResponseSamples(samples: ResponseSample[], message: CaptionMsg): ResponseSample[] {
  const seconds = message.latency.perceived_s;
  if (message.speaker === "CTRL" || message.speaker === "SYSTEM" || message.stage === "partial"
    || !Number.isFinite(seconds) || seconds < 0) return samples;
  const index = samples.findIndex(s => s.tx_id === message.tx_id && s.speaker === message.speaker);
  const sample = { tx_id: message.tx_id, speaker: message.speaker, seconds };
  if (index < 0) return [...samples, sample].slice(-10);
  const next = [...samples];
  next[index] = sample;
  return next;
}

export function responseMedian(samples: readonly ResponseSample[]): number | null {
  if (!samples.length) return null;
  const sorted = samples.map(s => s.seconds).sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
