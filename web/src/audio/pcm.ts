// Pure PCM helpers shared by capture (mic -> 16 kHz PCM16 frames) and playback (PCM16 -> float).
// No DOM, no Web Audio: unit-tested in Node. Erasable TypeScript only (Node strips the types).

export const CAPTURE_RATE = 16000;
export const FRAME_SAMPLES = 1600; // 100 ms @ 16 kHz, what the server expects per binary frame

export function base64ToBytes(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** Little-endian PCM16 bytes -> float32 [-1, 1). A dangling odd byte is ignored (use PcmAligner). */
export function pcm16ToFloat32(bytes: Uint8Array): Float32Array {
  const n = bytes.length >> 1;
  const out = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    let v = bytes[2 * i] | (bytes[2 * i + 1] << 8);
    if (v & 0x8000) v -= 0x10000;
    out[i] = v / 32768;
  }
  return out;
}

export function float32ToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    // one scale (32768) both ways so decode(encode(x)) is exact; clamp keeps +1.0 inside int16
    out[i] = Math.max(-32768, Math.min(32767, Math.round(input[i] * 32768)));
  }
  return out;
}

/** Int16 samples as little-endian bytes, independent of platform endianness. */
export function int16ToBytes(samples: Int16Array): ArrayBuffer {
  const buf = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buf);
  for (let i = 0; i < samples.length; i++) view.setInt16(i * 2, samples[i], true);
  return buf;
}

/**
 * Network chunks can end mid-sample. Keeps the odd trailing byte until the next chunk so
 * every decoded buffer holds whole 16-bit samples.
 */
export class PcmAligner {
  private carry: number | null = null;

  push(bytes: Uint8Array): Uint8Array {
    if (this.carry === null && bytes.length % 2 === 0) return bytes;
    let data = bytes;
    if (this.carry !== null) {
      data = new Uint8Array(bytes.length + 1);
      data[0] = this.carry;
      data.set(bytes, 1);
      this.carry = null;
    }
    if (data.length % 2 === 1) {
      this.carry = data[data.length - 1];
      data = data.subarray(0, data.length - 1);
    }
    return data;
  }
}

/**
 * Streaming area-average resampler (a box filter, so it also band-limits when downsampling
 * 44.1/48 kHz microphone audio to 16 kHz). State is carried across calls.
 */
export class Resampler {
  readonly ratio: number;
  private acc = 0;
  private filled = 0;

  constructor(inRate: number, outRate: number) {
    this.ratio = inRate / outRate;
  }

  process(input: Float32Array): Float32Array {
    const out: number[] = [];
    for (let i = 0; i < input.length; i++) {
      let remaining = 1;
      const s = input[i];
      while (remaining > 1e-12) {
        const take = Math.min(remaining, this.ratio - this.filled);
        this.acc += s * take;
        this.filled += take;
        remaining -= take;
        if (this.filled >= this.ratio - 1e-9) {
          out.push(this.acc / this.ratio);
          this.acc = 0;
          this.filled = 0;
        }
      }
    }
    return Float32Array.from(out);
  }
}

/** Collects samples into fixed-size frames (default 100 ms @ 16 kHz). */
export class Framer {
  private buf: Int16Array;
  private n = 0;
  readonly size: number;

  constructor(size: number = FRAME_SAMPLES) {
    this.size = size;
    this.buf = new Int16Array(size);
  }

  push(samples: Int16Array): Int16Array[] {
    const frames: Int16Array[] = [];
    let i = 0;
    while (i < samples.length) {
      const take = Math.min(this.size - this.n, samples.length - i);
      this.buf.set(samples.subarray(i, i + take), this.n);
      this.n += take;
      i += take;
      if (this.n === this.size) {
        frames.push(this.buf.slice());
        this.n = 0;
      }
    }
    return frames;
  }

  /** The partial frame, if any (sent on PTT up so the last syllable is not lost). */
  flush(): Int16Array | null {
    if (this.n === 0) return null;
    const out = this.buf.slice(0, this.n);
    this.n = 0;
    return out;
  }

  reset(): void {
    this.n = 0;
  }
}

export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/** Exponential smoothing with fast attack and slower release, for the VU meter. */
export function smoothLevel(previous: number, next: number, attack = 0.6, release = 0.15): number {
  const k = next > previous ? attack : release;
  return previous + (next - previous) * k;
}
