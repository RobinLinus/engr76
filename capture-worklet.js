class SampleCapture extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.samples = new Float32Array(options.processorOptions.frames);
    this.position = 0;
  }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (!channel) return true;
    const count = Math.min(channel.length, this.samples.length - this.position);
    this.samples.set(channel.subarray(0, count), this.position);
    this.position += count;
    if (this.position === this.samples.length) {
      this.port.postMessage(this.samples.buffer, [this.samples.buffer]);
      return false;
    }
    return true;
  }
}
registerProcessor('sample-capture', SampleCapture);
