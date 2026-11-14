import type { CaptionMsg } from "../protocol";

export function updateCaptions(messages: readonly CaptionMsg[], incoming: CaptionMsg): CaptionMsg[] {
  const index = messages.findIndex(m => m.tx_id === incoming.tx_id && m.speaker === incoming.speaker);
  if (index < 0) return [...messages, incoming].slice(-30);
  const previous = messages[index];
  if (previous.stage === "final" && incoming.stage === "partial") return [...messages];
  const next = [...messages];
  next[index] = {
    ...previous, ...incoming,
    text: incoming.text || previous.text,
    chips: incoming.chips.length ? incoming.chips : previous.chips,
    words: incoming.words.length ? incoming.words : previous.words,
    plain_english: incoming.plain_english || previous.plain_english,
    latency: { ...previous.latency, ...incoming.latency },
  };
  return next;
}

export function latencyMetrics(latency: Record<string, number>) {
  const names: Record<string, string> = {
    parse_s: "Parse", first_audio_s: "First audio", perceived_s: "Response",
  };
  return Object.entries(names).flatMap(([key, label]) => {
    const value = latency[key];
    return Number.isFinite(value) && value >= 0
      ? [{ key, text: `${label} ${value.toFixed(2)} s` }] : [];
  });
}
