// Microphone capture: getUserMedia -> AudioWorklet -> resample to 16 kHz -> PCM16 -> 100 ms frames.
// Frames are emitted only while a push-to-talk is active; the level meter runs continuously.
import { CAPTURE_RATE, FRAME_SAMPLES, Framer, Resampler, float32ToPcm16, int16ToBytes, rms, smoothLevel } from "./pcm.ts";
import { CAPTURE_PROCESSOR_NAME, CAPTURE_WORKLET_SOURCE } from "./worklets/capture.ts";

export interface MicCaptureLike {
  init(): Promise<void>;
  setActive(active: boolean): void;
  level(): number;
  dispose(): void;
}

/** The DOM-free part: blocks of mic samples in, 16 kHz PCM16 frames + a smoothed level out. */
export class CapturePipeline {
  private resampler: Resampler;
  private framer = new Framer(FRAME_SAMPLES);
  private active = false;
  private smoothed = 0;
  private readonly onFrame: (frame: ArrayBuffer) => void;
  private readonly inputRate: number;
  framesSent = 0;

  constructor(inputRate: number, onFrame: (frame: ArrayBuffer) => void) {
    this.inputRate = inputRate;
    this.onFrame = onFrame;
    this.resampler = new Resampler(inputRate, CAPTURE_RATE);
  }

  /** Feed one block of mono float32 microphone samples. */
  push(block: Float32Array): void {
    this.smoothed = smoothLevel(this.smoothed, Math.min(1, rms(block) * 4));
    if (!this.active) return;
    const pcm = float32ToPcm16(this.resampler.process(block));
    for (const frame of this.framer.push(pcm)) this.emit(frame);
  }

  setActive(active: boolean): void {
    if (active === this.active) return;
    if (active) {
      this.resampler = new Resampler(this.inputRate, CAPTURE_RATE);
      this.framer.reset();
    } else {
      const tail = this.framer.flush();   // never lose the last syllable
      if (tail) this.emit(tail);
    }
    this.active = active;
  }

  level(): number {
    return this.smoothed > 0.001 ? this.smoothed : 0;   // continuous, so the meter works before keying
  }

  private emit(frame: Int16Array): void {
    this.framesSent += 1;
    this.onFrame(int16ToBytes(frame));
  }
}

/** Real microphone. Constraints per spec: echo cancellation off, noise suppression on. */
export class MicCapture implements MicCaptureLike {
  private pipeline: CapturePipeline | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private readonly ctx: AudioContext;
  private readonly onFrame: (frame: ArrayBuffer) => void;

  constructor(ctx: AudioContext, onFrame: (frame: ArrayBuffer) => void) {
    this.ctx = ctx;
    this.onFrame = onFrame;
  }

  async init(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access is not available in this browser");
    if (!this.ctx.audioWorklet) throw new Error("AudioWorklet is not available in this browser");
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: false, channelCount: 1 },
    });
    const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET_SOURCE], { type: "text/javascript" }));
    try {
      await this.ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
    this.pipeline = new CapturePipeline(this.ctx.sampleRate, this.onFrame);
    const source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, CAPTURE_PROCESSOR_NAME, { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
    this.node.port.onmessage = (event: MessageEvent<Float32Array>) => this.pipeline?.push(event.data);
    // Worklets only run while connected towards the destination: route through a muted gain.
    const silent = this.ctx.createGain();
    silent.gain.value = 0;
    source.connect(this.node);
    this.node.connect(silent);
    silent.connect(this.ctx.destination);
  }

  setActive(active: boolean): void {
    this.pipeline?.setActive(active);
  }

  level(): number {
    return this.pipeline?.level() ?? 0;
  }

  dispose(): void {
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    this.node = null;
    this.stream = null;
  }
}
