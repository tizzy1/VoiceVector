// createAudioEngine(): the browser half of the voice pipeline (owner: Sanju). No React, no layout.
//
//   radar : holds the PTT, captures the microphone as 16 kHz PCM16 100 ms frames while keyed and
//           sends them with sendBinary(); plays pilot audio through the radio chain.
//   viz   : plays pilot audio only (init with role "viz").
//
// Implements AudioEngine from protocol.ts exactly. The extra methods (resume/state/micError/
// dispose) are optional conveniences for the host UI.
import type { AudioActivity, AudioEngine, ClientMsg, Role, ServerMsg } from "../protocol.ts";
import { MicCapture, type MicCaptureLike } from "./capture.ts";
import { OverlapMixer } from "./mixer.ts";
import { StreamPlayer } from "./player.ts";
import { RadioBus } from "./radio.ts";

export interface AudioEngineExt extends AudioEngine {
  /** Autoplay policies start the context suspended: call from a click/keydown. */
  resume(): Promise<void>;
  state(): AudioContextState | "none";
  /** Why the microphone is unavailable (denied, no device, insecure origin), else null. */
  micError(): string | null;
  /** Resolves once the microphone is ready or has failed (init() does not wait for the permission prompt). */
  whenMicReady(): Promise<void>;
  dispose(): void;
}

export interface AudioEngineDeps {
  createContext?: () => AudioContext;
  createMic?: (ctx: AudioContext, onFrame: (frame: ArrayBuffer) => void) => MicCaptureLike;
}

type InitOptions = Parameters<AudioEngine["init"]>[0];

export function createAudioEngine(deps: AudioEngineDeps = {}): AudioEngineExt {
  const createContext = deps.createContext ?? (() => new AudioContext({ latencyHint: "interactive" }));
  const createMic = deps.createMic ?? ((ctx, onFrame) => new MicCapture(ctx, onFrame));

  let opts: InitOptions | null = null;
  let ctx: AudioContext | null = null;
  let radio: RadioBus | null = null;
  let player: StreamPlayer | null = null;
  let mic: MicCaptureLike | null = null;
  let micProblem: string | null = null;
  let micReady: Promise<void> = Promise.resolve();
  let keyed = false;
  let volume = 1;
  let playbackOn = true;
  const listeners = new Set<(a: AudioActivity) => void>();
  const mixer = new OverlapMixer((on, seed) => radio?.setEngaged(on, seed));

  const emit = (a: AudioActivity) => listeners.forEach((cb) => {
    try { cb(a); } catch (error) { console.error("audio activity listener failed", error); }
  });

  function resumeOnGesture(): void {
    if (!ctx || ctx.state === "running" || typeof window === "undefined") return;
    const unlock = () => {
      void ctx?.resume();
      for (const name of ["pointerdown", "keydown", "touchstart"]) window.removeEventListener(name, unlock);
    };
    for (const name of ["pointerdown", "keydown", "touchstart"]) window.addEventListener(name, unlock, { once: true });
  }

  const engine: AudioEngineExt = {
    async init(options) {
      if (opts) return;                                  // idempotent: React strict mode mounts twice
      opts = options;
      playbackOn = options.playback;
      if (options.playback || options.role === "radar") {
        ctx = createContext();
        radio = new RadioBus(ctx);
        radio.setMuted(!playbackOn);
        radio.setVolume(volume);
        resumeOnGesture();
      }
      player = new StreamPlayer({ ctx, radio, mixer, emit });
      if (options.role === "radar" && ctx) {
        // Do not block on the permission prompt: playback must work even if nobody answers it.
        const candidate = createMic(ctx, (frame) => options.sendBinary(frame));
        micReady = candidate.init().then(
          () => {
            mic = candidate;
            if (keyed) candidate.setActive(true);        // PTT pressed while the prompt was open
          },
          (error) => {
            micProblem = error instanceof Error ? error.message : String(error);
            console.warn("Microphone unavailable:", micProblem);   // typed input and playback still work
          },
        );
      }
    },

    pttDown() {
      if (!opts || opts.role !== "radar" || keyed) return;
      keyed = true;
      void ctx?.resume();
      opts.sendJson({ type: "ptt", state: "down" } as ClientMsg);   // the server must hear "down" before audio
      mic?.setActive(true);
    },

    pttUp() {
      if (!opts || !keyed) return;
      keyed = false;
      mic?.setActive(false);                             // flushes the last partial frame first...
      opts.sendJson({ type: "ptt", state: "up" } as ClientMsg);     // ...then releases the key
    },

    handleServerMsg(m: ServerMsg) {
      if (!player) return;
      switch (m.type) {
        case "audio_start": player.start(m); break;
        case "audio_chunk": player.chunk(m); break;
        case "audio_end": void player.end(m); break;
        case "event":
          if (m.event.type === "overlap") {
            const data = m.event.data ?? {};
            const ids = Array.isArray(data.tx_ids) ? (data.tx_ids as unknown[]).map(String) : [];
            if (data.with_ctrl === true) {
              if (ids.length) mixer.overlap(ids, true);
              else mixer.overlapAllActive();
            }
          }
          break;
        default: break;                                  // everything else is the UI's business
      }
    },

    setPlayback(on) {
      playbackOn = on;
      radio?.setMuted(!on);
    },

    setVolume(v) {
      volume = Math.max(0, Math.min(1, v));
      radio?.setVolume(volume);
    },

    onActivity(cb) {
      listeners.add(cb);
      return () => { listeners.delete(cb); };
    },

    micLevel() {
      return mic?.level() ?? 0;
    },

    pilotWordClock(txId) {
      return player?.clock(txId) ?? null;
    },

    async resume() {
      if (ctx && ctx.state !== "running") await ctx.resume();
    },

    state() {
      return ctx ? ctx.state : "none";
    },

    micError() {
      return micProblem;
    },

    whenMicReady() {
      return micReady;
    },

    dispose() {
      player?.dispose();
      mic?.dispose();
      void ctx?.close();
      listeners.clear();
      ctx = radio = player = null;
      mic = null;
      opts = null;
    },
  };
  return engine;
}

export type { AudioEngine, AudioActivity, Role };
