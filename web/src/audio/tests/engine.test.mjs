// Engine + mixer tests against a fake AudioContext (no browser needed).
import assert from "node:assert/strict";
import test from "node:test";
import { createAudioEngine } from "../index.ts";
import { OverlapMixer } from "../mixer.ts";
import { RadioBus } from "../radio.ts";
import { pcmToBase64Chunks, synthSpeechLike } from "../fixtures.ts";

// ---------------------------------------------------------------- fake Web Audio
class Param {
  constructor(value = 0) { this.value = value; this.calls = []; }
  setValueAtTime(v, t) { this.calls.push(["set", v, t]); this.value = v; return this; }
  linearRampToValueAtTime(v, t) { this.calls.push(["ramp", v, t]); this.value = v; return this; }
  cancelScheduledValues() { return this; }
}
class Node {
  constructor(kind) { this.kind = kind; this.out = []; this.disconnected = false; }
  connect(n) { this.out.push(n); return n; }
  disconnect() { this.disconnected = true; }
}
class FakeContext {
  constructor() {
    this.currentTime = 100; this.state = "suspended"; this.sampleRate = 48000;
    this.destination = new Node("destination"); this.nodes = []; this.sources = []; this.closed = false;
  }
  make(kind, extra = {}) { const n = Object.assign(new Node(kind), extra); this.nodes.push(n); return n; }
  createGain() { return this.make("gain", { gain: new Param(1) }); }
  createBiquadFilter() { return this.make("biquad", { type: "", frequency: new Param(0), gain: new Param(0) }); }
  createWaveShaper() { return this.make("shaper", { curve: null }); }
  createOscillator() { return this.make("osc", { type: "", frequency: new Param(0), start() {} }); }
  createBuffer(channels, length, rate) {
    return { channels, length, sampleRate: rate, duration: length / rate, data: null, copyToChannel(d) { this.data = d; } };
  }
  createBufferSource() {
    const s = this.make("source", { buffer: null, loop: false, startedAt: null, start(t) { this.startedAt = t ?? this.ctxTime; } });
    this.sources.push(s);
    return s;
  }
  async decodeAudioData() { return { duration: 0.3 }; }
  async resume() { this.state = "running"; }
  async close() { this.closed = true; }
}
const radioNodes = (ctx) => ctx.nodes.filter((n) => n.kind === "gain");

function fakeMic() {
  const mic = { active: false, calls: [], inited: false, frame: null };
  return {
    mic,
    factory: (_ctx, onFrame) => {
      mic.frame = onFrame;
      return {
        async init() { mic.inited = true; },
        setActive(a) { mic.active = a; mic.calls.push(`active:${a}`); if (!a) onFrame(new Uint8Array(200).buffer); },
        level: () => 0.42,
        dispose() { mic.calls.push("dispose"); },
      };
    },
  };
}
async function make(role = "radar", { playback = true, micFails = false } = {}) {
  const ctx = new FakeContext();
  const sent = [];
  const m = fakeMic();
  const factory = micFails
    ? (c, f) => ({ ...m.factory(c, f), async init() { throw new Error("Permission denied"); } })
    : m.factory;
  const engine = createAudioEngine({ createContext: () => ctx, createMic: factory });
  await engine.init({ sendBinary: (b) => sent.push(["bin", b.byteLength]), sendJson: (j) => sent.push(["json", j.state]), role, playback });
  await engine.whenMicReady();
  return { ctx, engine, sent, mic: m.mic };
}
const start = (id, over = {}) => ({ type: "audio_start", tx_id: id, callsign: "BAW117", format: "pcm_s16le", sample_rate: 22050, blocked: false, overlap_group: null, emotion: "normal", ...over });
const chunk = (id, seq, b64) => ({ type: "audio_chunk", tx_id: id, seq, data_b64: b64 });
const end = (id) => ({ type: "audio_end", tx_id: id });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ------------------------------------------------------------------- push to talk
test("PTT: the server hears 'down' before any audio, and the last frame is flushed before 'up'", async () => {
  const { engine, sent, mic, ctx } = await make("radar");
  assert.ok(mic.inited);
  engine.pttDown();
  assert.equal(ctx.state, "running", "pressing PTT unlocks the audio context");
  mic.frame(new Uint8Array(3200).buffer);
  engine.pttUp();
  assert.deepEqual(sent, [["json", "down"], ["bin", 3200], ["bin", 200], ["json", "up"]]);
  assert.deepEqual(mic.calls, ["active:true", "active:false"]);
});

test("PTT is idempotent and viz screens cannot transmit", async () => {
  const radar = await make("radar");
  radar.engine.pttDown(); radar.engine.pttDown(); radar.engine.pttUp(); radar.engine.pttUp();
  assert.deepEqual(radar.sent.filter(([k]) => k === "json"), [["json", "down"], ["json", "up"]]);
  const viz = await make("viz");
  viz.engine.pttDown(); viz.engine.pttUp();
  assert.deepEqual(viz.sent, []);
  assert.equal(viz.engine.micLevel(), 0);
});

test("denied microphone: PTT still signals the server (typed input path), playback still works, reason is exposed", async () => {
  const { engine, sent } = await make("radar", { micFails: true });
  assert.equal(engine.micError(), "Permission denied");
  engine.pttDown(); engine.pttUp();
  assert.deepEqual(sent, [["json", "down"], ["json", "up"]]);
  assert.equal(engine.micLevel(), 0);
});

test("micLevel comes from the capture pipeline", async () => {
  const { engine } = await make("radar");
  assert.equal(engine.micLevel(), 0.42);
});

test("init is idempotent (React strict mode mounts effects twice)", async () => {
  const ctxs = [];
  const engine = createAudioEngine({ createContext: () => { const c = new FakeContext(); ctxs.push(c); return c; }, createMic: fakeMic().factory });
  const opts = { sendBinary() {}, sendJson() {}, role: "radar", playback: true };
  await engine.init(opts); await engine.init(opts);
  assert.equal(ctxs.length, 1);
});

// ---------------------------------------------------------------------- playback
test("playback: chunks are scheduled gapless, activity start/end fire, the word clock follows the audio", async () => {
  const { engine, ctx } = await make("viz");
  const activity = [];
  engine.onActivity((a) => activity.push(`${a.state}:${a.speaker}:${a.tx_id}`));
  const chunks = pcmToBase64Chunks(synthSpeechLike(0.4, 22050, 2), 4410);        // 4 x 100 ms
  engine.handleServerMsg(start("p1"));
  assert.deepEqual(activity, ["start:BAW117:p1"]);
  assert.equal(engine.pilotWordClock("p1"), null, "unknown until the first chunk is scheduled");
  chunks.forEach((c, i) => engine.handleServerMsg(chunk("p1", i, c)));
  engine.handleServerMsg(end("p1"));
  const starts = ctx.sources.filter((s) => s.buffer && !s.loop).map((s) => s.startedAt);
  assert.equal(starts.length, 4);
  assert.ok(Math.abs(starts[0] - 100.06) < 1e-6, "first chunk starts after the 60 ms cushion");
  for (let i = 1; i < starts.length; i++) assert.ok(Math.abs(starts[i] - starts[i - 1] - 0.1) < 1e-6, "gapless");
  ctx.currentTime = 100.26;
  assert.ok(Math.abs(engine.pilotWordClock("p1") - 0.2) < 1e-6);
  await sleep(520);                                                              // 0.46 s of audio at a frozen clock
  assert.deepEqual(activity, ["start:BAW117:p1", "end:BAW117:p1"]);
  assert.ok(engine.pilotWordClock("p1") !== null, "captions can still read the clock just after the end");
  assert.equal(engine.pilotWordClock("nope"), null);
});

test("every stream goes through the shared radio chain; hiss follows the carrier and a squelch tail closes it", async () => {
  const { engine, ctx } = await make("viz");
  const before = ctx.sources.length;
  engine.handleServerMsg(start("p1"));
  engine.handleServerMsg(chunk("p1", 0, pcmToBase64Chunks(synthSpeechLike(0.1, 22050, 1), 9000)[0]));
  engine.handleServerMsg(end("p1"));
  const hiss = ctx.nodes.find((n) => n.kind === "gain" && n.gain.calls.some((c) => Math.abs(c[1] - 0.012) < 1e-9));
  assert.ok(hiss, "hiss ramped up to the configured level");
  await sleep(200);
  const bands = ctx.nodes.filter((n) => n.kind === "biquad" && n.type === "bandpass");
  assert.ok(bands.some((b) => b.frequency.value === 1800), "a ~150 ms band-passed squelch tail was played");
  assert.ok(ctx.sources.length > before);
  const highpass = ctx.nodes.find((n) => n.kind === "biquad" && n.type === "highpass");
  const lowpass = ctx.nodes.find((n) => n.kind === "biquad" && n.type === "lowpass");
  assert.equal(highpass.frequency.value, 350);
  assert.equal(lowpass.frequency.value, 2600);
  const peaking = ctx.nodes.find((n) => n.kind === "biquad" && n.type === "peaking");
  const shelf = ctx.nodes.find((n) => n.kind === "biquad" && n.type === "highshelf");
  assert.ok(peaking && shelf, "presence lift and high-shelf cut are in the chain");
});

test("blocked / overlap-group / overlap-event streams engage the heterodyne, then release", async () => {
  const { engine, ctx } = await make("viz");
  const osc = () => ctx.nodes.filter((n) => n.kind === "osc");
  const oscGainRamps = () => ctx.nodes.filter((n) => n.kind === "gain").flatMap((n) => n.gain.calls).filter((c) => c[0] === "ramp" && Math.abs(c[1] - 0.55) < 1e-9);
  assert.equal(osc().length, 2, "carrier + LFO exist from the start (silent)");

  engine.handleServerMsg(start("a", { blocked: true }));
  assert.equal(oscGainRamps().length, 1, "blocked stream -> squeal");
  const carrier = osc().find((o) => o.frequency.calls.some((c) => c[0] === "set" && c[1] > 900));
  const hz = carrier.frequency.calls.at(-1)[1];
  assert.ok(hz > 990 && hz < 1210, `carrier ${hz}`);
  engine.handleServerMsg(chunk("a", 0, pcmToBase64Chunks(synthSpeechLike(0.05, 22050, 1), 9000)[0]));
  engine.handleServerMsg(end("a"));
  await sleep(150);

  engine.handleServerMsg(start("g1", { overlap_group: "G" }));
  assert.equal(oscGainRamps().length, 1, "one stream in a group is not an overlap");
  engine.handleServerMsg(start("g2", { overlap_group: "G" }));
  assert.equal(oscGainRamps().length, 2, "two simultaneous streams in the same group -> squeal");

  engine.handleServerMsg(start("c1"));
  engine.handleServerMsg({ type: "event", feed_text: "", event: { t: 1, type: "overlap", actor: "SYSTEM", data: { with_ctrl: true, tx_ids: ["c1"] } } });
  engine.handleServerMsg({ type: "state" });                       // unrelated messages are ignored
  assert.equal(engine.state(), "suspended");
});

test("setPlayback mutes the master gain, setVolume scales it, and the engine keeps the clock running", async () => {
  const { engine, ctx } = await make("radar", { playback: true });
  const master = ctx.nodes.find((n) => n.kind === "gain" && n.out.includes(ctx.destination));
  engine.setVolume(0.5);
  assert.equal(master.gain.value, 0.5);
  engine.setPlayback(false);
  assert.equal(master.gain.value, 0);
  engine.setPlayback(true);
  assert.equal(master.gain.value, 0.5);
  engine.setVolume(7);
  assert.equal(master.gain.value, 1);
});

test("a muted viewer (no AudioContext) still reports activity and a word clock", async () => {
  const engine = createAudioEngine({ createContext: () => { throw new Error("no context expected"); } });
  await engine.init({ sendBinary() {}, sendJson() {}, role: "viz", playback: false });
  const seen = [];
  const off = engine.onActivity((a) => seen.push(a.state));
  engine.handleServerMsg(start("p1"));
  engine.handleServerMsg(chunk("p1", 0, pcmToBase64Chunks(synthSpeechLike(0.1, 22050, 1), 9000)[0]));
  engine.handleServerMsg(end("p1"));
  await sleep(10);
  assert.deepEqual(seen, ["start"]);
  assert.ok(engine.pilotWordClock("p1") >= 0);
  await sleep(250);
  assert.deepEqual(seen, ["start", "end"]);
  off();
  assert.equal(engine.state(), "none");
});

test("chunks for unknown or finished transmissions and odd-length chunks are tolerated", async () => {
  const { engine, ctx } = await make("viz");
  engine.handleServerMsg(chunk("ghost", 0, "AAAA"));
  engine.handleServerMsg(end("ghost"));
  engine.handleServerMsg(start("p1"));
  engine.handleServerMsg(chunk("p1", 0, "AAEC"));                  // 3 bytes: one sample + a dangling byte
  engine.handleServerMsg(chunk("p1", 1, "AwQF"));                  // completes the dangling byte
  const scheduled = ctx.sources.filter((s) => s.buffer && !s.loop);
  assert.equal(scheduled.length, 2);
  assert.deepEqual(scheduled.map((s) => s.buffer.length), [1, 2]);
  engine.dispose();
});

test("mp3 audio is collected and decoded once at the end", async () => {
  const { engine, ctx } = await make("viz");
  engine.handleServerMsg(start("m", { format: "mp3" }));
  engine.handleServerMsg(chunk("m", 0, "//uQ"));
  engine.handleServerMsg(chunk("m", 1, "//uQ"));
  assert.equal(ctx.sources.filter((s) => s.buffer && !s.loop).length, 0, "nothing plays until decoded");
  engine.handleServerMsg(end("m"));
  await sleep(20);
  assert.equal(ctx.sources.filter((s) => s.buffer && !s.loop).length, 1);
  assert.ok(Math.abs(engine.pilotWordClock("m") - 0) < 1);
});

test("callback errors do not break the audio path, and unsubscribe works", async () => {
  const { engine } = await make("viz");
  const good = [];
  engine.onActivity(() => { throw new Error("listener bug"); });
  const off = engine.onActivity((a) => good.push(a.state));
  const quiet = console.error; console.error = () => {};
  try { engine.handleServerMsg(start("p1")); } finally { console.error = quiet; }
  assert.deepEqual(good, ["start"]);
  off();
  engine.handleServerMsg(start("p2"));
  assert.deepEqual(good, ["start"]);
});

// ----------------------------------------------------------------------- mixer
test("overlap mixer decision table", () => {
  const log = [];
  let now = 1000;
  const mixer = new OverlapMixer((on, seed) => log.push(`${on ? "on" : "off"}:${seed}`), () => now);
  mixer.add({ txId: "a", blocked: false, group: null });
  assert.deepEqual(log, []);
  mixer.overlap(["a"], false);                                    // pilot-only overlap events carry no ctrl
  assert.deepEqual(log, []);
  mixer.overlap(["a"], true);
  assert.deepEqual(log, ["on:squeal-1"]);
  mixer.remove("a");
  assert.deepEqual(log, ["on:squeal-1", "off:squeal-1"]);
  mixer.overlap(["late"], true);                                  // event raced ahead of audio_start
  now += 1000;
  mixer.add({ txId: "late", blocked: false, group: null });
  assert.equal(log.at(-1), "on:squeal-2", "each event gets its own seed (own pitch)");
  mixer.remove("late");
  mixer.overlap(["stale"], true);
  now += 6000;
  mixer.add({ txId: "stale", blocked: false, group: null });
  assert.equal(mixer.isEngaged, false, "a stale pending overlap is forgotten");
  mixer.add({ txId: "x", blocked: false, group: "G" });
  mixer.add({ txId: "y", blocked: false, group: "G" });
  assert.equal(mixer.isEngaged, true);
  mixer.remove("x");
  assert.equal(mixer.isEngaged, false);
  mixer.overlapAllActive();
  assert.equal(mixer.isEngaged, true);
});

test("RadioBus engage ramps the oscillator and overdrive, release returns to unity", () => {
  const ctx = new FakeContext();
  const radio = new RadioBus(ctx);
  radio.setEngaged(true, "s");
  const ramps = ctx.nodes.filter((n) => n.kind === "gain").flatMap((n) => n.gain.calls.filter((c) => c[0] === "ramp").map((c) => c[1]));
  assert.ok(ramps.includes(0.55) && ramps.includes(2.6));
  radio.setEngaged(true, "again");                                // idempotent while engaged
  assert.equal(ctx.nodes.filter((n) => n.kind === "gain").flatMap((n) => n.gain.calls).filter((c) => c[1] === 2.6).length, 1);
  radio.setEngaged(false);
  const overdrive = ctx.nodes.find((n) => n.kind === "gain" && n.gain.calls.some((c) => c[1] === 2.6));
  assert.equal(overdrive.gain.value, 1);
  radio.acquire(); radio.acquire();
  assert.equal(radio.carrierCount, 2);
  radio.release(); radio.release();
  assert.equal(radio.carrierCount, 0);
});


test("init does not wait for the microphone prompt; a PTT pressed meanwhile starts capture once granted", async () => {
  const ctx = new FakeContext();
  let grant;
  const calls = [];
  const engine = createAudioEngine({
    createContext: () => ctx,
    createMic: () => ({
      init: () => new Promise((resolve) => { grant = resolve; }),
      setActive: (a) => calls.push(a), level: () => 0, dispose() {},
    }),
  });
  const sent = [];
  await engine.init({ sendBinary() {}, sendJson: (j) => sent.push(j.state), role: "radar", playback: true });
  engine.handleServerMsg(start("p1"));                       // playback works while the prompt is open
  engine.pttDown();
  assert.deepEqual(sent, ["down"]);
  assert.deepEqual(calls, []);
  grant();
  await engine.whenMicReady();
  assert.deepEqual(calls, [true], "capture starts as soon as permission is granted");
  engine.pttUp();
  assert.deepEqual(calls, [true, false]);
  assert.deepEqual(sent, ["down", "up"]);
});
