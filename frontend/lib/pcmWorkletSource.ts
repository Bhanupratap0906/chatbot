/**
 * AudioWorklet processor source, injected via Blob URL.
 *
 * Takes caller audio at the AudioContext's rate, downsamples it to the
 * target rate (box-filter average, so no aliasing garbage), converts to
 * PCM16 and posts it in 20ms frames for /ws/voice.
 */
export const PCM_WORKLET_SOURCE = `
  class PCMProcessor extends AudioWorkletProcessor {
    constructor(options) {
      super();

      const opts = options.processorOptions || {};
      const targetRate = opts.targetRate || sampleRate;

      // Input samples consumed per output sample (3 for 48k -> 16k).
      this.step = sampleRate / targetRate;
      this.phase = 0;
      this.sum = 0;
      this.count = 0;

      // 20ms of audio at the target rate.
      this.frame = new Int16Array(Math.round(targetRate * 0.02));
      this.filled = 0;
    }

    process(inputs) {
      const channel = inputs[0] && inputs[0][0];
      if (!channel) return true;

      for (let i = 0; i < channel.length; i++) {
        this.sum += channel[i];
        this.count++;
        this.phase += 1;

        if (this.phase >= this.step) {
          this.phase -= this.step;

          const avg = this.sum / this.count;
          this.sum = 0;
          this.count = 0;

          const sample = Math.max(-1, Math.min(1, avg));
          this.frame[this.filled++] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;

          if (this.filled === this.frame.length) {
            const out = this.frame.slice().buffer;
            this.port.postMessage(out, [out]);
            this.filled = 0;
          }
        }
      }

      return true;
    }
  }

  registerProcessor("pcm-processor", PCMProcessor);
`;
