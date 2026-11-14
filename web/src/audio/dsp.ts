// Pure DSP parameters for the radio chain and the heterodyne squeal (no Web Audio objects).

export const RADIO = {
  highpassHz: 300,
  lowpassHz: 3000,
  drive: 2.2, // waveshaper drive: mild tanh saturation
  hissLevel: 0.012, // faint noise floor under a transmission
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
