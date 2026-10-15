import { SAMPLE_RATE, MAX_CAPTURE_SECONDS, MAX_SEND_SECONDS } from './limits.js';

const stopped = () => new DOMException('Stopped.', 'AbortError');
const releaseSafely = action => { try { action(); } catch { /* Continue releasing other resources. */ } };

function validSampleRate(rate) {
  if (!Number.isInteger(rate) || rate < 8000 || rate > 192000) throw new Error('Unsupported microphone sample rate.');
}

export function resampleToCodec(samples, sourceRate) {
  validSampleRate(sourceRate);
  if (!(samples instanceof Float32Array) || !samples.length || samples.length > sourceRate * MAX_CAPTURE_SECONDS) {
    throw new Error('Recording must contain between 0 and 20 seconds of mono audio.');
  }
  if (!samples.every(Number.isFinite)) throw new Error('The microphone returned invalid audio samples.');
  if (sourceRate === SAMPLE_RATE) return samples.slice();
  const count = Math.round(samples.length * SAMPLE_RATE / sourceRate);
  const output = new Float32Array(count);
  // Linear interpolation preserves time/frequency at the 2 kHz carrier. This is
  // intentionally a small converter, not a general-purpose antialiasing filter.
  for (let n = 0; n < count; n++) {
    const position = n * sourceRate / SAMPLE_RATE;
    const left = Math.min(Math.floor(position), samples.length - 1);
    const right = Math.min(left + 1, samples.length - 1);
    output[n] = samples[left] + (samples[right] - samples[left]) * (position - left);
  }
  return output;
}

export class IndependentAudio {
  constructor({
    Context = globalThis.AudioContext || globalThis.webkitAudioContext,
    WorkletNode = globalThis.AudioWorkletNode,
    mediaDevices = globalThis.navigator?.mediaDevices,
    secure = globalThis.isSecureContext,
    // Window timer functions are receiver-sensitive in browsers. Never store an
    // unbound host function and later invoke it with the adapter as its receiver.
    setTimer = (...args) => globalThis.setTimeout(...args),
    clearTimer = (...args) => globalThis.clearTimeout(...args),
  } = {}) {
    Object.assign(this, { Context, WorkletNode, mediaDevices, secure, setTimer, clearTimer });
    this.session = null;
  }

  _create(kind) {
    if (this.session) throw new Error('Stop the current audio operation first.');
    if (!this.Context) throw new Error('This browser does not support Web Audio.');
    const context = new this.Context(); // Use the hardware's actual rate.
    const session = { kind, context, chunks: [], count: 0, nodes: [], closing: false };
    this.session = session;
    return session;
  }

  _check(session) {
    if (this.session !== session || session.closing) throw stopped();
  }

  _haltInput(session) {
    session.inputStopped = true;
    session.stream?.getTracks().forEach(track => {
      track.onended = null;
      releaseSafely(() => track.stop());
    });
    releaseSafely(() => session.input?.disconnect());
  }

  _release(session) {
    if (session.closing) return;
    session.closing = true;
    releaseSafely(() => this.clearTimer(session.timer));
    releaseSafely(() => this.clearTimer(session.flushTimer));
    this._haltInput(session);
    if (session.source) {
      session.source.onended = null;
      try { session.source.stop(); } catch { /* already ended */ }
    }
    for (const node of session.nodes) {
      if (node.port) {
        node.port.onmessage = null;
        releaseSafely(() => node.port.close());
      }
      node.onprocessorerror = null;
      try { node.disconnect(); } catch { /* already disconnected */ }
    }
    session.context.onstatechange = null;
    if (session.context.state !== 'closed') releaseSafely(() => { void session.context.close().catch(() => {}); });
    session.chunks = [];
    if (this.session === session) this.session = null;
  }

  _fail(session, error) {
    if (session.closing) return;
    this._release(session);
    session.playReject?.(error);
    session.flushReject?.(error);
    session.onError?.(error);
  }

  _watchContext(session) {
    session.context.onstatechange = () => {
      if (!session.closing && session.context.state !== 'running') {
        this._fail(session, new Error('Audio was interrupted. Keep this page open and try again.'));
      }
    };
  }

  async preparePlayback() {
    const session = this._create('send');
    try {
      await session.context.resume(); // invoked synchronously from the Send gesture
      this._check(session);
      if (session.context.state !== 'running') throw new Error('Audio could not start. Tap Send again.');
      return session;
    } catch (error) { this._release(session); throw error; }
  }

  play(samples) {
    const session = this.session;
    if (!session || session.kind !== 'send') return Promise.reject(stopped());
    return new Promise((resolve, reject) => {
      session.playReject = reject;
      try {
        this._check(session);
        if (!(samples instanceof Float32Array) || !samples.length || samples.length > SAMPLE_RATE * MAX_SEND_SECONDS ||
            !samples.every(value => Number.isFinite(value) && Math.abs(value) <= 1)) {
          throw new Error('Encoder returned invalid or overlong audio.');
        }
        const buffer = session.context.createBuffer(1, samples.length, SAMPLE_RATE);
        buffer.copyToChannel(samples, 0);
        session.source = session.context.createBufferSource();
        session.nodes.push(session.source);
        session.source.buffer = buffer;
        session.source.connect(session.context.destination);
        session.source.onended = () => { this._release(session); resolve(); };
        this._watchContext(session);
        session.source.start(); // Browser resamples the 44.1 kHz AudioBuffer for output.
        session.timer = this.setTimer(() => this._fail(session, new Error('Playback was interrupted. Try again.')), MAX_SEND_SECONDS * 1000 + 500);
      } catch (error) { this._fail(session, error); }
    });
  }

  async startCapture({ onLimit, onError }) {
    if (!this.secure) throw new Error('Microphone access requires HTTPS or localhost. Plain LAN HTTP does not work on a phone.');
    if (!this.mediaDevices?.getUserMedia || !this.WorkletNode) throw new Error('This browser does not support microphone AudioWorklet capture.');
    const session = this._create('receive');
    session.onError = onError;
    try {
      await session.context.resume(); // called within the Receive gesture
      this._check(session);
      if (session.context.state !== 'running') throw new Error('Audio could not start. Tap Receive again.');
      validSampleRate(session.context.sampleRate);
      if (!session.context.audioWorklet) throw new Error('AudioWorklet is unavailable. Use a current browser over HTTPS.');
      await session.context.audioWorklet.addModule(new URL('./capture-worklet.js', import.meta.url));
      this._check(session);
      const stream = await this.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false },
        video: false,
      });
      // Permission prompts cannot be aborted. Dispose a late grant immediately.
      if (this.session !== session || session.closing) {
        stream.getTracks().forEach(track => releaseSafely(() => track.stop()));
        throw stopped();
      }
      session.stream = stream;
      if (!stream.getAudioTracks().some(track => track.readyState === 'live')) throw new Error('No live microphone track was supplied.');
      for (const track of stream.getTracks()) track.onended = () => {
        if (!session.inputStopped) this._fail(session, new Error('The microphone disconnected. Please try again.'));
      };
      session.input = session.context.createMediaStreamSource(stream);
      session.nodes.push(session.input);
      session.worklet = new this.WorkletNode(session.context, 'project2a-capture', {
        numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
        channelCount: 1, channelCountMode: 'explicit',
      });
      session.nodes.push(session.worklet);
      session.worklet.onprocessorerror = () => this._fail(session, new Error('Microphone processing failed. Please try again.'));
      session.worklet.port.onmessage = ({ data }) => {
        if (session.closing) return;
        if (data.type === 'chunk') {
          const samples = data.samples;
          if (!(samples instanceof Float32Array) || !samples.length || !samples.every(Number.isFinite) ||
              session.count + samples.length > session.context.sampleRate * MAX_CAPTURE_SECONDS) {
            this._fail(session, new Error('The microphone returned invalid or overlong audio.'));
            return;
          }
          session.chunks.push(samples);
          session.count += samples.length;
        } else if (data.type === 'done') {
          session.workletDone = true;
          this._haltInput(session);
          if (session.flushResolve) session.flushResolve();
          else onLimit();
        }
      };
      session.input.connect(session.worklet);
      // The worklet outputs zeros; the live microphone is never monitored aloud.
      session.worklet.connect(session.context.destination);
      this._watchContext(session);
      session.timer = this.setTimer(onLimit, MAX_CAPTURE_SECONDS * 1000);
    } catch (error) {
      this._release(session);
      throw error;
    }
  }

  async finishCapture() {
    const session = this.session;
    if (!session || session.kind !== 'receive' || !session.worklet) throw stopped();
    this.clearTimer(session.timer);
    this._haltInput(session); // Stop the device NOW; flush only buffered worklet samples.
    try {
      if (!session.workletDone) {
        await new Promise((resolve, reject) => {
          session.flushResolve = resolve;
          session.flushReject = reject;
          session.flushTimer = this.setTimer(() => reject(new Error('Microphone flush timed out. Please try again.')), 1000);
          session.worklet.port.postMessage({ type: 'finish' });
        });
      }
      this._check(session);
      const samples = new Float32Array(session.count);
      let offset = 0;
      for (const chunk of session.chunks) { samples.set(chunk, offset); offset += chunk.length; }
      return { samples, sampleRate: session.context.sampleRate };
    } finally { this._release(session); }
  }

  cancel() {
    const session = this.session;
    if (!session) return;
    this._release(session);
    session.playReject?.(stopped());
    session.flushReject?.(stopped());
  }
}
