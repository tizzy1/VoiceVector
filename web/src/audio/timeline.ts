// Playback bookkeeping for one transmission, independent of Web Audio so it is unit-testable.
// Chunks are scheduled back to back; the "word clock" is how far into the transmission playback is.

export const JITTER_BUFFER_S = 0.06; // small cushion before the first chunk so it cannot underrun

export class StreamTimeline {
  /** Clock time (seconds) when the first sample plays; null until the first chunk. */
  startedAt: number | null = null;
  /** Clock time when everything scheduled so far finishes. */
  nextTime = 0;
  /** Total seconds of audio scheduled. */
  duration = 0;
  ended = false;

  /**
   * Reserve a slot for a chunk of ``seconds`` at clock time ``now``. Returns the time to start
   * it. If playback fell behind (underrun) the chunk starts immediately and the gap is accepted.
   */
  schedule(seconds: number, now: number): number {
    if (this.startedAt === null) {
      this.startedAt = now + JITTER_BUFFER_S;
      this.nextTime = this.startedAt;
    }
    const at = Math.max(this.nextTime, now);
    this.nextTime = at + seconds;
    this.duration += seconds;
    return at;
  }

  /** Seconds since this transmission's audio started playing (0 while still buffering). */
  clock(now: number): number | null {
    if (this.startedAt === null) return null;
    return Math.max(0, now - this.startedAt);
  }

  /** Seconds until all scheduled audio has played. */
  remaining(now: number): number {
    return Math.max(0, this.nextTime - now);
  }

  get finished(): boolean {
    return this.ended;
  }
}
