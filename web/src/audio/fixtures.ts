// Synthetic "radio voice" for the dev page and tests: a gliding glottal pitch through formant-ish
// harmonics with a syllable-rate amplitude envelope. Deterministic, no assets, no network.
import { mulberry32 } from "./dsp.ts";

export function synthSpeechLike(seconds: number, sampleRate = 22050, seed = 1, pitchHz = 120): Int16Array {
  const rand = mulberry32(seed);
  const n = Math.floor(seconds * sampleRate);
  const out = new Int16Array(n);
  let phase = 0;
  const syllableHz = 3.5 + rand() * 1.5;
  for (let i = 0; i < n; i++) {
    const t = i / sampleRate;
    const f0 = pitchHz * (1 + 0.12 * Math.sin(2 * Math.PI * 0.7 * t));
    phase += (2 * Math.PI * f0) / sampleRate;
    let v = 0;
    for (let h = 1; h <= 12; h++) {
      const f = h * f0;
      const formant = Math.exp(-Math.pow((f - 700) / 350, 2)) + 0.6 * Math.exp(-Math.pow((f - 1700) / 500, 2));
      v += (formant / h) * Math.sin(h * phase);
    }
    const envelope = Math.max(0, Math.sin(Math.PI * syllableHz * t)) ** 0.7;
    const fadeIn = Math.min(1, t / 0.03);
    const fadeOut = Math.min(1, (seconds - t) / 0.05);
    out[i] = Math.round(Math.max(-1, Math.min(1, v * 0.35 * envelope * fadeIn * fadeOut + (rand() - 0.5) * 0.01)) * 32767);
  }
  return out;
}

export function bytesToBase64(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

/** PCM16 samples -> base64 chunks of ``chunkBytes`` (little-endian), as the server sends them. */
export function pcmToBase64Chunks(samples: Int16Array, chunkBytes = 4096): string[] {
  const bytes = new Uint8Array(samples.length * 2);
  for (let i = 0; i < samples.length; i++) {
    bytes[2 * i] = samples[i] & 0xff;
    bytes[2 * i + 1] = (samples[i] >> 8) & 0xff;
  }
  const chunks: string[] = [];
  for (let i = 0; i < bytes.length; i += chunkBytes) chunks.push(bytesToBase64(bytes.subarray(i, i + chunkBytes)));
  return chunks;
}
