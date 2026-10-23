/**
 * AudioWorklet processor source, injected via Blob URL when VoiceAgent builds its microphone pipeline.
 *
 * Takes caller audio at the AudioContext's rate, downsamples it to the
 * target rate (box-filter average, so no aliasing garbage), converts to
 * PCM16 and posts it in 20ms frames for the backend /ws/voice WebSocket.
 */
export const PCM_WORKLET_SOURCE = `
  class PCMProcessor extends AudioWorkletProcessor {
    constructor(options) {
      // Configure the input-to-output sample-rate conversion when the worklet is created.
      super();

      const opts = options.processorOptions || {};
      const targetRate = opts.targetRate || sampleRate;
      this.vadThreshold = opts.vadThreshold || 0.006;
      this.trailingFrames = Math.ceil((opts.trailingSilenceMs || 1200) / 20);
      this.preRollLimit = Math.ceil((opts.preRollMs || 200) / 20);
      this.preRoll = [];
      this.speechActive = false;
      this.silenceFrames = 0;

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
      // Convert each browser audio block to PCM16 and post complete 20ms frames to VoiceAgent.
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
            const completed = this.frame;
            let squareSum = 0;
            for (let j = 0; j < completed.length; j++) {
              const normalized = completed[j] / 32768;
              squareSum += normalized * normalized;
            }
            const rms = Math.sqrt(squareSum / completed.length);
            const out = completed.slice().buffer;

            if (rms >= this.vadThreshold) {
              if (!this.speechActive) {
                for (const previous of this.preRoll) {
                  this.port.postMessage(previous, [previous]);
                }
                this.preRoll = [];
              }
              this.speechActive = true;
              this.silenceFrames = 0;
              this.port.postMessage(out, [out]);
            } else if (this.speechActive) {
              // Send enough trailing silence for Deepgram endpointing, then
              // stop forwarding silence until speech resumes.
              this.port.postMessage(out, [out]);
              if (++this.silenceFrames >= this.trailingFrames) {
                this.speechActive = false;
                this.silenceFrames = 0;
              }
            } else {
              this.preRoll.push(out);
              if (this.preRoll.length > this.preRollLimit) this.preRoll.shift();
            }
            this.filled = 0;
          }
        }
      }

      return true;
    }
  }

  registerProcessor("pcm-processor", PCMProcessor);
`;
