// Run: node --test web/src/audio/tests   (Node >= 22.18 strips the TypeScript types natively)
import assert from "node:assert/strict";
import test from "node:test";
import { CapturePipeline } from "../capture.ts";
import { HETERODYNE, RADIO, hardClipCurve, hashSeed, heterodyneParams, hissGain, mulberry32, noiseSamples, tanhCurve } from "../dsp.ts";
import { bytesToBase64, pcmToBase64Chunks, synthSpeechLike } from "../fixtures.ts";
import { FRAME_SAMPLES, Framer, PcmAligner, Resampler, base64ToBytes, float32ToPcm16, int16ToBytes, pcm16ToFloat32, rms, smoothLevel } from "../pcm.ts";
import { JITTER_BUFFER_S, StreamTimeline } from "../timeline.ts";

const sine = (hz, rate, seconds, amp = 0.5) =>
  Float32Array.from({ length: Math.floor(rate * seconds) }, (_, i) => amp * Math.sin((2 * Math.PI * hz * i) / rate));

test("PCM16 little-endian decoding and encoding round-trip", () => {
  const bytes = new Uint8Array([0x00, 0x00, 0xff, 0x7f, 0x00, 0x80, 0x34, 0x12]);
  const f = pcm16ToFloat32(bytes);
  assert.deepEqual([...f], [0, 32767 / 32768, -1, 0x1234 / 32768]);
  const back = float32ToPcm16(f);
  assert.deepEqual([...back], [0, 32767, -32768, 0x1234]);
  assert.deepEqual([...new Uint8Array(int16ToBytes(back))], [...bytes]);
  assert.deepEqual([...base64ToBytes(bytesToBase64(bytes))], [...bytes]);
  assert.deepEqual([...float32ToPcm16(Float32Array.from([2, -2]))], [32767, -32768]);   // clipped, not wrapped
});

test("odd network chunk boundaries never split a 16-bit sample", () => {
  const all = Uint8Array.from({ length: 101 }, (_, i) => i);
  const a = new PcmAligner();
  const parts = [all.subarray(0, 7), all.subarray(7, 8), all.subarray(8, 51), all.subarray(51, 101)].map((p) => a.push(p));
  assert.ok(parts.every((p) => p.length % 2 === 0));
  const joined = Uint8Array.from(parts.flatMap((p) => [...p]));
  assert.deepEqual([...joined], [...all.subarray(0, 100)]);     // the final odd byte waits for its partner
});

test("resampler 48k -> 16k keeps a tone's level, length and frequency", () => {
  const input = sine(440, 48000, 1);
  const out = new Resampler(48000, 16000).process(input);
  assert.equal(out.length, 16000);
  assert.ok(Math.abs(rms(out) - 0.5 / Math.SQRT2) < 0.01, `rms ${rms(out)}`);
  let crossings = 0;
  for (let i = 1; i < out.length; i++) if (out[i - 1] < 0 && out[i] >= 0) crossings++;
  assert.ok(Math.abs(crossings - 440) <= 2, `crossings ${crossings}`);
});

test("resampler is stream-invariant: chunking does not change the output", () => {
  const input = sine(300, 44100, 0.5);
  const whole = new Resampler(44100, 16000).process(input);
  const r = new Resampler(44100, 16000);
  const pieces = [];
  for (let i = 0; i < input.length; i += 1000) pieces.push(...r.process(input.subarray(i, i + 1000)));
  assert.equal(pieces.length, whole.length);
  assert.ok(whole.every((v, i) => Math.abs(v - pieces[i]) < 1e-6));
});

test("resampler is a box filter: partial cut above the new Nyquist, a full null at the input/3 alias", () => {
  const full = 0.5 / Math.SQRT2;
  assert.ok(rms(new Resampler(48000, 16000).process(sine(11000, 48000, 0.5))) < 0.5 * full);
  assert.ok(rms(new Resampler(48000, 16000).process(sine(16000, 48000, 0.5))) < 0.02 * full);
  assert.ok(Math.abs(rms(new Resampler(48000, 16000).process(sine(1000, 48000, 0.5))) - full) < 0.02 * full + 0.005);
});

test("framer emits exact 100 ms frames and flushes the tail", () => {
  const f = new Framer();
  assert.equal(f.size, FRAME_SAMPLES);
  const frames = f.push(new Int16Array(4000).fill(7));
  assert.deepEqual(frames.map((x) => x.length), [1600, 1600]);
  const tail = f.flush();
  assert.equal(tail.length, 800);
  assert.equal(f.flush(), null);
  assert.equal(f.push(new Int16Array(1600)).length, 1);
});

test("level meter: rms and smoothing (fast attack, slow release)", () => {
  assert.equal(rms(new Float32Array(0)), 0);
  assert.ok(Math.abs(rms(Float32Array.from([1, -1, 1, -1])) - 1) < 1e-9);
  assert.ok(smoothLevel(0, 1) > 0.5 && smoothLevel(1, 0) > 0.8);
});

test("capture pipeline: frames only while keyed, tail flushed on release", () => {
  const frames = [];
  const cap = new CapturePipeline(48000, (b) => frames.push(b));
  const block = sine(200, 48000, 0.0213);
  for (let i = 0; i < 20; i++) cap.push(block);
  assert.equal(frames.length, 0, "nothing is sent while the key is up");
  assert.ok(cap.level() > 0.05, "but the VU meter still moves");
  cap.setActive(true);
  const seconds = 1.0;
  const blocks = Math.round(seconds / 0.0213);
  for (let i = 0; i < blocks; i++) cap.push(block);
  const beforeRelease = frames.length;
  assert.ok(beforeRelease >= 9 && beforeRelease <= 10, `frames ${beforeRelease}`);
  assert.ok(frames.slice(0, beforeRelease).every((b) => b.byteLength === 3200));
  cap.setActive(false);
  assert.equal(frames.length, beforeRelease + 1, "the partial last frame is flushed");
  assert.ok(frames.at(-1).byteLength % 2 === 0 && frames.at(-1).byteLength <= 3200);
  cap.setActive(true);                                      // a new transmission starts clean
  cap.push(block);
  cap.setActive(false);
  assert.ok(frames.length >= beforeRelease + 2);
});

test("radio waveshapers: tanh is odd, monotone and unity at the ends; clip is flat outside the limit", () => {
  const t = tanhCurve(257);
  assert.ok(Math.abs(t[0] + 1) < 1e-6 && Math.abs(t[256] - 1) < 1e-6);
  assert.ok(Math.abs(t[128]) < 1e-6);
  for (let i = 1; i < t.length; i++) assert.ok(t[i] >= t[i - 1]);
  assert.ok(t[192] > 0.5 && t[192] < 0.6, "light saturation, as fitted to real VHF audio (drive 0.5)");
  const c = hardClipCurve(201, 0.8);
  assert.ok(Math.abs(c[0] + 0.8) < 1e-6);
  assert.ok(Math.abs(c[200] - 0.8) < 1e-6);
  assert.ok(Math.abs(c[110] - 0.1) < 1e-6);
});

test("heterodyne: ~1.1 kHz / ~7 Hz, varied within +-10 %, deterministic per event", () => {
  const seen = new Set();
  for (let i = 0; i < 200; i++) {
    const p = heterodyneParams(`squeal-${i}`);
    assert.ok(Math.abs(p.carrierHz / HETERODYNE.carrierHz - 1) <= 0.1 + 1e-9);
    assert.ok(Math.abs(p.lfoHz / HETERODYNE.lfoHz - 1) <= 0.1 + 1e-9);
    seen.add(p.carrierHz.toFixed(1));
  }
  assert.ok(seen.size > 100, "each event sounds different");
  assert.deepEqual(heterodyneParams("a"), heterodyneParams("a"));
  assert.notDeepEqual(heterodyneParams("a"), heterodyneParams("b"));
});

test("seeded noise and hashing are reproducible", () => {
  assert.deepEqual([...noiseSamples(8, 3)], [...noiseSamples(8, 3)]);
  assert.notDeepEqual([...noiseSamples(8, 3)], [...noiseSamples(8, 4)]);
  assert.ok(noiseSamples(1000, 1).every((v) => v >= -1 && v <= 1));
  assert.equal(mulberry32(5)(), mulberry32(5)());
  assert.notEqual(hashSeed("a"), hashSeed("b"));
});

test("stream timeline: gapless scheduling, jitter cushion, underrun recovery, word clock", () => {
  const t = new StreamTimeline();
  assert.equal(t.clock(10), null);
  const a = t.schedule(0.5, 10);
  assert.ok(Math.abs(a - (10 + JITTER_BUFFER_S)) < 1e-9);
  const b = t.schedule(0.25, 10.01);
  assert.ok(Math.abs(b - (a + 0.5)) < 1e-9, "second chunk starts exactly when the first ends");
  assert.ok(Math.abs(t.duration - 0.75) < 1e-9);
  assert.equal(t.clock(10), 0);                              // still inside the jitter cushion
  assert.ok(Math.abs(t.clock(10.56) - 0.5) < 1e-9);
  assert.ok(Math.abs(t.remaining(10.06) - 0.75) < 1e-9);
  const late = t.schedule(0.1, 12);                          // underrun: the network stalled
  assert.equal(late, 12);
  assert.equal(t.remaining(13), 0);
});

test("fixtures: synthetic voice is audible, bounded and deterministic; chunks reassemble", () => {
  const pcm = synthSpeechLike(1, 22050, 3);
  assert.equal(pcm.length, 22050);
  assert.ok(rms(pcm16ToFloat32(new Uint8Array(int16ToBytes(pcm)))) > 0.03);
  assert.deepEqual([...synthSpeechLike(0.1, 22050, 3)], [...synthSpeechLike(0.1, 22050, 3)]);
  const chunks = pcmToBase64Chunks(pcm, 4096);
  const rebuilt = chunks.flatMap((c) => [...base64ToBytes(c)]);
  assert.deepEqual(rebuilt, [...new Uint8Array(int16ToBytes(pcm))]);
});


test("hiss through the VHF band comes out at RADIO.hissLevel RMS at any sample rate", () => {
  // Simulate the browser's highpass -> lowpass biquads (Q = 0 dB) on the same white noise.
  const biquad = (x, kind, f0, rate) => {
    const w0 = (2 * Math.PI * f0) / rate, cos = Math.cos(w0), alpha = Math.sin(w0) / 2;
    const b = kind === "lowpass" ? [(1 - cos) / 2, 1 - cos, (1 - cos) / 2] : [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2];
    const a = [1 + alpha, -2 * cos, 1 - alpha];
    const y = new Float64Array(x.length);
    for (let n = 0; n < x.length; n++) {
      y[n] = (b[0] * x[n] + b[1] * (x[n - 1] ?? 0) + b[2] * (x[n - 2] ?? 0) - a[1] * (y[n - 1] ?? 0) - a[2] * (y[n - 2] ?? 0)) / a[0];
    }
    return y;
  };
  for (const rate of [22050, 44100, 48000]) {
    const white = noiseSamples(rate * 4, 7);
    const band = biquad(biquad(white, "highpass", RADIO.hissHighpassHz, rate), "lowpass", RADIO.hissLowpassHz, rate);
    const level = Math.sqrt(band.reduce((s, v) => s + v * v, 0) / band.length) * hissGain(rate);
    assert.ok(Math.abs(level / RADIO.hissLevel - 1) < 0.05, `${rate} Hz: ${level}`);
  }
  assert.ok(RADIO.hissLevel >= 0.015 && RADIO.hissLevel <= 0.02, "about 26 dB under speech");
});
