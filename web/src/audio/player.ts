// Playback of the server's audio_start / audio_chunk / audio_end messages.
// Each transmission gets its own gain node feeding the shared RadioBus; chunks are decoded and
// scheduled back to back on the AudioContext clock. The server has already applied the pilot's
// reaction delay, so playback starts on the first chunk (plus a 60 ms jitter cushion).
import type { AudioActivity, AudioChunkMsg, AudioEndMsg, AudioStartMsg } from "../protocol.ts";
import { PcmAligner, base64ToBytes, pcm16ToFloat32 } from "./pcm.ts";
import { OverlapMixer } from "./mixer.ts";
import { RadioBus } from "./radio.ts";
import { StreamTimeline } from "./timeline.ts";

interface Stream {
  txId: string;
  callsign: string;
  timeline: StreamTimeline;
  aligner: PcmAligner;
  gain: GainNode | null;
  format: AudioStartMsg["format"];
  sampleRate: number;
  mp3: Uint8Array[];
  timer: ReturnType<typeof setTimeout> | null;
  done: boolean;
}

export interface PlayerDeps {
  ctx: AudioContext | null;
  radio: RadioBus | null;
  mixer: OverlapMixer;
  emit: (activity: AudioActivity) => void;
  /** Seconds. Defaults to the AudioContext clock, or performance.now() without one (muted viewer). */
  now?: () => number;
}

const KEEP_FINISHED = 24;      // finished timelines kept briefly so captions can still read the word clock
const END_SLACK_MS = 30;

export class StreamPlayer {
  private readonly streams = new Map<string, Stream>();
  private readonly finished = new Map<string, StreamTimeline>();
  private readonly deps: PlayerDeps;

  constructor(deps: PlayerDeps) {
    this.deps = deps;
  }

  private now(): number {
    if (this.deps.now) return this.deps.now();
    return this.deps.ctx ? this.deps.ctx.currentTime : performance.now() / 1000;
  }

  start(msg: AudioStartMsg): void {
    this.drop(msg.tx_id);
    const { ctx, radio, mixer } = this.deps;
    let gain: GainNode | null = null;
    if (ctx && radio) {
      gain = ctx.createGain();
      gain.gain.value = msg.emotion === "stressed" ? 1.25 : 1;
      gain.connect(radio.input);
      radio.acquire();
    }
    this.streams.set(msg.tx_id, {
      txId: msg.tx_id, callsign: msg.callsign, timeline: new StreamTimeline(), aligner: new PcmAligner(),
      gain, format: msg.format, sampleRate: msg.sample_rate || 22050, mp3: [], timer: null, done: false,
    });
    mixer.add({ txId: msg.tx_id, blocked: msg.blocked, group: msg.overlap_group ?? null });
    this.deps.emit({ tx_id: msg.tx_id, speaker: msg.callsign, state: "start" });
  }

  chunk(msg: AudioChunkMsg): void {
    const s = this.streams.get(msg.tx_id);
    if (!s || s.done) return;            // late joiner / already ended: ignore
    const bytes = s.aligner.push(base64ToBytes(msg.data_b64));
    if (bytes.length === 0) return;
    if (s.format === "mp3") {
      s.mp3.push(bytes);                 // mp3 frames cannot be decoded independently: decode at the end
      return;
    }
    this.schedulePcm(s, pcm16ToFloat32(bytes));
  }

  private schedulePcm(s: Stream, samples: Float32Array): void {
    const seconds = samples.length / s.sampleRate;
    const at = s.timeline.schedule(seconds, this.now());
    const { ctx } = this.deps;
    if (!ctx || !s.gain) return;
    const buffer = ctx.createBuffer(1, samples.length, s.sampleRate);
    buffer.copyToChannel(samples as Float32Array<ArrayBuffer>, 0);
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(s.gain);
    source.start(at);
  }

  async end(msg: AudioEndMsg): Promise<void> {
    const s = this.streams.get(msg.tx_id);
    if (!s || s.done) return;
    s.done = true;
    s.timeline.ended = true;
    if (s.format === "mp3" && s.mp3.length && this.deps.ctx) {
      try {
        const total = s.mp3.reduce((n, b) => n + b.length, 0);
        const joined = new Uint8Array(total);
        let offset = 0;
        for (const part of s.mp3) { joined.set(part, offset); offset += part.length; }
        const decoded = await this.deps.ctx.decodeAudioData(joined.buffer.slice(0));
        this.scheduleBuffer(s, decoded);
      } catch {
        /* undecodable audio: the caption still carries the words */
      }
    }
    const wait = s.timeline.remaining(this.now());
    s.timer = setTimeout(() => this.finish(s.txId), wait * 1000 + END_SLACK_MS);
  }

  private scheduleBuffer(s: Stream, buffer: AudioBuffer): void {
    const at = s.timeline.schedule(buffer.duration, this.now());
    const { ctx } = this.deps;
    if (!ctx || !s.gain) return;
    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(s.gain);
    source.start(at);
  }

  private finish(txId: string): void {
    const s = this.streams.get(txId);
    if (!s) return;
    this.streams.delete(txId);
    this.deps.radio?.release(s.timeline.nextTime);
    this.deps.mixer.remove(txId);
    s.gain?.disconnect();
    this.finished.set(txId, s.timeline);
    while (this.finished.size > KEEP_FINISHED) this.finished.delete(this.finished.keys().next().value as string);
    this.deps.emit({ tx_id: txId, speaker: s.callsign, state: "end" });
  }

  /** Seconds since this transmission's audio began playing, or null if unknown / not yet started. */
  clock(txId: string): number | null {
    const timeline = this.streams.get(txId)?.timeline ?? this.finished.get(txId);
    return timeline ? timeline.clock(this.now()) : null;
  }

  activeCount(): number {
    return this.streams.size;
  }

  private drop(txId: string): void {
    const old = this.streams.get(txId);
    if (!old) return;
    if (old.timer) clearTimeout(old.timer);
    this.streams.delete(txId);
    old.gain?.disconnect();
    this.deps.radio?.release();
    this.deps.mixer.remove(txId);
  }

  dispose(): void {
    for (const id of [...this.streams.keys()]) {
      const s = this.streams.get(id);
      if (s?.timer) clearTimeout(s.timer);
      this.drop(id);
    }
  }
}
