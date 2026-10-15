// Bounded real microphone capture. No synthesis and no monitor output.
class Project2ACapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = new Float32Array(2048);
    this.used = 0;
    this.total = 0;
    this.limit = Math.floor(sampleRate * 20);
    this.done = false;
    this.port.onmessage = ({ data }) => { if (data.type === 'finish') this.finish(); };
  }

  flush() {
    if (!this.used) return;
    const samples = this.buffer.slice(0, this.used);
    this.port.postMessage({ type: 'chunk', samples }, [samples.buffer]);
    this.used = 0;
  }

  finish() {
    if (this.done) return;
    this.done = true;
    this.flush();
    this.port.postMessage({ type: 'done' });
  }

  process(inputs, outputs) {
    for (const output of outputs) for (const channel of output) channel.fill(0);
    if (this.done) return false;
    const input = inputs[0]?.[0];
    if (!input) return true;
    for (let i = 0; i < input.length && this.total < this.limit; i++) {
      this.buffer[this.used++] = input[i];
      this.total++;
      if (this.used === this.buffer.length) this.flush();
    }
    if (this.total === this.limit) this.finish();
    return !this.done;
  }
}

registerProcessor('project2a-capture', Project2ACapture);
