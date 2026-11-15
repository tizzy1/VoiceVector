// AudioWorklet processor source for microphone capture. It is shipped as a string and loaded from
// a Blob URL, so it works under Vite, the FastAPI static mount and the dev page without any
// bundler configuration. The processor batches 128-sample render quanta into ~21 ms blocks of
// mono float32 and posts them to the main thread, where they are resampled to 16 kHz PCM16.

export const CAPTURE_PROCESSOR_NAME = "voicevector-capture";
export const CAPTURE_BLOCK = 1024;

export const CAPTURE_WORKLET_SOURCE = `
class VoiceVectorCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(${CAPTURE_BLOCK});
    this.filled = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    let offset = 0;
    while (offset < channel.length) {
      const take = Math.min(this.buffer.length - this.filled, channel.length - offset);
      this.buffer.set(channel.subarray(offset, offset + take), this.filled);
      this.filled += take;
      offset += take;
      if (this.filled === this.buffer.length) {
        this.port.postMessage(this.buffer.slice(0));
        this.filled = 0;
      }
    }
    return true;
  }
}
registerProcessor("${CAPTURE_PROCESSOR_NAME}", VoiceVectorCapture);
`;
