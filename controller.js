import { messageInfo, validateRate } from './limits.js?v=2a-simple-1';
import { resampleToCodec } from './audio.js?v=2a-simple-1';

export class ClientController {
  constructor({ runtime, audio, update }) {
    Object.assign(this, { runtime, audio, update });
    this.generation = 0;
    this.state = 'idle';
    this.ready = false;
  }

  show(state, status, result) {
    this.state = state;
    this.update({ state, status, result, ready: this.ready });
  }

  async initialize() {
    const generation = ++this.generation;
    this.show('loading', 'Loading Python and NumPy… The first visit may take a moment.');
    try {
      await this.runtime.init();
      if (generation !== this.generation) return;
      this.ready = true;
      this.show('idle', 'Ready. Choose Send or Receive.');
    } catch (error) {
      if (generation !== this.generation) return;
      this.ready = false;
      this.show('error', `Could not load Python. ${error.message} Use Retry to load again.`);
    }
  }

  fail(error, generation) {
    if (generation !== this.generation) return;
    this.audio.cancel();
    this.ready = this.runtime.ready;
    this.show('error', error.message || 'Something went wrong. Please try again.');
  }

  async send(text, rate) {
    if (!this.ready || !['idle', 'error'].includes(this.state)) return;
    const generation = ++this.generation;
    try {
      const info = messageInfo(text, rate);
      this.show('preparing', 'Preparing the tone…');
      await this.audio.preparePlayback();
      if (generation !== this.generation) return;
      const samples = await this.runtime.encode(text, rate);
      if (generation !== this.generation) return;
      const playback = this.audio.play(samples);
      this.show('sending', `Sending through the speaker · ${info.seconds.toFixed(1)} seconds.`, '');
      await playback;
      if (generation === this.generation) this.show('idle', 'Tone finished. On the receiving device, press Stop to decode.');
    } catch (error) { this.fail(error, generation); }
  }

  async receive(rate) {
    if (!this.ready || !['idle', 'error'].includes(this.state)) return;
    const generation = ++this.generation;
    try {
      validateRate(rate);
      this.receiveRate = rate; // snapshot only the selected rate, never sender data
      this.show('permission', 'Opening the microphone… Allow access if your browser asks.', '');
      await this.audio.startCapture({
        onLimit: () => { if (generation === this.generation) void this.stop(); },
        onError: error => this.fail(error, generation),
      });
      if (generation === this.generation && this.state === 'permission') {
        this.show('receiving', 'Microphone is listening. Send from the other device, then press Stop. Ends after 20 seconds.');
      }
    } catch (error) { this.fail(error, generation); }
  }

  async stop() {
    if (this.state === 'receiving') {
      const generation = this.generation;
      const rate = this.receiveRate;
      this.show('decoding', 'Microphone stopped. Decoding the recording…');
      try {
        const capture = await this.audio.finishCapture();
        if (generation !== this.generation) return;
        const samples = resampleToCodec(capture.samples, capture.sampleRate);
        const decoded = await this.runtime.decode(samples, rate);
        if (generation === this.generation) this.show('idle', 'Recording decoded. Errors may remain.', decoded.text);
      } catch (error) { this.fail(error, generation); }
      return;
    }
    const previous = this.state;
    ++this.generation; // invalidate every late permission, init or codec continuation
    this.audio.cancel();
    if (['loading', 'preparing', 'decoding'].includes(previous)) {
      this.runtime.dispose();
      this.ready = false;
    }
    this.show('idle', this.ready ? 'Stopped. Ready to send or receive again.' : 'Stopped. Use Retry to load Python again.');
  }

  dispose() {
    ++this.generation;
    this.audio.cancel();
    this.runtime.dispose();
    this.ready = false;
    this.show('idle', 'Paused. Use Retry to load Python again.');
  }
}
