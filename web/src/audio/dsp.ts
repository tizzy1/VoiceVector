// Pure DSP parameters for the radio chain and the heterodyne squeal (no Web Audio objects).

// Fitted to real VHF ATC audio (UWB-ATCC test split, 1220 pilot transmissions, 14 Nov 2026): energy
// 300 Hz-1 kHz 67 %, 1-2 kHz 23 %, 2-3 kHz 5.5 %, crest factor ~10 dB. The previous 300/3000 Hz,
// drive 2.2 chain was too bright above 2 kHz (11 %), too boomy below 300 Hz (6 %) and over-squashed
// (crest 6.6 dB); this one lands within ~1 point per band (crest 8.2 dB: TTS audio is pre-levelled).
// Keep in sync with server/voice/voice_design.py RADIO_PROFILE (a test checks it).
export const RADIO = {
  highpassHz: 350,
  lowpassHz: 2600,
  presenceHz: 1400, // peaking lift, the mid "honk" of a VHF set
  presenceDb: 2,
  shelfHz: 2200, // high-shelf cut: real radios roll off early and gently
  shelfDb: -6,
  drive: 0.5, // waveshaper drive: light tanh saturation
  // Carrier noise floor, broadband like real VHF (UWB-ATCC noise: 0-4 kHz, roughly flat). hissLevel
  // is the RMS of the band-limited hiss: 0.017 puts it ~26 dB under speech, the noisier quarter of
  // real pilot transmissions (median 31.6 dB, p25 26.6 dB). Was a narrow 1.5 kHz band at ~-48 dB.
  hissLevel: 0.017,
  hissHighpassHz: 150,
  hissLowpassHz: 3500,
  squelchMs: 150, // band-passed noise burst when the carrier drops
  squelchLevel: 0.22,
  squelchBandHz: 1800,
};

export const HETERODYNE = {
  carrierHz: 1100,
  lfoHz: 7,
  variation: 0.1, // +-10 % per event
  oscLevel: 0.55,
  overdrive: 2.6, // pre-clip gain while engaged
  clipAt: 0.8,
  releaseMs: 350,
};

/** |H(f)|^2 of a Web Audio lowpass/highpass BiquadFilterNode (Audio EQ Cookbook, Q = 0 dB). */
export function biquadPower(kind: "lowpass" | "highpass", f0: number, f: number, rate: number): number {
  const w0 = (2 * Math.PI * f0) / rate, cos = Math.cos(w0), alpha = Math.sin(w0) / 2;
  const b = kind === "lowpass" ? [(1 - cos) / 2, 1 - cos, (1 - cos) / 2] : [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2];
  const a = [1 + alpha, -2 * cos, 1 - alpha];
  const w = (2 * Math.PI * f) / rate;
  const mag2 = (c: number[]) => {
    const re = c[0] + c[1] * Math.cos(w) + c[2] * Math.cos(2 * w);
    const im = -(c[1] * Math.sin(w) + c[2] * Math.sin(2 * w));
    return re * re + im * im;
  };
  return mag2(b) / mag2(a);
}

/** Gain for uniform white noise (+-1) through the hiss band so its RMS equals RADIO.hissLevel at any rate. */
export function hissGain(rate: number, level: number = RADIO.hissLevel): number {
  const n = 2048;
  let power = 0;
  for (let i = 0; i < n; i++) {
    const f = ((i + 0.5) / n) * (rate / 2);
    power += biquadPower("highpass", RADIO.hissHighpassHz, f, rate) * biquadPower("lowpass", RADIO.hissLowpassHz, f, rate);
  }
  const rms = Math.sqrt(1 / 3) * Math.sqrt(power / n);
  return level / rms;
}

/** tanh waveshaper curve: output = tanh(drive * x) / tanh(drive), unity at +-1. */
export function tanhCurve(samples: number = 1024, drive: number = RADIO.drive): Float32Array<ArrayBuffer> {
  const curve = new Float32Array(samples);
  const norm = Math.tanh(drive);
  for (let i = 0; i < samples; i++) {
    const x = (i / (samples - 1)) * 2 - 1;
    curve[i] = Math.tanh(drive * x) / norm;
  }
  return curve;
}

/** Hard clipper curve: identity inside +-clipAt, flat outside. */
export function hardClipCurve(samples: number = 1024, clipAt: number = HETERODYNE.clipAt): Float32Array<ArrayBuffer> {
  const curve = new Float32Array(samples);
  for (let i = 0; i < samples; i++) {
    const x = (i / (samples - 1)) * 2 - 1;
    curve[i] = Math.max(-clipAt, Math.min(clipAt, x));
  }
  return curve;
}

/** Small deterministic PRNG (mulberry32) so noise and per-event variation are reproducible. */
export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** String -> 32-bit seed (FNV-1a). */
export function hashSeed(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/** Carrier and AM rate for one overlap event: ~1.1 kHz modulated at ~7 Hz, each varied by +-10 %. */
export function heterodyneParams(seed: string | number): { carrierHz: number; lfoHz: number } {
  const rand = mulberry32(typeof seed === "number" ? seed : hashSeed(seed));
  const vary = () => 1 + (rand() * 2 - 1) * HETERODYNE.variation;
  return { carrierHz: HETERODYNE.carrierHz * vary(), lfoHz: HETERODYNE.lfoHz * vary() };
}

/** White noise samples in [-1, 1], deterministic for a seed. */
export function noiseSamples(length: number, seed: number): Float32Array<ArrayBuffer> {
  const rand = mulberry32(seed);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) out[i] = rand() * 2 - 1;
  return out;
}
